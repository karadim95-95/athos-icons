"""
All automatic emails. Templates live in templates/emails/; the first line of
each rendered template is the subject, the rest is the body.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import translation

from core.models import SiteSettings

log = logging.getLogger(__name__)


def send(to, template, context, language=None, reply_to=None):
    """Render and send one email. Returns True on success; failures are logged, never raised."""
    if not to:
        return False
    site = SiteSettings.load()
    context = {"shop": site, "site_url": settings.SITE_URL, **context}
    with translation.override(language or settings.LANGUAGE_CODE):
        text = render_to_string(f"emails/{template}.txt", context).strip()
    subject, _, body = text.partition("\n")
    message = EmailMessage(
        subject=f"{subject.strip()} – {site.shop_name}",
        body=body.strip() + "\n",
        to=[to] if isinstance(to, str) else to,
        reply_to=reply_to or ([site.contact_email] if site.contact_email else None),
    )
    try:
        message.send()
        return True
    except Exception:
        log.exception("Could not send email %s to %s", template, to)
        return False


def to_customer(user, template, context):
    return send(user.email, template, {"customer": user, **context}, language=user.language)


def to_shop(title, lines, link, reply_to=None):
    """Short notification for the shop owner."""
    site = SiteSettings.load()
    return send(
        site.notify_email, "admin_notice",
        {"title": title, "lines": lines, "link": f"{settings.SITE_URL}{link}"},
        reply_to=reply_to,
    )


def order_status(order, note=""):
    return to_customer(order.user, "order_status", {"order": order, "note": note})


def request_status(custom_request, note=""):
    return to_customer(custom_request.user, "request_status", {"req": custom_request, "note": note})


def new_message(message):
    thread = message.thread
    return to_customer(thread.user, "new_message", {"message": message, "thread": thread})


def verify_email(user, link):
    return to_customer(user, "verify_email", {"link": link})


def business_approved(user):
    return to_customer(user, "business_approved", {})
