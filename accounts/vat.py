"""
Checks for the tax numbers of business accounts.

- Greek ΑΦΜ: the check digit catches typos and made-up numbers. With AADE special codes in the
  settings (free, see README), the official registry also confirms that the number belongs to an
  active business and gives its official name and ΔΟΥ.
- EU VAT numbers (e.g. Cyprus): checked with the EU's free VIES service.

If a registry cannot be reached, the number is accepted and the problem is noted on the account,
so a slow government server never stops a customer from signing up.
"""

import json
import logging
import re
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

log = logging.getLogger(__name__)

AADE_URL = "https://www1.gsis.gr/wsaade/RgWsPublic2/RgWsPublic2"
VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
EU_COUNTRIES = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "HR", "HU", "IE", "IT",
                "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "XI"}
# AADE answers for a number that is not (and never was) a business, or is not a valid ΑΦΜ.
AADE_NOT_A_BUSINESS = ("RG_WS_PUBLIC_EPIT_NF", "RG_WS_PUBLIC_WRONG_AFM")

AADE_REQUEST = """<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wss="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
 xmlns:srvc="http://rgwspublic2/RgWsPublic2Service" xmlns:rg="http://rgwspublic2/RgWsPublic2">
<env:Header><wss:Security><wss:UsernameToken><wss:Username>{username}</wss:Username>
<wss:Password>{password}</wss:Password></wss:UsernameToken></wss:Security></env:Header>
<env:Body><srvc:rgWsPublic2AfmMethod><srvc:INPUT_REC><rg:afm_called_by/>
<rg:afm_called_for>{afm}</rg:afm_called_for></srvc:INPUT_REC></srvc:rgWsPublic2AfmMethod></env:Body>
</env:Envelope>"""


class RegistryUnavailable(Exception):
    """The registry could not be asked (network, credentials, daily limit…)."""


@dataclass
class Result:
    number: str            # as stored on the account: "123456783" or "CY12345678X"
    registry: str = ""     # what the registry said, shown to you in the admin
    tax_office: str = ""   # official ΔΟΥ from AADE
    checked: bool = False  # a registry was actually asked


def split(value):
    """'EL 123 456 783' → ('EL', '123456783'); 'cy12345678x' → ('CY', '12345678X')."""
    compact = re.sub(r"[\s.\-/]", "", value or "").upper()
    if compact[:2] in ("EL", "GR"):
        return "EL", compact[2:]
    if compact[:2].isalpha():
        return compact[:2], compact[2:]
    return "EL", compact


def afm_checksum_ok(afm):
    if not re.fullmatch(r"\d{9}", afm) or afm == "000000000":
        return False
    total = sum(int(digit) * 2 ** (8 - i) for i, digit in enumerate(afm[:8]))
    return total % 11 % 10 == int(afm[8])


def aade_enabled():
    return bool(settings.AADE_USERNAME and settings.AADE_PASSWORD)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def aade_lookup(afm):
    """Registry details for a Greek ΑΦΜ as a dict, or None if it does not belong to a business."""
    body = AADE_REQUEST.format(username=escape(settings.AADE_USERNAME), password=escape(settings.AADE_PASSWORD),
                               afm=escape(afm))
    request = Request(AADE_URL, data=body.encode(),
                      headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urlopen(request, timeout=15) as response:
            root = ElementTree.fromstring(response.read())
    except (URLError, TimeoutError, ElementTree.ParseError) as error:
        raise RegistryUnavailable(str(error)) from error

    fields = {}
    activities = []
    for element in root.iter():
        name = _local(element.tag)
        if name == "item" and element.find("./*") is not None:
            activities.append({_local(child.tag): (child.text or "").strip() for child in element})
        elif len(element) == 0:
            fields.setdefault(name, (element.text or "").strip())
    error_code = fields.get("error_code", "")
    if error_code in AADE_NOT_A_BUSINESS:
        return None
    if error_code or not fields.get("afm"):
        raise RegistryUnavailable(fields.get("error_descr") or error_code or "empty answer")
    main = next((a for a in activities if a.get("firm_act_kind") == "1"), activities[0] if activities else {})
    fields["main_activity"] = main.get("firm_act_descr", "")
    return fields


def vies_check(country, number):
    """(True/False, details) from the EU VIES service, or (None, problem) if it could not answer."""
    request = Request(VIES_URL, data=json.dumps({"countryCode": country, "vatNumber": number}).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            answer = json.load(response)
    except (URLError, TimeoutError, ValueError) as error:
        return None, str(error)
    if "valid" not in answer:
        return None, str(answer)[:200]
    details = ", ".join(part for part in [answer.get("name"), answer.get("address")] if part and part != "---")
    return bool(answer["valid"]), details


def _check_greek(afm):
    if not afm_checksum_ok(afm):
        raise ValidationError(_("This ΑΦΜ is not valid. Please check the 9 digits."))
    if not aade_enabled():
        return Result(afm)
    try:
        found = aade_lookup(afm)
    except RegistryUnavailable as error:
        log.warning("AADE lookup for %s failed: %s", afm, error)
        return Result(afm, registry=_("AADE registry could not be checked: %(error)s") % {"error": error})
    if found is None:
        raise ValidationError(_("This ΑΦΜ does not belong to a business in the AADE registry."))
    if found.get("deactivation_flag") == "2":
        raise ValidationError(_("This ΑΦΜ is no longer active in the AADE registry."))
    address = " ".join(p for p in [found.get("postal_address"), found.get("postal_address_no"),
                                   found.get("postal_zip_code"), found.get("postal_area_description")] if p)
    lines = [
        f"AADE: {found.get('onomasia', '')}",
        found.get("commer_title", ""),
        f"{_('Tax office')}: {found.get('doy_descr', '')}",
        address,
        found.get("main_activity", ""),
        found.get("deactivation_flag_descr", ""),
    ]
    return Result(afm, registry="\n".join(line for line in lines if line.strip()),
                  tax_office=found.get("doy_descr", ""), checked=True)


def _check_eu(country, number):
    valid, details = vies_check(country, number)
    if valid is False:
        raise ValidationError(_("This VAT number is not valid according to the EU VIES service."))
    if valid is None:
        log.warning("VIES check for %s%s failed: %s", country, number, details)
        return Result(country + number, registry=_("VIES could not be checked: %(error)s") % {"error": details})
    return Result(country + number, registry=f"VIES: {details}" if details else "VIES: OK", checked=True)


def check(value):
    """Validate a business tax number. Returns a Result or raises ValidationError."""
    country, number = split(value)
    cache_key = f"vat-check:{country}{number}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    if country == "EL":
        result = _check_greek(number)
    elif country in EU_COUNTRIES and re.fullmatch(r"[0-9A-Z]{2,12}", number):
        result = _check_eu(country, number)
    else:
        raise ValidationError(_("Enter a Greek ΑΦΜ (9 digits), or an EU VAT number starting with the country "
                                "code, e.g. CY12345678X."))
    if result.checked:
        cache.set(cache_key, result, 86400)
    return result
