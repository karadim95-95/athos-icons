from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = "Delete customer accounts whose email was never confirmed (fake sign-ups, typos)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Keep accounts younger than this (default 7).")

    def handle(self, *args, days, **options):
        stale = User.objects.filter(
            email_verified=False, is_staff=False, date_joined__lt=timezone.now() - timedelta(days=days),
            orders__isnull=True, custom_requests__isnull=True,
        )
        count, _ = User.objects.filter(pk__in=list(stale.values_list("pk", flat=True))).delete()
        self.stdout.write(f"{count} unconfirmed account(s) deleted.")
