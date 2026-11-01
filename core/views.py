from django.contrib import messages
from django.db.models import Count, Q
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from catalog.models import Category, Product, ProductImage
from orders import emails

from . import antispam, demo
from .forms import ContactForm
from .models import Page


def home(request):
    products = Product.objects.visible().select_related("category").prefetch_related("images")
    featured = list(products.filter(is_featured=True)[:8])
    categories = list(Category.objects.filter(is_active=True).annotate(
        n=Count("products", filter=Q(products__is_active=True))
    ).filter(n__gt=0)[:8])
    # A collection without its own picture shows a photo of one of its icons (featured ones first).
    covers = {}
    for image in (ProductImage.objects.filter(product__in=products, product__category__in=categories)
                  .order_by("-product__is_featured", "product__id", "order").select_related("product")):
        covers.setdefault(image.product.category_id, image.image.url)
    for category in categories:
        category.cover = category.image.url if category.image else covers.get(category.pk)
    return render(request, "core/home.html", {
        "categories": categories,
        "featured": featured,
        "new_products": products.exclude(pk__in=[p.pk for p in featured])[:8],
        "on_sale": products.filter(discount_percent__gt=0, is_available=True)[:4],
    })


def page(request, slug):
    return render(request, "core/page.html", {"page": get_object_or_404(Page, slug=slug)})


def contact(request):
    form = ContactForm(request.POST or None, request=request)
    if request.method == "POST":
        if antispam.too_many("contact", antispam.client_ip(request), limit=5, period=3600):
            messages.error(request, _("Too many messages from your connection. Please try again later."))
            return render(request, "core/contact.html", {"form": form}, status=429)
        if form.is_valid():
            data = form.cleaned_data
            emails.to_shop(_("Contact form: %(name)s") % {"name": data["name"]},
                           [data["email"], "", data["message"]], reverse("core:contact"),
                           reply_to=[data["email"]])
            messages.success(request, _("Thank you! We received your message and will reply soon."))
            return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form})


def demo_mailbox(request):
    """Every email the demo would have sent (DEMO_MODE only)."""
    if not settings.DEMO_MODE:
        raise Http404
    return render(request, "core/demo_mailbox.html", {"emails": demo.mailbox()})


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /*/account/",
        "Disallow: /*/cart/",
        f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
