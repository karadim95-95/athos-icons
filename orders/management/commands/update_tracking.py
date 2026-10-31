from django.core.management.base import BaseCommand

from orders import tracking


class Command(BaseCommand):
    help = "Ask ACS and Geniki Taxydromiki about shipped orders and mark them In transit / Delivered."

    def handle(self, *args, **options):
        if not tracking.enabled():
            self.stdout.write("Automatic tracking is off: set the ACS and/or Geniki Taxydromiki API details.")
            return
        self.stdout.write(f"{tracking.sync()} order(s) updated.")
