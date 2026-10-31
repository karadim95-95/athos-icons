"""
Parcel tracking straight from the couriers' business APIs: ACS (acs.py) and Geniki Taxydromiki (geniki.py).

Each courier module offers:
    enabled()        True when its API details are in the settings (.env)
    track(number)    the parcel's latest stage as an Update; raises CourierError when the courier can't answer
    TRACKING_URL     the public tracking page, for the link in the customer's emails
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.dateparse import parse_datetime

GREEK_TIME = ZoneInfo("Europe/Athens")


class CourierError(Exception):
    """The courier could not be asked, or refused (wrong API details, unknown voucher, request limit…)."""


@dataclass
class Update:
    stage: str             # an Order.CourierStatus value
    description: str = ""  # the courier's own words, e.g. "Σε διανομή – ΜΑΡΟΥΣΙ"
    when: datetime | None = None


def courier_time(text):
    """Couriers send Greek local time, often without a time zone."""
    try:
        when = parse_datetime(text or "")
    except ValueError:
        return None
    if when is None or when.year < 2000:  # .NET sends 0001-01-01 for "no date"
        return None
    return timezone.make_aware(when, GREEK_TIME) if timezone.is_naive(when) else when
