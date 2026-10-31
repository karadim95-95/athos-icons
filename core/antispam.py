"""
Protection against bots: attempt limits, plus a check that a form was filled in by a person.

A form counts as filled in by a person when the hidden "website" field (invisible to people)
is empty, it was sent at least a couple of seconds after the page loaded, and, if Turnstile
keys are set, Cloudflare's check passed.
"""

import json
import logging
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.core.cache import cache

log = logging.getLogger(__name__)

MIN_SECONDS = 2  # people need longer than this to fill in a form; bots don't
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def client_ip(request):
    # Caddy puts the visitor's address last in X-Forwarded-For.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[-1].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


# --- Attempt limits ------------------------------------------------------------
def _key(scope, who):
    return f"limit:{scope}:{who}"


def attempts(scope, who):
    return cache.get(_key(scope, who), 0)


def record_attempt(scope, who, period):
    key = _key(scope, who)
    cache.add(key, 0, period)
    try:
        return cache.incr(key)
    except ValueError:  # expired in between
        cache.set(key, 1, period)
        return 1


def too_many(scope, who, limit, period):
    """Count one attempt. True once there were more than `limit` within `period` seconds."""
    return record_attempt(scope, who, period) > limit


# --- Filled in by a person? ----------------------------------------------------------
def form_timestamp(seconds_ago=0):
    return signing.dumps(int(time.time() - seconds_ago), salt="antispam.form")


def turnstile_enabled():
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def turnstile_passed(request):
    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return False
    data = urlencode({"secret": settings.TURNSTILE_SECRET_KEY, "response": token,
                      "remoteip": client_ip(request)}).encode()
    try:
        with urlopen(Request(TURNSTILE_VERIFY_URL, data=data), timeout=10) as response:
            return bool(json.load(response).get("success"))
    except (URLError, TimeoutError, ValueError):
        log.exception("Turnstile check failed")
        return False


def looks_human(request):
    data = request.POST
    if data.get("website"):
        return False
    try:
        started = signing.loads(data.get("form_ts", ""), salt="antispam.form")
    except signing.BadSignature:
        return False
    if time.time() - started < MIN_SECONDS:
        return False
    return turnstile_passed(request) if turnstile_enabled() else True
