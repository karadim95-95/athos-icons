"""
ACS Courier, through ACS Web Services: the REST API ACS gives its business customers.
Specification: "ACS Rest API Web Services" (https://acscourier.net/media/mcqpgkvq/acs-web-services-english.pdf).

Settings, from your ACS contract: ACS_API_KEY, ACS_COMPANY_ID, ACS_COMPANY_PASSWORD, ACS_USER_ID, ACS_USER_PASSWORD.
The API key needs rights to ACS_Trackingsummary and ACS_TrackingDetails.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.translation import gettext as _

from catalog.models import normalize_search

from ..models import Order
from . import CourierError, Update, courier_time

URL = "https://webservices.acscourier.net/ACSRestServices/api/ACSAutoRest"
TRACKING_URL = "https://www.acscourier.net/el/myacs/anafores-apostolwn/anazitisi-apostolwn/?trackingNumber={}"

C = Order.CourierStatus
DELIVERED, TO_BE_RETURNED, RETURNED = 4, 6, 7  # "shipment_status" codes
# "non_delivery_reason_code": waiting at an ACS shop, or a failed delivery you may need to act on.
# The other codes (force majeure, hard-to-reach area, new delivery date, LS2 "on its way") mean it is on its way.
PICKUP_AT_SHOP = {"AD1", "AD8"}
FAILED = {"AP1", "AP2", "AP3", "AP4", "AS1", "LS1", "LS3"}  # refused, absent, unknown recipient, wrong address


def enabled():
    return all([settings.ACS_API_KEY, settings.ACS_COMPANY_ID, settings.ACS_COMPANY_PASSWORD,
                settings.ACS_USER_ID, settings.ACS_USER_PASSWORD])


def _call(alias, number):
    body = {"ACSAlias": alias, "ACSInputParameters": {
        "Company_ID": settings.ACS_COMPANY_ID, "Company_Password": settings.ACS_COMPANY_PASSWORD,
        "User_ID": settings.ACS_USER_ID, "User_Password": settings.ACS_USER_PASSWORD,
        "Language": "GR", "Voucher_No": number,
    }}
    request = Request(URL, data=json.dumps(body).encode(),
                      headers={"ACSApiKey": settings.ACS_API_KEY, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            answer = json.load(response)
    except HTTPError as error:
        if error.code == 403:
            raise CourierError(_("ACS refused the API key (ACS_API_KEY).")) from error
        raise CourierError(f"ACS: {error}") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise CourierError(f"ACS: {error}") from error
    if answer.get("ACSExecution_HasError"):
        raise CourierError(f"ACS: {answer.get('ACSExecutionErrorMessage') or 'error'}")
    output = answer.get("ACSOutputResponce") or answer.get("ACSOutputResponse") or {}  # ACS spells it both ways
    for value in output.get("ACSValueOutput") or []:
        if value.get("Error_Message"):
            raise CourierError(f"ACS: {value['Error_Message']}")
    return (output.get("ACSTableOutput") or {}).get("Table_Data") or []


def _stage(summary, latest):
    flag = lambda name: str(summary.get(name)).strip() == "1"  # noqa: E731
    try:
        status = int(summary.get("shipment_status"))
    except (TypeError, ValueError):
        status = None
    reason = (summary.get("non_delivery_reason_code") or "").strip().upper()
    if flag("returned_flag") or status in (TO_BE_RETURNED, RETURNED):
        return C.EXCEPTION
    if flag("delivery_flag") or status == DELIVERED:
        return C.DELIVERED
    if reason in PICKUP_AT_SHOP:
        return C.AVAILABLE_FOR_PICKUP
    if reason in FAILED:
        return C.DELIVERY_FAILURE
    action = normalize_search((latest or {}).get("checkpoint_action") or "")
    if "διανομ" in action or "on delivery" in action:
        return C.OUT_FOR_DELIVERY
    if latest or summary.get("pickup_date"):
        return C.IN_TRANSIT
    return C.INFO_RECEIVED


def track(number):
    summary = next(iter(_call("ACS_Trackingsummary", number)), None)
    if summary is None:
        return Update(C.NOT_FOUND)
    checkpoints = _call("ACS_TrackingDetails", number)
    latest = max(checkpoints, key=lambda c: c.get("checkpoint_date_time") or "", default=None)
    if latest:
        description = " – ".join(p for p in [(latest.get("checkpoint_action") or "").strip(),
                                             (latest.get("checkpoint_location") or "").strip()] if p)
        when = courier_time(latest.get("checkpoint_date_time"))
    else:
        description, when = (summary.get("delivery_info") or "").strip(), None
    return Update(_stage(summary, latest), description, when)
