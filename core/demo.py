"""Pieces used only by the downloadable demo (DEMO_MODE, see demo/launch.py)."""

import json

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils import timezone


class DemoMailBackend(BaseEmailBackend):
    """Keeps emails in a file shown on the "Demo mailbox" page instead of sending them."""

    def send_messages(self, messages):
        settings.DEMO_MAILBOX.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.DEMO_MAILBOX, "a", encoding="utf-8") as mailbox:
            for message in messages:
                mailbox.write(json.dumps({
                    "to": ", ".join(message.to), "subject": message.subject, "body": message.body,
                    "date": timezone.now().isoformat(),
                }, ensure_ascii=False) + "\n")
        return len(messages)


def mailbox():
    """Emails sent so far, newest first."""
    try:
        lines = settings.DEMO_MAILBOX.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    emails = [json.loads(line) for line in reversed(lines) if line.strip()]
    for email in emails:
        email["date"] = timezone.datetime.fromisoformat(email["date"])
    return emails
