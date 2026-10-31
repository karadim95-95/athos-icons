"""
Geniki Taxydromiki, through JobServicesV2: the SOAP web service Geniki gives its business customers.
Specification: https://voucher.taxydromiki.gr/help/jobservicesapiv2.pdf

Settings, from Geniki: GENIKI_USERNAME, GENIKI_PASSWORD, GENIKI_APP_KEY, and GENIKI_TEST=1 while using
the test user they give you.
"""

from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

from ..models import Order
from . import CourierError, Update, courier_time

LIVE_URL = "https://voucher.taxydromiki.gr/JobServicesV2.asmx"
TEST_URL = "https://testvoucher.taxydromiki.gr/JobServicesV2.asmx"
NAMESPACE = "http://voucher.taxydromiki.gr/JobServicesV2.asmx"
TRACKING_URL = "https://www.taxydromiki.com/track/{}"
KEY_CACHE = "geniki-auth-key"

OK, AUTH_FAILED, NOT_FOUND, NOT_YOURS, INVALID_KEY = 0, 1, 9, 10, 11  # result codes

C = Order.CourierStatus
# Geniki's checkpoint status codes ("Checkpoints Status Codes" table) that say more than "on its way".
STAGES = {
    **dict.fromkeys(["C_NW", "C_KK", "C_P1", "C_SC"], C.INFO_RECEIVED),          # label created, details
    **dict.fromkeys(["C_A3", "C_S0"], C.OUT_FOR_DELIVERY),
    **dict.fromkeys(["C_D2", "C_S9"], C.AVAILABLE_FOR_PICKUP),                     # waiting to be picked up
    **dict.fromkeys(["C_W2", "C_S2"], C.DELIVERED),
    # attempted delivery: unknown recipient, damaged, refused, absent, wrong address, not distributed
    **dict.fromkeys(["C_EA_AG", "C_EA_AK", "C_EA_AP", "C_EA_AS", "C_EA_LD", "C_EA_DD", "C_SF"], C.DELIVERY_FAILURE),
    # back to the sender, cancelled, lost, damaged, destroyed
    **dict.fromkeys(["C_E1", "C_EA_EP", "C_P4", "C_S3", "C_S5", "C_S6", "C_S8", "C_SA", "C_SB"], C.EXCEPTION),
}
# Checkpoints that cancel the latest earlier one with the given code.
CANCELS = {"C_E2": "C_E1", "C_L2": "C_L1", "C_K9": "C_K8"}


def enabled():
    return all([settings.GENIKI_USERNAME, settings.GENIKI_PASSWORD, settings.GENIKI_APP_KEY])


def _local(element):
    return element.tag.rsplit("}", 1)[-1]


def _text(element, name):
    """Text of a direct child (checkpoints have fields with the same names as the result)."""
    found = next((e for e in element if _local(e) == name), None)
    return (found.text or "").strip() if found is not None else ""


def _call(action, fields):
    """Run one SOAP method; returns its <...Result> element."""
    arguments = "".join(f"<{name}>{escape(str(value))}</{name}>" for name, value in fields.items())
    body = ('<?xml version="1.0" encoding="utf-8"?><soap:Envelope '
            'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            f'<{action} xmlns="{NAMESPACE}">{arguments}</{action}></soap:Body></soap:Envelope>')
    request = Request(TEST_URL if settings.GENIKI_TEST else LIVE_URL, data=body.encode(),
                      headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{NAMESPACE}/{action}"'})
    try:
        with urlopen(request, timeout=20) as response:
            root = ElementTree.fromstring(response.read())
    except (URLError, TimeoutError, ElementTree.ParseError) as error:
        raise CourierError(f"Geniki Taxydromiki: {error}") from error
    result = next((e for e in root.iter() if _local(e) == f"{action}Result"), None)
    if result is None:
        raise CourierError(f"Geniki Taxydromiki: no answer to {action}")
    return result


def _code(result):
    try:
        return int(_text(result, "Result"))
    except ValueError:
        return -1


def _auth_key(renew=False):
    """Geniki's login key is reused until it expires (then the service answers INVALID_KEY)."""
    key = None if renew else cache.get(KEY_CACHE)
    if key:
        return key
    result = _call("Authenticate", {"sUsrName": settings.GENIKI_USERNAME, "sUsrPwd": settings.GENIKI_PASSWORD,
                                    "applicationKey": settings.GENIKI_APP_KEY})
    if _code(result) == AUTH_FAILED:
        raise CourierError(_("Geniki Taxydromiki refused the user name, password or application key."))
    if _code(result) != OK or not _text(result, "Key"):
        raise CourierError(f"Geniki Taxydromiki: login error {_code(result)}")
    key = _text(result, "Key")
    cache.set(KEY_CACHE, key, 6 * 3600)
    return key


def _checkpoints(result):
    """The checkpoints in order, without the ones that a later checkpoint cancelled."""
    kept = []
    for point in (e for e in result.iter() if _local(e) == "Checkpoint"):
        code = _text(point, "StatusCode")
        if code in CANCELS:
            cancelled = next((p for p in reversed(kept) if p["code"] == CANCELS[code]), None)
            if cancelled:
                kept.remove(cancelled)
            continue
        kept.append({"code": code, "status": _text(point, "Status"), "shop": _text(point, "Shop"),
                     "when": courier_time(_text(point, "StatusDate"))})
    return kept


def track(number):
    for renew in (False, True):
        result = _call("TrackAndTrace", {"authKey": _auth_key(renew), "voucherNo": number, "language": "el"})
        if _code(result) != INVALID_KEY:
            break
    code = _code(result)
    if code == NOT_FOUND:
        raise CourierError(_("Geniki Taxydromiki does not know this voucher number."))
    if code == NOT_YOURS:
        raise CourierError(_("This voucher was not sent from your Geniki Taxydromiki account."))
    if code != OK:
        raise CourierError(f"Geniki Taxydromiki: error {code}")

    points = _checkpoints(result)
    latest = points[-1] if points else None
    stage = STAGES.get(latest["code"], C.IN_TRANSIT) if latest else C.NOT_FOUND
    status = _text(result, "Status").upper()
    if status == "IN RETURN" or (status == "DELIVERED" and _text(result, "DeliveredAt").upper() == "RETURN TO SENDER"):
        stage = C.EXCEPTION
    elif status == "DELIVERED":
        stage = C.DELIVERED
    if not latest:
        return Update(stage)
    return Update(stage, " – ".join(p for p in [latest["status"], latest["shop"]] if p), latest["when"])
