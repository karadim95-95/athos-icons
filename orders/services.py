"""The order workflow in one place, used by both the shop pages and the admin."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from catalog.models import money
from core.models import SiteSettings

from . import emails
from .models import CustomRequest, Message, Order, OrderEvent, OrderItem

ADDRESS_FIELDS = ["full_name", "email", "phone", "company_name", "vat_number", "tax_office",
                  "address", "city", "postal_code", "country"]


def _admin_link(obj):
    return reverse(f"admin:orders_{obj._meta.model_name}_change", args=[obj.pk])


def default_shipping(site, items_total, business):
    """Default shipping from the shop settings, in the same VAT basis as the items."""
    gross_items = items_total * site.vat_multiplier if business else items_total
    if site.free_shipping_over is not None and gross_items >= site.free_shipping_over:
        return money(0)
    return money(site.shipping_cost / site.vat_multiplier) if business else site.shipping_cost


def record_status(order, note="", notify=True):
    """Log the order's current status in its history and email the customer about it.

    Delivered orders go to the archive; reopening one brings it back."""
    if order.status == Order.Status.DELIVERED and not order.archived_at:
        order.archived_at = timezone.now()
        order.save(update_fields=["archived_at"])
    elif order.status in Order.OPEN_STATUSES and order.archived_at:
        order.archived_at = None
        order.save(update_fields=["archived_at"])
    event = OrderEvent.objects.create(order=order, status=order.status, note=note)
    if notify:
        event.emailed = emails.order_status(order, note)
        event.save(update_fields=["emailed"])
    return event


def set_order_status(order, status, note="", notify=True):
    order.status = status
    order.save(update_fields=["status", "updated_at"])
    return record_status(order, note, notify)


def _item_name(line):
    details = [*([str(line["size"].size)] if line.get("size") else []), *[o.name for o in line.get("options", ())]]
    return f"{line['product'].name} – {', '.join(details)}" if details else line["product"].name


def create_order(user, lines, address, notes=""):
    """Turn the cart into an order request (status: Request received)."""
    site = SiteSettings.load()
    business = user.is_business
    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            is_business=business,
            prices_include_vat=not business,
            vat_rate=site.vat_rate,
            customer_notes=notes,
            **{f: address.get(f, "") for f in ADDRESS_FIELDS},
        )
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=line["product"], product_name=_item_name(line),
                      quantity=line["quantity"], unit_price=line["price"].final)
            for line in lines
        ])
        order.shipping_cost = default_shipping(site, order.items_total, business)
        order.save(update_fields=["shipping_cost"])
    # Emails go out after the database work, so a slow mail server never holds up the shop.
    record_status(order)
    emails.to_shop(
        _("New order request %(no)s") % {"no": order.number},
        [order.full_name + (f" ({order.company_name})" if order.company_name else ""),
         _("Total: €%(total)s") % {"total": order.total},
         *[str(item) for item in order.items.all()]],
        _admin_link(order), reply_to=[order.email],
    )
    return order


def cancel_by_customer(order):
    set_order_status(order, Order.Status.CANCELLED, notify=True)
    emails.to_shop(_("Order %(no)s was cancelled by the customer") % {"no": order.number},
                   [order.full_name], _admin_link(order))


def create_custom_request(user, data, images):
    with transaction.atomic():
        req = CustomRequest.objects.create(user=user, is_business=user.is_business, **data)
        for image in images:
            req.images.create(image=image)
    emails.request_status(req)
    emails.to_shop(
        _("New custom request %(no)s") % {"no": req.number},
        [str(user), user.email, req.subject, req.description[:500]],
        _admin_link(req), reply_to=[user.email],
    )
    return req


def accept_quote(req, address):
    """The customer accepts your offer: create the order and send payment instructions."""
    site = SiteSettings.load()
    with transaction.atomic():
        order = Order.objects.create(
            user=req.user,
            status=Order.Status.AWAITING_PAYMENT,
            is_business=req.is_business,
            prices_include_vat=not req.is_business,
            vat_rate=site.vat_rate,
            shipping_cost=req.quoted_shipping,
            custom_request=req,
            **{f: address.get(f, "") for f in ADDRESS_FIELDS},
        )
        name = req.subject if req.quantity == 1 else _("%(subject)s (%(qty)s pieces)") % {
            "subject": req.subject, "qty": req.quantity}
        OrderItem.objects.create(order=order, product_name=name, quantity=1, unit_price=req.quoted_price)
        req.status = CustomRequest.Status.ACCEPTED
        req.save(update_fields=["status", "updated_at"])
    record_status(order)
    emails.to_shop(
        _("Offer accepted: %(req)s → order %(no)s") % {"req": req.number, "no": order.number},
        [str(req.user), req.subject, _("Total: €%(total)s") % {"total": order.total}],
        _admin_link(order),
    )
    return order


def decline_quote(req):
    req.status = CustomRequest.Status.DECLINED
    req.save(update_fields=["status", "updated_at"])
    emails.to_shop(_("Offer declined: %(req)s") % {"req": req.number}, [str(req.user), req.subject],
                   _admin_link(req))


def post_customer_message(thread, user, body):
    kwargs = {"order": thread} if isinstance(thread, Order) else {"custom_request": thread}
    message = Message.objects.create(author=user, from_shop=False, body=body, **kwargs)
    emails.to_shop(_("New message from %(name)s about %(no)s") % {"name": user, "no": thread.number},
                   [body[:1000]], _admin_link(thread), reply_to=[user.email])
    return message


def post_shop_message(thread, author, body, notify=True):
    kwargs = {"order": thread} if isinstance(thread, Order) else {"custom_request": thread}
    message = Message.objects.create(author=author, from_shop=True, body=body, **kwargs)
    # Replying to a new request moves it to "in discussion".
    if isinstance(thread, CustomRequest) and thread.status == CustomRequest.Status.NEW:
        thread.status = CustomRequest.Status.DISCUSSION
        thread.save(update_fields=["status", "updated_at"])
    # Messages from the customer are now answered, so stop flagging them as unread.
    thread.messages.filter(from_shop=False, read_at__isnull=True).update(read_at=timezone.now())
    if notify:
        emails.new_message(message)
    return message


def mark_read_by_customer(thread):
    thread.messages.filter(from_shop=True, read_at__isnull=True).update(read_at=timezone.now())
