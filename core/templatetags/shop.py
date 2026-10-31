import re

from django import template
from django.urls import reverse
from django.utils import formats
from django.utils.translation import gettext as _

from catalog.pricing import price_info

register = template.Library()


@register.filter
def euro(value):
    if value is None or value == "":
        return ""
    return f"{formats.number_format(value, 2, use_l10n=True, force_grouping=True)} €"


@register.filter
def short_size(text):
    """ "20 x 26 cm" → "20 × 26": the unit is already in the heading."""
    text = re.sub(r"\s*(cm|εκ\.?)$", "", str(text), flags=re.IGNORECASE)
    return re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", " × ", text)


@register.simple_tag(takes_context=True)
def price(context, product):
    """{% price product as p %} → p.final, p.original, p.has_discount, p.business"""
    return price_info(product, context["request"].user, context.get("shop"))


@register.simple_tag
def admin_attention():
    """Counts for the 'needs your attention' panel on the admin home page."""
    from accounts.models import User
    from orders import tracking
    from orders.models import CustomRequest, Order

    def link(model, query):
        return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist") + query

    S, R = Order.Status, CustomRequest.Status
    rows = [
        (_("New order requests to review"), Order.objects.filter(status=S.PENDING).count(),
         link(Order, f"?status__exact={S.PENDING}")),
        (_("Orders awaiting payment"), Order.objects.filter(status=S.AWAITING_PAYMENT).count(),
         link(Order, f"?status__exact={S.AWAITING_PAYMENT}")),
        (_("Paid orders to ship"), Order.objects.filter(status=S.PAID).count(),
         link(Order, f"?status__exact={S.PAID}")),
        (_("Shipped orders without a receipt"), Order.objects.needing_receipt().count(),
         link(Order, "?receipt=missing")),
        (_("New custom requests"), CustomRequest.objects.filter(status=R.NEW).count(),
         link(CustomRequest, f"?status__exact={R.NEW}")),
        (_("Orders with unanswered messages"),
         Order.objects.filter(messages__from_shop=False, messages__read_at__isnull=True).distinct().count(),
         link(Order, "?unread=1")),
        (_("Custom requests with unanswered messages"),
         CustomRequest.objects.filter(messages__from_shop=False, messages__read_at__isnull=True).distinct().count(),
         link(CustomRequest, "?unread=1")),
        (_("Business accounts to approve"),
         User.objects.filter(account_type=User.AccountType.BUSINESS, business_approved=False).count(),
         link(User, "?account_type__exact=business&business_approved__exact=0")),
    ]
    if tracking.enabled():
        rows.insert(3, (_("Shipments with a delivery problem"), Order.objects.with_tracking_problem().count(),
                        link(Order, "?tracking=problem")))
    return rows
