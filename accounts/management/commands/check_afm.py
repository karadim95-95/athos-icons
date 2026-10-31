from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from accounts import vat


class Command(BaseCommand):
    help = "Check a Greek ΑΦΜ or EU VAT number the way sign-up does (tests your AADE special codes)."

    def add_arguments(self, parser):
        parser.add_argument("number")

    def handle(self, *args, number, **options):
        if not vat.aade_enabled():
            self.stdout.write("AADE codes are not set: only the check digit is verified for Greek numbers.")
        try:
            result = vat.check(number)
        except ValidationError as error:
            self.stdout.write(f"Rejected: {' '.join(error.messages)}")
            return
        self.stdout.write(f"Accepted: {result.number}")
        if result.registry:
            self.stdout.write(result.registry)
