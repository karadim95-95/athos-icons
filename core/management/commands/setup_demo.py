"""
python manage.py setup_demo   → demo products plus demo accounts and orders, for the downloadable demo.

Every account's password is "demo1234". Run it on an empty database only.
"""

from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand

from accounts.models import User
from catalog.models import Product
from catalog.pricing import price_info
from core.models import SiteSettings
from orders import emails, services
from orders.couriers import acs, geniki
from orders.models import CustomRequest, Order

PASSWORD = "demo1234"
ADDRESS = {"address": "Ερμού 10", "city": "Αθήνα", "postal_code": "10563", "country": "Ελλάδα"}


class Command(BaseCommand):
    help = "Create demo products, accounts (password demo1234) and orders in every stage."

    def handle(self, *args, **options):
        if User.objects.filter(email="admin@demo.gr").exists():
            self.stdout.write("Demo data already exists.")
            return
        call_command("setup_shop", demo=True, stdout=self.stdout)
        site = SiteSettings.load()
        site.contact_email = "shop@demo.gr"
        site.phone = "210 000 0000"
        site.bank_name = "Τράπεζα Πειραιώς (demo)"
        site.account_holder = "Athos Icons"
        site.iban = "GR16 0110 1250 0000 0001 2300 695"
        site.facebook_url = "https://www.facebook.com/"
        site.instagram_url = "https://www.instagram.com/"
        site.youtube_url = "https://www.youtube.com/"
        site.save()

        admin = User.objects.create_superuser("admin@demo.gr", PASSWORD, first_name="Admin")
        maria = User.objects.create_user(
            "maria@demo.gr", PASSWORD, first_name="Μαρία", last_name="Παπαδοπούλου", phone="6900000000",
            email_verified=True, **ADDRESS)
        church = User.objects.create_user(
            "church@demo.gr", PASSWORD, first_name="Νίκος", last_name="Οικονόμου", phone="6911111111",
            account_type=User.AccountType.BUSINESS, company_name="Ιερός Ναός Αγίου Νικολάου",
            vat_number="123456783", tax_office="Α' Αθηνών", business_approved=True, email_verified=True, **ADDRESS)
        User.objects.create_user(
            "newshop@demo.gr", PASSWORD, first_name="Ελένη", last_name="Γεωργίου", phone="6922222222",
            account_type=User.AccountType.BUSINESS, company_name="Βιβλιοπωλείο Η Ορθοδοξία",
            vat_number="987654324", tax_office="Θεσσαλονίκης", email_verified=True)

        products = list(Product.objects.filter(is_available=True)[:4])

        def order(user, items, *statuses, **extra):
            lines = [{"product": p, "quantity": q, "price": price_info(p, user)} for p, q in items]
            address = {"full_name": user.get_full_name(), "email": user.email, "phone": user.phone,
                       "company_name": user.company_name, "vat_number": user.vat_number,
                       "tax_office": user.tax_office, **ADDRESS}
            new = services.create_order(user, lines, address)
            for field, value in extra.items():
                setattr(new, field, value)
            new.save()
            for status in statuses:
                services.set_order_status(new, status)
            return new

        order(maria, [(products[0], 1)])  # waiting for you to accept it
        order(maria, [(products[1], 1), (products[2], 2)], Order.Status.AWAITING_PAYMENT)
        order(maria, [(products[3], 1)], Order.Status.AWAITING_PAYMENT, Order.Status.PAID, Order.Status.SHIPPED,
              courier=Order.Courier.ACS, tracking_number="7400123456",
              tracking_url=acs.TRACKING_URL.format("7400123456"))
        order(church, [(products[0], 10)], Order.Status.AWAITING_PAYMENT, Order.Status.PAID,
              Order.Status.SHIPPED, Order.Status.DELIVERED, courier=Order.Courier.GENIKI,
              tracking_number="4012345678", tracking_url=geniki.TRACKING_URL.format("4012345678"))

        request = services.create_custom_request(maria, {
            "subject": "Άγιος Σπυρίδων", "description": "Εικόνα με χρυσό φόντο για δώρο βάπτισης.",
            "dimensions": "25 x 35 εκ.", "quantity": 1, "budget": "έως 250 €",
        }, [])
        services.post_shop_message(request, admin, "Καλησπέρα! Θα θέλατε και επιγραφή με το όνομα του παιδιού;")
        services.post_customer_message(request, maria, "Ναι, «Σπυρίδων» στο κάτω μέρος, ευχαριστώ!")
        request.quoted_price = Decimal("220.00")
        request.quoted_shipping = Decimal("6.00")
        request.quote_note = "Χειροποίητη, σε ξύλο με φύλλο χρυσού. Παράδοση σε 3 εβδομάδες."
        request.status = CustomRequest.Status.QUOTED
        request.save()
        emails.request_status(request)

        self.stdout.write("Demo accounts created (password demo1234): admin@demo.gr, maria@demo.gr, "
                          "church@demo.gr, newshop@demo.gr")
