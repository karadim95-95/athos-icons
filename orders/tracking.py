"""
Automatic parcel tracking, straight from the couriers' business APIs (orders/couriers/): ACS and
Geniki Taxydromiki, each on once its API details are in the settings.

`python manage.py update_tracking`, run every couple of hours (the "jobs" service in docker-compose.yml),
asks the courier about every order on its way, moves it to In transit and Delivered and emails the
customer each time. You are emailed about delivery problems.
"""

import logging
import re

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import emails, services
from .couriers import CourierError, acs, geniki
from .models import Order

log = logging.getLogger(__name__)

COURIERS = {Order.Courier.ACS: acs, Order.Courier.GENIKI: geniki}

C = Order.CourierStatus
# The order status each courier stage moves the order to.
STAGES = {
    C.IN_TRANSIT: Order.Status.IN_TRANSIT,
    C.OUT_FOR_DELIVERY: Order.Status.IN_TRANSIT,
    C.AVAILABLE_FOR_PICKUP: Order.Status.IN_TRANSIT,
    C.DELIVERED: Order.Status.DELIVERED,
}


def enabled():
    return any(courier.enabled() for courier in COURIERS.values())


def courier_for(order):
    """The courier module that can track this order, if its API details are set."""
    courier = COURIERS.get(order.courier)
    return courier if courier and courier.enabled() else None


def tracking_url(courier, number):
    courier = COURIERS.get(courier)
    return courier.TRACKING_URL.format(clean_number(number)) if courier and number else ""


def clean_number(number):
    return re.sub(r"\s+", "", number or "").upper()


def apply(order, update):
    """Save the courier's latest update; move the order on if it reached a new stage. Returns True if it moved."""
    new_problem = update.stage in Order.COURIER_PROBLEMS and update.stage != order.tracking_status
    order.tracking_status = update.stage
    order.tracking_event = update.description[:300]
    order.tracking_event_at = update.when
    order.tracking_checked_at = timezone.now()
    order.tracking_error = ""
    fields = ["tracking_status", "tracking_event", "tracking_event_at", "tracking_checked_at", "tracking_error"]
    new_status = STAGES.get(update.stage)
    moved = bool(new_status) and Order.FLOW.index(new_status) > Order.FLOW.index(order.status)
    if moved:
        order.status = new_status
        fields += ["status", "updated_at"]
    order.save(update_fields=fields)

    if moved:
        services.record_status(order)
    if moved and order.status == Order.Status.DELIVERED:
        emails.to_shop(
            _("Order %(no)s was delivered") % {"no": order.number},
            [order.company_name or order.full_name, order.tracking_event, _("The order was moved to the archive.")],
            reverse("admin:orders_order_change", args=[order.pk]),
        )
    if new_problem:
        emails.to_shop(
            _("Delivery problem with order %(no)s") % {"no": order.number},
            [order.get_tracking_status_display(), order.tracking_event,
             f"{_('Tracking number')}: {order.tracking_number}", f"{order.full_name}, {order.phone}"],
            reverse("admin:orders_order_change", args=[order.pk]),
        )
    return moved


def check(order):
    """Ask the courier about one order. Returns True if it moved on; problems are saved in tracking_error."""
    courier = courier_for(order)
    if courier is None or not order.tracking_number:
        return False
    try:
        update = courier.track(clean_number(order.tracking_number))
    except CourierError as error:
        log.warning("Tracking %s failed: %s", order.number, error)
        order.tracking_error = str(error)[:300]
        order.tracking_checked_at = timezone.now()
        order.save(update_fields=["tracking_error", "tracking_checked_at"])
        return False
    return apply(order, update)


def sync(orders=None):
    """Check every order on its way with a tracked courier. Returns how many orders moved on."""
    if orders is None:
        orders = Order.objects.all()
    orders = (orders.filter(status__in=Order.ON_THE_WAY, courier__in=list(COURIERS))
              .exclude(tracking_number="").select_related("user"))
    return sum(check(order) for order in orders)
