from django.core.management.base import BaseCommand, CommandError

from orders import tracking
from orders.couriers import CourierError


class Command(BaseCommand):
    help = "Ask a courier about one voucher, to check its API details: check_courier acs 7400123456"

    def add_arguments(self, parser):
        parser.add_argument("courier", choices=[str(c) for c in tracking.COURIERS])
        parser.add_argument("voucher")

    def handle(self, *args, courier, voucher, **options):
        module = tracking.COURIERS[courier]
        if not module.enabled():
            raise CommandError(f"The {courier} API details are not set in .env (see .env.example).")
        try:
            update = module.track(tracking.clean_number(voucher))
        except CourierError as error:
            raise CommandError(str(error))
        self.stdout.write(f"Stage: {update.stage}")
        self.stdout.write(f"Latest update: {update.description or '-'} ({update.when or 'no date'})")
