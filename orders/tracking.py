"""
Automatic parcel tracking through 17TRACK (https://www.17track.net), which follows the
Greek couriers (ACS, ELTA, ELTA Courier, Geniki Taxydromiki, Speedex, Courier Center,
BOX NOW, DPD) and most international ones.

It is on when TRACKING_API_KEY is set. When you save an order as Shipped with a tracking
number, the number is registered with 17TRACK. `python manage.py update_tracking`, run every
couple of hours (the "tracker" service in docker-compose.yml does that), then moves the
order to In transit and Delivered and emails the customer each time.
"""

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from . import emails, services
from .models import Order

log = logging.getLogger(__name__)

API_URL = "https://api.17track.net/track/v2.4/"
BATCH_SIZE = 40  # the API's limit per request

# 17TRACK courier codes, recognised from the tracking link's domain. Without a match
# 17TRACK guesses the courier from the tracking number.
CARRIERS = {
    "acscourier.net": 100070,
    "elta-courier.gr": 100179,
    "elta.gr": 7071,
    "taxydromiki.com": 100224,
    "speedex.gr": 100315,
    "courier.gr": 100667,
    "boxnow.gr": 101008,
    "dpd.gr": 100404,
    "dhl.com": 100001,
    "ups.com": 100002,
    "fedex.com": 100003,
}

C = Order.CourierStatus
# The order status each courier stage moves the order to.
STAGES = {
    C.IN_TRANSIT: Order.Status.IN_TRANSIT,
    C.OUT_FOR_DELIVERY: Order.Status.IN_TRANSIT,
    C.AVAILABLE_FOR_PICKUP: Order.Status.IN_TRANSIT,
    C.DELIVERED: Order.Status.DELIVERED,
}

ALREADY_REGISTERED = -18019901
CARRIER_NOT_DETECTED = -18019903


class TrackingError(Exception):
    """17TRACK could not be reached or refused the request."""


def enabled():
    return bool(settings.TRACKING_API_KEY)


def carrier_for(url):
    host = (urlsplit(url or "").hostname or "").lower()
    for domain, code in CARRIERS.items():
        if host == domain or host.endswith("." + domain):
            return code
    return None


def clean_number(number):
    return re.sub(r"\s+", "", number or "").upper()


def needs_registration(order):
    return bool(order.tracking_number) and order.tracking_registered != clean_number(order.tracking_number)


def _item(order):
    item = {"number": clean_number(order.tracking_number)}
    carrier = carrier_for(order.tracking_url)
    if carrier:
        item["carrier"] = carrier
    return item


def _call(endpoint, items):
    request = Request(
        API_URL + endpoint, data=json.dumps(items).encode(), method="POST",
        headers={"17token": settings.TRACKING_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = json.load(response)
    except HTTPError as error:  # e.g. a wrong key: the reason is in the body
        try:
            body = json.load(error)
        except ValueError:
            raise TrackingError(str(error)) from error
    except (URLError, TimeoutError, ValueError) as error:
        raise TrackingError(str(error)) from error
    if body.get("code") != 0:
        errors = (body.get("data") or {}).get("errors") or [{}]
        raise TrackingError(errors[0].get("message") or f"17TRACK error {body.get('code')}")
    return body.get("data") or {}


def _chunks(items):
    for i in range(0, len(items), BATCH_SIZE):
        yield items[i:i + BATCH_SIZE]


def register(orders):
    """Start tracking these orders' numbers. Problems are saved in order.tracking_error."""
    orders = [o for o in orders if needs_registration(o)]
    for batch in _chunks(orders):
        data = _call("register", [_item(o) for o in batch])
        results = {clean_number(r["number"]): None for r in data.get("accepted", [])}
        for rejected in data.get("rejected", []):
            error = rejected.get("error") or {}
            code = error.get("code")
            if code == ALREADY_REGISTERED:
                message = None
            elif code == CARRIER_NOT_DETECTED:
                message = _("The courier could not be recognised from the tracking number. "
                            "Add the courier's tracking link as well.")
            else:
                message = error.get("message") or str(code)
            results[clean_number(rejected.get("number"))] = message
        for order in batch:
            number = clean_number(order.tracking_number)
            if number not in results:
                continue
            if results[number] is None:
                order.tracking_registered = number
                order.tracking_status = order.tracking_event = order.tracking_error = ""
                order.tracking_event_at = order.tracking_checked_at = None
            else:
                order.tracking_error = results[number][:300]
            order.save(update_fields=["tracking_registered", "tracking_status", "tracking_event",
                                      "tracking_event_at", "tracking_checked_at", "tracking_error"])


def apply(order, info):
    """Save the courier's latest update; move the order on if it reached a new stage. Returns True if it moved."""
    status = (info.get("latest_status") or {}).get("status") or ""
    order.tracking_checked_at = timezone.now()
    fields = ["tracking_checked_at"]
    moved = False
    new_problem = status in Order.COURIER_PROBLEMS and status != order.tracking_status
    if status:
        event = info.get("latest_event") or {}
        order.tracking_status = status
        order.tracking_event = " – ".join(p for p in [event.get("description"), event.get("location")] if p)[:300]
        try:
            order.tracking_event_at = parse_datetime(event.get("time_utc") or "")
        except ValueError:
            order.tracking_event_at = None
        fields += ["tracking_status", "tracking_event", "tracking_event_at"]
        new_status = STAGES.get(status)
        if new_status and Order.FLOW.index(new_status) > Order.FLOW.index(order.status):
            order.status = new_status
            fields += ["status", "updated_at"]
            moved = True
    order.save(update_fields=fields)
    if moved:
        services.record_status(order)
    if moved and order.status == Order.Status.DELIVERED:
        emails.to_shop(
            _("Order %(no)s was delivered") % {"no": order.number},
            [order.company_name or order.full_name, order.tracking_event,
             _("The order was moved to the archive.")],
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


def sync(orders=None):
    """Ask 17TRACK about shipped orders and update them. Returns how many orders moved on."""
    if not enabled():
        return 0
    if orders is None:
        orders = Order.objects.all()
    orders = list(orders.filter(status__in=Order.ON_THE_WAY).exclude(tracking_number="").select_related("user"))
    register(orders)
    tracked = [o for o in orders if o.tracking_registered and not needs_registration(o)]
    moved = 0
    for batch in _chunks(tracked):
        data = _call("gettrackinfo", [_item(o) for o in batch])
        found = {clean_number(a["number"]): a.get("track_info") or {} for a in data.get("accepted", [])}
        for order in batch:
            info = found.get(order.tracking_registered)
            if info is not None:
                moved += apply(order, info)
    return moved
