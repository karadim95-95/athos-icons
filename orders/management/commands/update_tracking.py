from django.core.management.base import BaseCommand, CommandError

from orders import tracking


class Command(BaseCommand):
    help = "Ask the couriers (through 17TRACK) about shipped orders and mark them In transit / Delivered."

    def handle(self, *args, **options):
        if not tracking.enabled():
            self.stdout.write("Automatic tracking is off: set TRACKING_API_KEY to turn it on.")
            return
        try:
            moved = tracking.sync()
        except tracking.TrackingError as error:
            raise CommandError(f"17TRACK: {error}")
        self.stdout.write(f"{moved} order(s) updated.")
