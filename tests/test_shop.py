import re
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

from unittest import mock
from urllib.error import URLError
from urllib.parse import urlsplit

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts import vat
from accounts.models import User
from catalog.models import Category, Product, ProductImage
from core import antispam
from core.models import SiteSettings
from orders import services, tracking
from orders.models import CustomRequest, Order

MEDIA = tempfile.mkdtemp()


def jpeg(size=(3000, 2000), name="photo.jpg"):
    buf = BytesIO()
    Image.new("RGB", size, "#c9a24f").save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA, LANGUAGE_CODE="en",
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ShopTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        site = SiteSettings.load()
        site.iban = "GR1601101250000000012300695"
        site.bank_name = "Test Bank"
        site.contact_email = "owner@example.com"
        site.shipping_cost = Decimal("5.00")
        site.save()
        self.category = Category.objects.create(name="Εικόνες Παναγίας", name_en="Theotokos")
        self.product = Product.objects.create(name="Παναγία Γλυκοφιλούσα", name_en="Panagia Glykofilousa",
                                              category=self.category, price=Decimal("62.00"))
        self.customer = User.objects.create_user("maria@example.com", "pass-12345!", first_name="Maria",
                                                 language="en", phone="6900000000", address="Odos 1",
                                                 city="Athens", postal_code="10552", country="Greece",
                                                 email_verified=True)
        self.business = User.objects.create_user(
            "shop@example.com", "pass-12345!", first_name="Nikos", language="en",
            account_type=User.AccountType.BUSINESS, company_name="Church Shop", vat_number="EL123456789",
            business_approved=True, address="Odos 2", city="Thessaloniki", postal_code="54622", country="Greece",
            email_verified=True)
        self.owner = User.objects.create_superuser("admin@example.com", "pass-12345!")

    def order_for(self, user, qty=2):
        self.client.force_login(user)
        self.client.post(reverse("orders:cart_add", args=[self.product.pk]), {"quantity": qty})
        data = {"full_name": "Test Person", "phone": "690", "address": "Odos 1", "city": "Athens",
                "postal_code": "10552", "country": "Greece", "company_name": "Church Shop", "notes": ""}
        self.client.post(reverse("orders:checkout"), data)
        return Order.objects.filter(user=user).latest("created_at")

    def admin_post(self, order, follow=False, **changes):
        item = order.items.get()
        event_ids = list(order.events.values_list("id", flat=True))
        data = {
            "status": order.status, "notify_customer": "on", "status_note": "", "tracking_number": "",
            "tracking_url": "", "full_name": order.full_name, "email": order.email, "phone": order.phone,
            "company_name": "", "vat_number": "", "tax_office": "", "address": order.address, "city": order.city,
            "postal_code": order.postal_code, "country": order.country, "shipping_cost": str(order.shipping_cost),
            "prices_include_vat": "on", "vat_rate": str(order.vat_rate), "reply": "", "internal_notes": "",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "1", "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000", "items-0-id": item.pk, "items-0-order": order.pk,
            "items-0-product_name": item.product_name, "items-0-product": item.product_id,
            "items-0-quantity": item.quantity, "items-0-unit_price": str(item.unit_price),
            "events-TOTAL_FORMS": str(len(event_ids)), "events-INITIAL_FORMS": str(len(event_ids)),
            "events-MIN_NUM_FORMS": "0", "events-MAX_NUM_FORMS": "1000",
        }
        for i, pk in enumerate(event_ids):
            data[f"events-{i}-id"] = pk
            data[f"events-{i}-order"] = order.pk
        data.update(changes)
        self.client.force_login(self.owner)
        return self.client.post(reverse("admin:orders_order_change", args=[order.pk]), data, follow=follow)


class PricingTests(ShopTestCase):


    def test_individual_pays_retail_with_discount(self):
        self.product.discount_percent = 10
        self.assertEqual(self.product.price_for(False, Decimal("1.24")), Decimal("55.80"))

    def test_business_sees_price_without_vat(self):
        self.assertEqual(self.product.price_for(True, Decimal("1.24")), Decimal("50.00"))

    def test_business_price_override(self):
        self.product.business_price = Decimal("40.00")
        self.product.discount_percent = 0
        self.assertEqual(self.product.price_for(True, Decimal("1.24")), Decimal("40.00"))

    def test_unapproved_business_sees_retail_prices(self):
        self.business.business_approved = False
        self.business.save()
        self.client.force_login(self.business)
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, "62.00")
        self.assertNotContains(response, "excl. VAT</span>")

    def test_greek_search_ignores_accents_and_case(self):
        response = self.client.get(reverse("catalog:products"), {"q": "γλυκοφιλουσα"})
        self.assertContains(response, "Panagia Glykofilousa")


class CheckoutTests(ShopTestCase):
    def test_checkout_requires_login(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.pk]))
        response = self.client.get(reverse("orders:checkout"))
        self.assertRedirects(response, reverse("accounts:login") + "?next=" + reverse("orders:checkout"))

    def test_order_request_created_and_both_sides_notified(self):
        order = self.order_for(self.customer)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.items_total, Decimal("124.00"))
        self.assertEqual(order.shipping_cost, Decimal("5.00"))
        self.assertEqual(order.total, Decimal("129.00"))
        self.assertTrue(order.prices_include_vat)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn("maria@example.com", recipients)
        self.assertIn("owner@example.com", recipients)
        self.assertEqual(len(self.client.session.get("cart", {})), 0)

    def test_business_order_adds_vat_on_top(self):
        order = self.order_for(self.business)
        self.assertFalse(order.prices_include_vat)
        self.assertEqual(order.items_total, Decimal("100.00"))   # 2 × 50.00 net
        self.assertEqual(order.shipping_cost, Decimal("4.03"))    # 5.00 / 1.24
        self.assertEqual(order.vat_amount, Decimal("24.97"))
        self.assertEqual(order.total, Decimal("129.00"))

    def test_free_shipping_threshold(self):
        site = SiteSettings.load()
        site.free_shipping_over = Decimal("100")
        site.save()
        self.assertEqual(self.order_for(self.customer).shipping_cost, Decimal("0.00"))

    def test_customers_cannot_see_each_others_orders(self):
        order = self.order_for(self.customer)
        self.client.force_login(self.business)
        self.assertEqual(self.client.get(order.get_absolute_url()).status_code, 404)

    def test_customer_can_cancel_pending_order(self):
        order = self.order_for(self.customer)
        self.client.post(reverse("orders:order_cancel", args=[order.number]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_sold_out_product_cannot_be_added(self):
        self.product.is_available = False
        self.product.save()
        response = self.client.post(reverse("orders:cart_add", args=[self.product.pk]))
        self.assertEqual(response.status_code, 404)


class AdminWorkflowTests(ShopTestCase):
    def test_accepting_sends_payment_email_with_updated_price(self):
        order = self.order_for(self.customer)
        mail.outbox.clear()
        response = self.admin_post(order, status=Order.Status.AWAITING_PAYMENT, **{"items-0-unit_price": "50.00"})
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", {}).get("errors"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(order.total, Decimal("105.00"))
        email = mail.outbox[0]
        self.assertEqual(email.to, ["maria@example.com"])
        self.assertIn("GR1601101250000000012300695", email.body)
        self.assertIn("105.00", email.body)
        self.assertIn(order.number, email.subject)
        self.assertEqual(order.events.last().status, Order.Status.AWAITING_PAYMENT)

    def test_shipping_requires_tracking(self):
        order = self.order_for(self.customer)
        services.set_order_status(order, Order.Status.PAID)
        response = self.admin_post(order, status=Order.Status.SHIPPED)
        self.assertEqual(response.status_code, 200)  # form shown again with an error
        response = self.admin_post(order, status=Order.Status.SHIPPED, tracking_number="ACS123")
        self.assertEqual(response.status_code, 302)
        self.assertIn("ACS123", mail.outbox[-1].body)

    def test_reply_from_admin_reaches_customer(self):
        order = self.order_for(self.customer)
        mail.outbox.clear()
        self.admin_post(order, reply="Would you like gift wrapping?")
        self.assertEqual(order.messages.get().from_shop, True)
        self.assertIn("gift wrapping", mail.outbox[0].body)
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "1 new message")

    def test_admin_dashboard_loads(self):
        self.order_for(self.customer)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "New order requests to review")
        for name in ["catalog_product_add", "orders_order_changelist", "orders_customrequest_changelist",
                     "accounts_user_changelist", "core_sitesettings_changelist"]:
            self.assertIn(self.client.get(reverse(f"admin:{name}"), follow=True).status_code, [200])

    def test_admin_can_upload_several_photos_at_once(self):
        self.client.force_login(self.owner)
        data = {"name": "Άγιος Νικόλαος", "price": "40.00", "discount_percent": "0", "is_active": "on",
                "is_available": "on", "new_images": [jpeg(name="a.jpg"), jpeg(name="b.jpg")],
                "images-TOTAL_FORMS": "0", "images-INITIAL_FORMS": "0", "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000"}
        response = self.client.post(reverse("admin:catalog_product_add"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Product.objects.get(name="Άγιος Νικόλαος").images.count(), 2)


class CustomRequestTests(ShopTestCase):
    def test_full_custom_request_flow(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse("orders:custom_request"), {
            "subject": "Saint Spyridon", "description": "Gold background", "quantity": 1,
            "images": [jpeg()],
        })
        req = CustomRequest.objects.get()
        self.assertRedirects(response, req.get_absolute_url())
        self.assertEqual(req.images.count(), 1)

        req.quoted_price = Decimal("180.00")
        req.quoted_shipping = Decimal("6.00")
        req.status = CustomRequest.Status.QUOTED
        req.save()
        mail.outbox.clear()

        response = self.client.post(reverse("orders:quote_accept", args=[req.number]), {
            "full_name": "Maria P", "phone": "690", "address": "Odos 1", "city": "Athens",
            "postal_code": "10552", "country": "Greece",
        })
        order = Order.objects.get(custom_request=req)
        self.assertRedirects(response, order.get_absolute_url())
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertEqual(order.total, Decimal("186.00"))
        self.assertTrue(any("GR1601101250000000012300695" in m.body for m in mail.outbox))
        req.refresh_from_db()
        self.assertEqual(req.status, CustomRequest.Status.ACCEPTED)

    def test_anonymous_visitor_sees_intro(self):
        response = self.client.get(reverse("orders:custom_request"))
        self.assertContains(response, "Create an account")


def signup_data(**changes):
    return {
        "account_type": "consumer", "first_name": "Eleni", "last_name": "K", "email": "Eleni@Example.com",
        "phone": "690", "password1": "a-Long-pass-77", "password2": "a-Long-pass-77", "accept_terms": "on",
        "form_ts": antispam.form_timestamp(seconds_ago=10), "website": "", **changes,
    }


def emailed_link(message):
    return urlsplit(re.search(r"https?://\S+", message.body).group(0)).path


class AccountTests(ShopTestCase):
    def test_business_registration_waits_for_approval(self):
        response = self.client.post(reverse("accounts:register"), signup_data(
            account_type="business", company_name="Monastery Shop", vat_number="123456783", tax_office="Α' Αθηνών"))
        self.assertRedirects(response, reverse("accounts:verify_email"))
        user = User.objects.get(email="eleni@example.com")
        self.assertTrue(user.business_pending)
        self.assertFalse(user.is_business)
        self.assertEqual([m.to for m in mail.outbox], [["eleni@example.com"]])  # only the confirmation link
        self.client.get(emailed_link(mail.outbox[0]))
        self.assertIn(["owner@example.com"], [m.to for m in mail.outbox])  # you hear about it once confirmed

    def test_login_with_email(self):
        response = self.client.post(reverse("accounts:login"), {"username": "MARIA@example.com",
                                                                "password": "pass-12345!"})
        self.assertRedirects(response, reverse("accounts:dashboard"))


class ImageTests(ShopTestCase):
    def test_upload_is_resized_and_thumbnailed(self):
        image = ProductImage.objects.create(product=self.product, image=jpeg((4000, 3000)))
        with Image.open(image.image.path) as full, Image.open(image.thumb.path) as thumb:
            self.assertLessEqual(max(full.size), 1800)
            self.assertLessEqual(max(thumb.size), 640)



class ArchiveTests(ShopTestCase):
    def delivered_order(self):
        order = self.order_for(self.customer)
        for status in [Order.Status.PAID, Order.Status.SHIPPED]:
            services.set_order_status(order, status, notify=False)
        mail.outbox.clear()
        response = self.admin_post(order, status=Order.Status.DELIVERED, tracking_number="ACS1", follow=True)
        self.assertContains(response, "moved to the archive")
        order.refresh_from_db()
        return order

    def listed(self, query=""):
        response = self.client.get(reverse("admin:orders_order_changelist") + query)
        return [o.number for o in response.context["cl"].result_list]

    def test_delivered_order_is_archived_but_can_be_found(self):
        order = self.delivered_order()
        self.assertIsNotNone(order.archived_at)
        self.assertEqual([m.to for m in mail.outbox], [["maria@example.com"]])  # you set it yourself
        self.assertNotIn(order.number, self.listed())
        self.assertIn(order.number, self.listed("?archive=archived"))
        self.assertIn(order.number, self.listed(f"?q={order.number}"))
        self.assertIn(order.number, self.listed("?q=Test+Person"))
        self.assertIn(order.number, self.listed("?status__exact=delivered"))
        self.assertIn(order.number, self.listed(f"?user__id__exact={self.customer.pk}"))
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(reverse("orders:order_list")), order.number)

    def test_reopening_brings_the_order_back(self):
        order = self.delivered_order()
        self.admin_post(order, status=Order.Status.SHIPPED, tracking_number="ACS1")
        order.refresh_from_db()
        self.assertIsNone(order.archived_at)
        self.assertIn(order.number, self.listed())

    def test_archive_by_hand(self):
        order = self.order_for(self.customer)
        services.cancel_by_customer(order)
        self.client.force_login(self.owner)
        url = reverse("admin:orders_order_changelist")
        self.client.post(url, {"action": "archive", "_selected_action": [order.pk]})
        self.assertNotIn(order.number, self.listed())
        self.client.post(url + "?archive=archived", {"action": "unarchive", "_selected_action": [order.pk]})
        self.assertIn(order.number, self.listed())

class FakeTrack:
    """Stands in for the 17TRACK API: register numbers, then report whatever stage we set."""

    def __init__(self):
        self.registered = {}
        self.stage = {}

    def __call__(self, endpoint, items):
        if endpoint == "register":
            accepted, rejected = [], []
            for item in items:
                if item["number"] == "UNKNOWN1":
                    rejected.append({"number": item["number"], "error": {"code": -18019903, "message": "x"}})
                else:
                    self.registered[item["number"]] = item.get("carrier")
                    accepted.append(item)
            return {"accepted": accepted, "rejected": rejected}
        return {"accepted": [
            {"number": item["number"], "track_info": {
                "latest_status": {"status": self.stage.get(item["number"], ("NotFound", ""))[0]},
                "latest_event": {"description": self.stage.get(item["number"], ("", ""))[1], "location": "ATHENS",
                                 "time_utc": "2026-11-02T08:15:00Z"}}}
            for item in items
        ], "rejected": []}


@override_settings(TRACKING_API_KEY="test-key")
class TrackingTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeTrack()
        patcher = mock.patch.object(tracking, "_call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def shipped_order(self, number="7400 1234 567", url="https://www.acscourier.net/el/track?n=74001234567"):
        order = self.order_for(self.customer)
        services.set_order_status(order, Order.Status.PAID)
        response = self.admin_post(order, status=Order.Status.SHIPPED, tracking_number=number, tracking_url=url)
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        return order

    def update(self):
        mail.outbox.clear()
        call_command("update_tracking", stdout=StringIO())

    def test_courier_recognised_from_link(self):
        self.assertEqual(tracking.carrier_for("https://www.acscourier.net/el/track?n=1"), 100070)
        self.assertEqual(tracking.carrier_for("https://www.elta-courier.gr/search?br=1"), 100179)
        self.assertEqual(tracking.carrier_for("https://www.courier.gr/track/result?tracknr=1"), 100667)
        self.assertEqual(tracking.carrier_for("https://boxnow.gr/el/track?id=1"), 101008)
        self.assertIsNone(tracking.carrier_for("https://parcels.example.com/1"))
        self.assertIsNone(tracking.carrier_for(""))

    def test_order_follows_the_courier_until_delivered(self):
        order = self.shipped_order()
        self.assertEqual(self.fake.registered, {"74001234567": 100070})
        self.assertEqual(order.tracking_registered, "74001234567")

        self.fake.stage["74001234567"] = ("InTransit", "Αναχώρηση από κέντρο διαλογής")
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_TRANSIT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("in transit", mail.outbox[0].subject)
        self.assertIn("Αναχώρηση από κέντρο διαλογής – ATHENS", mail.outbox[0].body)

        self.update()  # nothing new: no second email
        self.assertEqual(len(mail.outbox), 0)

        self.fake.stage["74001234567"] = ("OutForDelivery", "Σε διανομή")
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_TRANSIT)
        self.assertEqual(len(mail.outbox), 0)
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(order.get_absolute_url()), "Out for delivery")

        self.fake.stage["74001234567"] = ("Delivered", "Παραδόθηκε")
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual([m.to for m in mail.outbox], [["maria@example.com"], ["owner@example.com"]])
        self.assertIn("delivered", mail.outbox[0].subject)
        self.assertIn("was delivered", mail.outbox[1].subject)
        self.assertIsNotNone(order.archived_at)
        self.assertEqual([e.status for e in order.events.all()][-3:],
                         [Order.Status.SHIPPED, Order.Status.IN_TRANSIT, Order.Status.DELIVERED])

    def test_delivery_problem_emails_the_shop_once(self):
        order = self.shipped_order()
        self.fake.stage["74001234567"] = ("DeliveryFailure", "Ο παραλήπτης απουσίαζε")
        self.update()
        self.assertEqual([m.to for m in mail.outbox], [["owner@example.com"]])
        self.assertIn(order.number, mail.outbox[0].subject)
        self.assertIn("Delivery attempt failed", mail.outbox[0].body)
        self.update()
        self.assertEqual(len(mail.outbox), 0)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:index")), "Shipments with a delivery problem")
        response = self.client.get(reverse("admin:orders_order_changelist") + "?tracking=problem")
        self.assertContains(response, order.number)

    def test_unrecognised_number_is_shown_to_the_owner(self):
        order = self.order_for(self.customer)
        services.set_order_status(order, Order.Status.PAID)
        response = self.admin_post(order, status=Order.Status.SHIPPED, tracking_number="UNKNOWN1", follow=True)
        self.assertContains(response, "courier could not be recognised")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SHIPPED)
        self.assertTrue(Order.objects.with_tracking_problem().filter(pk=order.pk).exists())

    @override_settings(TRACKING_API_KEY="")
    def test_off_without_api_key(self):
        order = self.shipped_order()
        self.assertEqual(self.fake.registered, {})
        self.assertEqual(order.status, Order.Status.SHIPPED)
        out = StringIO()
        call_command("update_tracking", stdout=out)
        self.assertIn("off", out.getvalue())


class AccessTests(ShopTestCase):
    """Only customers with a confirmed email can order; bots are kept out."""

    def add_to_cart(self):
        self.client.post(reverse("orders:cart_add", args=[self.product.pk]), {"quantity": 1})

    def test_admin_account_cannot_order(self):
        self.client.force_login(self.owner)
        self.add_to_cart()
        response = self.client.get(reverse("orders:checkout"), follow=True)
        self.assertRedirects(response, reverse("core:home"))
        self.assertContains(response, "admin account, which cannot place orders")
        self.assertContains(response, "You are logged in as the shop admin")
        self.client.post(reverse("orders:checkout"), {"full_name": "X"})
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.client.get(reverse("orders:custom_request")).status_code, 302)

    def test_anonymous_visitor_must_log_in(self):
        self.add_to_cart()
        response = self.client.get(reverse("orders:checkout"))
        self.assertRedirects(response, reverse("accounts:login") + "?next=" + reverse("orders:checkout"))

    def test_new_account_must_confirm_email_before_ordering(self):
        self.add_to_cart()
        response = self.client.post(reverse("accounts:register"), signup_data())
        self.assertRedirects(response, reverse("accounts:verify_email"))
        user = User.objects.get(email="eleni@example.com")
        self.assertFalse(user.email_verified)
        self.assertRedirects(self.client.get(reverse("orders:checkout")), reverse("accounts:verify_email"))
        self.assertRedirects(self.client.get(reverse("orders:custom_request")), reverse("accounts:verify_email"))

        link = emailed_link(mail.outbox[0])
        response = self.client.get(link)
        self.assertRedirects(response, reverse("orders:cart"))  # back to the waiting cart
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertEqual(self.client.get(reverse("orders:checkout")).status_code, 200)
        self.assertContains(self.client.get(link, follow=True), "already confirmed")

    def test_confirmation_link_logs_in_on_another_device(self):
        self.client.post(reverse("accounts:register"), signup_data())
        self.client.logout()
        response = self.client.get(emailed_link(mail.outbox[0]))
        self.assertRedirects(response, reverse("accounts:dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get(email="eleni@example.com").pk)

    def test_bot_signups_are_rejected(self):
        for data in [signup_data(website="http://spam.example"),            # filled in the hidden field
                     signup_data(form_ts=antispam.form_timestamp()),         # sent instantly
                     signup_data(form_ts="forged")]:                         # no valid timestamp
            response = self.client.post(reverse("accounts:register"), data)
            self.assertContains(response, "could not confirm that you are a person")
        self.assertFalse(User.objects.filter(email="eleni@example.com").exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_signups_are_limited_per_connection(self):
        for i in range(5):
            self.client.post(reverse("accounts:register"), signup_data(email=f"bot{i}@example.com"))
            self.client.logout()
        response = self.client.post(reverse("accounts:register"), signup_data(email="bot6@example.com"))
        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(email="bot6@example.com").exists())

    def test_login_pauses_after_repeated_failures(self):
        for _ in range(10):
            self.client.post(reverse("accounts:login"), {"username": "maria@example.com", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "maria@example.com",
                                                                "password": "pass-12345!"})
        self.assertContains(response, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_login_pauses_after_repeated_failures(self):
        url = reverse("admin:login")
        for _ in range(10):
            self.client.post(url, {"username": "admin@example.com", "password": "wrong"})
        response = self.client.post(url, {"username": "ADMIN@example.com", "password": "pass-12345!"})
        self.assertContains(response, "Too many failed attempts")
        cache.clear()
        response = self.client.post(url, {"username": "ADMIN@example.com", "password": "pass-12345!"})
        self.assertEqual(response.status_code, 302)

    def test_contact_form_blocks_bots_and_floods(self):
        data = {"name": "Anna", "email": "anna@example.com", "message": "Hello",
                "form_ts": antispam.form_timestamp(seconds_ago=10)}
        self.client.post(reverse("core:contact"), {**data, "website": "spam"})
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse("core:contact"), data)
        self.assertEqual(len(mail.outbox), 1)
        for _ in range(3):
            self.client.post(reverse("core:contact"), data)
        self.assertEqual(self.client.post(reverse("core:contact"), data).status_code, 429)

    def test_password_reset_needs_the_human_check(self):
        url = reverse("accounts:password_reset")
        self.client.post(url, {"email": "maria@example.com"})
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(url, {"email": "maria@example.com", "form_ts": antispam.form_timestamp(seconds_ago=10)})
        self.assertEqual(len(mail.outbox), 1)

    def test_customer_message_limit(self):
        order = self.order_for(self.customer)
        for _ in range(30):
            antispam.record_attempt("message", self.customer.pk, 3600)
        response = self.client.post(order.get_absolute_url(), {"body": "one more"}, follow=True)
        self.assertContains(response, "reached the limit")
        self.assertFalse(order.messages.exists())

    @override_settings(TURNSTILE_SITE_KEY="site", TURNSTILE_SECRET_KEY="secret")
    def test_turnstile_check_when_configured(self):
        self.assertContains(self.client.get(reverse("accounts:register")), 'data-sitekey="site"')
        self.client.post(reverse("accounts:register"), signup_data())
        self.assertFalse(User.objects.filter(email="eleni@example.com").exists())
        with mock.patch.object(antispam, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = BytesIO(b'{"success": true}')
            self.client.post(reverse("accounts:register"), signup_data(**{"cf-turnstile-response": "token"}))
        self.assertTrue(User.objects.filter(email="eleni@example.com").exists())

    def test_unconfirmed_accounts_are_cleaned_up(self):
        old = timezone.now() - timedelta(days=8)
        stale = User.objects.create_user("stale@example.com", "x-pass-1234")
        fresh = User.objects.create_user("fresh@example.com", "x-pass-1234")
        User.objects.filter(pk__in=[stale.pk, self.customer.pk]).update(date_joined=old)
        call_command("delete_unverified_accounts", stdout=StringIO())
        self.assertFalse(User.objects.filter(pk=stale.pk).exists())
        self.assertEqual(User.objects.filter(pk__in=[fresh.pk, self.customer.pk, self.owner.pk]).count(), 3)


def aade_answer(error_code="", flag="1"):
    basic = "" if error_code else (
        "<basic_rec><afm>123456783</afm><doy>1104</doy><doy_descr>Δ' ΑΘΗΝΩΝ</doy_descr>"
        f"<deactivation_flag>{flag}</deactivation_flag><deactivation_flag_descr>ΕΝΕΡΓΟΣ ΑΦΜ</deactivation_flag_descr>"
        "<onomasia>ΙΕΡΑ ΜΟΝΗ ΑΓΙΟΥ ΝΙΚΟΛΑΟΥ</onomasia><commer_title/><postal_address>ΕΡΜΟΥ</postal_address>"
        "<postal_address_no>10</postal_address_no><postal_zip_code>10563</postal_zip_code>"
        "<postal_area_description>ΑΘΗΝΑ</postal_area_description></basic_rec>"
        "<firm_act_tab><item><firm_act_code>47781</firm_act_code><firm_act_descr>ΛΙΑΝΙΚΟ ΕΜΠΟΡΙΟ ΕΙΚΟΝΩΝ"
        "</firm_act_descr><firm_act_kind>1</firm_act_kind><firm_act_kind_descr>ΚΥΡΙΑ</firm_act_kind_descr>"
        "</item></firm_act_tab>")
    return ('<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"><env:Body>'
            '<srvc:rgWsPublic2AfmMethodResponse xmlns="http://rgwspublic2/RgWsPublic2" '
            'xmlns:srvc="http://rgwspublic2/RgWsPublic2Service"><srvc:result><rg_ws_public2_result_rtType>'
            f"<call_seq_id>1</call_seq_id><error_rec><error_code>{error_code}</error_code><error_descr>x"
            f"</error_descr></error_rec>{basic}</rg_ws_public2_result_rtType></srvc:result>"
            "</srvc:rgWsPublic2AfmMethodResponse></env:Body></env:Envelope>")


def fake_registries(aade=None, vies=None):
    """Stand-in for urlopen: answers like AADE / VIES would (or raises the given exception)."""
    sent = []

    def opener(request, timeout=None):
        sent.append(request.data.decode())
        answer = aade if "gsis.gr" in request.full_url else vies
        if isinstance(answer, Exception):
            raise answer
        response = mock.MagicMock()
        response.__enter__.return_value = BytesIO(answer.encode())
        return response

    opener.sent = sent
    return opener


class VatNumberTests(ShopTestCase):
    def business_signup(self, **changes):
        data = signup_data(account_type="business", company_name="Monastery Shop", vat_number="EL 123 456 783",
                           tax_office="Α Αθηνών")
        return self.client.post(reverse("accounts:register"), {**data, **changes})

    def test_check_digit(self):
        self.assertTrue(vat.afm_checksum_ok("123456783"))
        self.assertFalse(vat.afm_checksum_ok("123456789"))
        self.assertFalse(vat.afm_checksum_ok("000000000"))
        self.assertFalse(vat.afm_checksum_ok("12345678"))
        self.assertEqual(vat.split("el 123.456.783"), ("EL", "123456783"))
        self.assertEqual(vat.split("GR123456783"), ("EL", "123456783"))
        self.assertEqual(vat.split("cy 12345678x"), ("CY", "12345678X"))

    def test_business_needs_valid_afm_and_tax_office(self):
        response = self.business_signup(vat_number="", tax_office="")
        self.assertContains(response, "Required for business accounts.")
        self.assertContains(response, "Required for Greek businesses.")
        self.assertContains(self.business_signup(vat_number="123456789"), "This ΑΦΜ is not valid")
        self.assertFalse(User.objects.filter(email="eleni@example.com").exists())
        self.assertRedirects(self.business_signup(), reverse("accounts:verify_email"))
        user = User.objects.get(email="eleni@example.com")
        self.assertEqual((user.vat_number, user.tax_office), ("123456783", "Α Αθηνών"))
        self.assertIsNone(user.vat_checked_at)  # no AADE codes set: check digit only

    def test_individuals_do_not_need_afm(self):
        self.assertRedirects(self.client.post(reverse("accounts:register"), signup_data()),
                             reverse("accounts:verify_email"))

    @override_settings(AADE_USERNAME="user", AADE_PASSWORD="pass")
    def test_aade_registry_confirms_business(self):
        opener = fake_registries(aade=aade_answer())
        with mock.patch.object(vat, "urlopen", opener):
            self.assertRedirects(self.business_signup(), reverse("accounts:verify_email"))
        self.assertIn("<wss:Username>user</wss:Username>", opener.sent[0])
        self.assertIn("<rg:afm_called_for>123456783</rg:afm_called_for>", opener.sent[0])
        user = User.objects.get(email="eleni@example.com")
        self.assertEqual(user.tax_office, "Δ' ΑΘΗΝΩΝ")  # official spelling from AADE
        self.assertIn("ΙΕΡΑ ΜΟΝΗ ΑΓΙΟΥ ΝΙΚΟΛΑΟΥ", user.vat_registry)
        self.assertIn("ΛΙΑΝΙΚΟ ΕΜΠΟΡΙΟ ΕΙΚΟΝΩΝ", user.vat_registry)
        self.assertIsNotNone(user.vat_checked_at)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:accounts_user_change", args=[user.pk])),
                            "ΙΕΡΑ ΜΟΝΗ ΑΓΙΟΥ ΝΙΚΟΛΑΟΥ")

    @override_settings(AADE_USERNAME="user", AADE_PASSWORD="pass")
    def test_aade_rejects_non_business_and_inactive_numbers(self):
        with mock.patch.object(vat, "urlopen", fake_registries(aade=aade_answer("RG_WS_PUBLIC_EPIT_NF"))):
            self.assertContains(self.business_signup(), "does not belong to a business")
        cache.clear()
        with mock.patch.object(vat, "urlopen", fake_registries(aade=aade_answer(flag="2"))):
            self.assertContains(self.business_signup(), "no longer active")
        self.assertFalse(User.objects.filter(email="eleni@example.com").exists())

    @override_settings(AADE_USERNAME="user", AADE_PASSWORD="pass")
    def test_registry_problems_never_block_sign_up(self):
        for answer in [URLError("timeout"), aade_answer("RG_WS_PUBLIC_TOKEN_USERNAME_NOT_AUTHENTICATED")]:
            User.objects.filter(email="eleni@example.com").delete()
            cache.clear()
            with mock.patch.object(vat, "urlopen", fake_registries(aade=answer)):
                self.assertRedirects(self.business_signup(), reverse("accounts:verify_email"))
            self.assertIn("could not be checked", User.objects.get(email="eleni@example.com").vat_registry)

    def test_eu_businesses_are_checked_with_vies(self):
        invalid = fake_registries(vies='{"valid": false, "name": "---", "address": "---"}')
        with mock.patch.object(vat, "urlopen", invalid):
            self.assertContains(self.business_signup(vat_number="CY12345678X", tax_office=""), "EU VIES")
        valid = fake_registries(vies='{"valid": true, "name": "ICONS LTD", "address": "Nicosia"}')
        with mock.patch.object(vat, "urlopen", valid):
            self.assertRedirects(self.business_signup(vat_number="cy 12345678x", tax_office=""),
                                 reverse("accounts:verify_email"))
        user = User.objects.get(email="eleni@example.com")
        self.assertEqual(user.vat_number, "CY12345678X")
        self.assertIn("ICONS LTD", user.vat_registry)

    def test_approved_business_cannot_swap_its_afm(self):
        self.client.force_login(self.business)
        self.client.post(reverse("accounts:profile"), {
            "first_name": "Nikos", "last_name": "", "phone": "690", "language": "en", "company_name": "Church Shop",
            "vat_number": "123456783", "tax_office": "Α Αθηνών", "address": "", "city": "", "postal_code": "",
            "country": "Greece"})
        self.business.refresh_from_db()
        self.assertEqual(self.business.vat_number, "EL123456789")
        self.assertEqual(self.business.tax_office, "Α Αθηνών")

    def test_checkout_uses_the_afm_on_the_account(self):
        order = self.order_for(self.business)  # the checkout form posts no ΑΦΜ at all
        self.assertEqual(order.vat_number, "EL123456789")


class DemoTests(ShopTestCase):
    """The downloadable demo (demo/launch.py runs the shop with DEMO_MODE on)."""

    def test_demo_mailbox_shows_every_email(self):
        mailbox = Path(MEDIA) / "demo-mailbox.jsonl"
        with override_settings(DEMO_MODE=True, EMAIL_BACKEND="core.demo.DemoMailBackend", DEMO_MAILBOX=mailbox):
            self.client.post(reverse("accounts:register"), signup_data())
            response = self.client.get(reverse("core:demo_mailbox"))
        self.assertContains(response, "Confirm your email address")
        self.assertContains(response, "eleni@example.com")
        self.assertContains(response, "/account/confirm-email/")
        self.assertContains(response, "Demo version")  # the bar at the top of every page

    def test_mailbox_does_not_exist_in_the_real_shop(self):
        self.assertEqual(self.client.get(reverse("core:demo_mailbox")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("core:home")), "Demo version")

    def test_demo_data(self):
        call_command("setup_demo", stdout=StringIO())
        call_command("setup_demo", stdout=StringIO())  # second start: nothing doubled
        maria = User.objects.get(email="maria@demo.gr")
        self.assertTrue(maria.check_password("demo1234"))
        self.assertTrue(User.objects.get(email="admin@demo.gr").is_superuser)
        self.assertTrue(User.objects.get(email="church@demo.gr").is_business)
        self.assertTrue(User.objects.get(email="newshop@demo.gr").business_pending)
        self.assertEqual(sorted(maria.orders.values_list("status", flat=True)),
                         sorted([Order.Status.PENDING, Order.Status.AWAITING_PAYMENT, Order.Status.SHIPPED]))
        self.assertEqual(maria.custom_requests.get().status, CustomRequest.Status.QUOTED)
        self.assertEqual(User.objects.filter(email__endswith="@demo.gr").count(), 4)
