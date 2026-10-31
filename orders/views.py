from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import customer_required, refuse_non_customer
from catalog.models import Product
from core import antispam
from core.models import SiteSettings

from . import services
from .cart import Cart
from .forms import AddressForm, CheckoutForm, CustomRequestForm, MessageForm
from .models import CustomRequest, Order


# --- Cart ----------------------------------------------------------------------
def cart_detail(request):
    cart = Cart(request)
    lines = cart.lines()
    return render(request, "orders/cart.html", {"lines": lines, "cart_total": Cart.total(lines)})


@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(Product.objects.visible(), pk=product_id, is_available=True)
    sizes = {str(option.pk): option for option in product.size_options}
    size = sizes.get(request.POST.get("size", ""))
    if sizes and size is None:
        messages.error(request, _("Please choose a size."))
        return redirect(product)
    try:
        quantity = max(1, int(request.POST.get("quantity", 1)))
    except ValueError:
        quantity = 1
    Cart(request).add(Cart.key(product, size), quantity)
    name = f"{product.local_name} – {size.size}" if size else product.local_name
    messages.success(request, _("“%(name)s” was added to your cart.") % {"name": name})
    next_url = request.POST.get("next")
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        next_url = "orders:cart"
    return redirect(next_url)


@require_POST
def cart_update(request):
    cart = Cart(request)
    for key, value in request.POST.items():
        if key.startswith("qty_"):
            try:
                cart.set(key[4:], int(value or 0))
            except ValueError:
                continue
    return redirect("orders:cart")


# What one customer can send (count, seconds), so a misused account cannot flood the shop.
LIMITS = {"order": (10, 86400), "custom_request": (10, 86400), "message": (30, 3600)}


def _limit_reached(request, scope):
    if antispam.attempts(scope, request.user.pk) >= LIMITS[scope][0]:
        messages.error(request, _("You have reached the limit for now. Please contact us if you need more."))
        return True
    return False


def _count(request, scope):
    antispam.record_attempt(scope, request.user.pk, LIMITS[scope][1])


@customer_required
def checkout(request):
    cart = Cart(request)
    lines = cart.lines()
    if not lines:
        messages.info(request, _("Your cart is empty."))
        return redirect("catalog:products")
    form = CheckoutForm(request.POST or None, user=request.user)
    if request.method == "POST" and _limit_reached(request, "order"):
        return redirect("orders:cart")
    if request.method == "POST" and form.is_valid():
        form.save_to_profile()
        order = services.create_order(request.user, lines, form.address_data(), form.cleaned_data["notes"])
        _count(request, "order")
        cart.clear()
        return redirect("orders:order_placed", number=order.number)
    total = Cart.total(lines)
    shipping = services.default_shipping(SiteSettings.load(), total, request.user.is_business)
    return render(request, "orders/checkout.html", {
        "form": form, "lines": lines, "cart_total": total,
        "estimated_shipping": shipping, "estimated_total": total + shipping,
    })


@login_required
def order_placed(request, number):
    order = get_object_or_404(Order, number=number, user=request.user)
    return render(request, "orders/order_placed.html", {"order": order})


# --- My orders -----------------------------------------------------------------
@login_required
def order_list(request):
    orders = request.user.orders.prefetch_related("items")
    return render(request, "orders/order_list.html", {"orders": orders})


@login_required
def order_detail(request, number):
    order = get_object_or_404(
        Order.objects.prefetch_related("items", "events", "messages"), number=number, user=request.user
    )
    form = MessageForm(request.POST or None)
    if request.method == "POST" and _limit_reached(request, "message"):
        return redirect(order)
    if request.method == "POST" and form.is_valid():
        services.post_customer_message(order, request.user, form.cleaned_data["body"])
        _count(request, "message")
        messages.success(request, _("Message sent. We will reply as soon as possible."))
        return redirect(order)
    services.mark_read_by_customer(order)
    reached = order.FLOW.index(order.status) if order.status in order.FLOW else -1
    return render(request, "orders/order_detail.html", {
        "order": order, "form": form, "on_the_way": order.status in Order.ON_THE_WAY,
        "steps": [(Order.Status(status).label, i <= reached) for i, status in enumerate(order.FLOW)],
    })


@login_required
@require_POST
def order_cancel(request, number):
    order = get_object_or_404(Order, number=number, user=request.user)
    if order.can_cancel:
        services.cancel_by_customer(order)
        messages.success(request, _("Your order request was cancelled."))
    return redirect(order)


# --- Custom requests -------------------------------------------------------------
def custom_request_create(request):
    if not request.user.is_authenticated:
        return render(request, "orders/custom_request_intro.html")
    refused = refuse_non_customer(request)
    if refused:
        return refused
    form = CustomRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and _limit_reached(request, "custom_request"):
        return redirect("orders:request_list")
    if request.method == "POST" and form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items() if k != "images"}
        req = services.create_custom_request(request.user, data, form.cleaned_data["images"])
        _count(request, "custom_request")
        messages.success(request, _("Thank you! We received your request and will contact you soon."))
        return redirect(req)
    return render(request, "orders/custom_request_form.html", {"form": form})


@login_required
def request_list(request):
    return render(request, "orders/request_list.html", {"requests": request.user.custom_requests.all()})


@login_required
def request_detail(request, number):
    req = get_object_or_404(
        CustomRequest.objects.prefetch_related("images", "messages"), number=number, user=request.user
    )
    form = MessageForm(request.POST or None)
    if request.method == "POST" and _limit_reached(request, "message"):
        return redirect(req)
    if request.method == "POST" and form.is_valid():
        services.post_customer_message(req, request.user, form.cleaned_data["body"])
        _count(request, "message")
        messages.success(request, _("Message sent. We will reply as soon as possible."))
        return redirect(req)
    services.mark_read_by_customer(req)
    return render(request, "orders/request_detail.html", {"req": req, "form": form})


@customer_required
def quote_accept(request, number):
    req = get_object_or_404(CustomRequest, number=number, user=request.user)
    if not req.can_respond_to_quote:
        return redirect(req)
    form = AddressForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save_to_profile()
        order = services.accept_quote(req, form.address_data())
        messages.success(request, _("Thank you! We sent you an email with the payment details."))
        return redirect(order)
    return render(request, "orders/quote_accept.html", {"req": req, "form": form})


@login_required
@require_POST
def quote_decline(request, number):
    req = get_object_or_404(CustomRequest, number=number, user=request.user)
    if req.can_respond_to_quote:
        services.decline_quote(req)
        messages.info(request, _("You declined the offer. You can still send us a message if you change your mind."))
    return redirect(req)
