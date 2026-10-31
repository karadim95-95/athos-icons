import json
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

from unittest import mock
from urllib.error import HTTPError, URLError
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
from accounts.tokens import email_verification_token
from catalog.models import Category, Product, ProductImage, ProductSize, Size
from core import antispam
from core.models import SiteSettings
from orders import services, tracking
from orders.couriers import acs, geniki
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
        if qty:  # 0: check out what is already in the cart
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

ACS_SETTINGS = {"ACS_API_KEY": "acs-key", "ACS_COMPANY_ID": "company", "ACS_COMPANY_PASSWORD": "company-pass",
                "ACS_USER_ID": "user", "ACS_USER_PASSWORD": "user-pass"}
GENIKI_SETTINGS = {"GENIKI_USERNAME": "user", "GENIKI_PASSWORD": "pass", "GENIKI_APP_KEY": "app-key"}


def fake_response(body):
    response = mock.MagicMock()
    response.__enter__.return_value = BytesIO(body.encode())
    return response


class FakeACS:
    """Answers like ACS Web Services (format from the examples in ACS's specification)."""

    def __init__(self):
        self.summary = None  # None: ACS has nothing for this voucher yet
        self.checkpoints = []
        self.requests = []
        self.refuse_key = False

    def __call__(self, request, timeout=None):
        if self.refuse_key:
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        body = json.loads(request.data)
        self.requests.append((request.get_header("Acsapikey"), body))
        table = {"ACS_Trackingsummary": [self.summary] if self.summary else [],
                 "ACS_TrackingDetails": self.checkpoints}[body["ACSAlias"]]
        return fake_response(json.dumps({
            "ACSExecution_HasError": False, "ACSExecutionErrorMessage": "",
            "ACSOutputResponce": {"ACSValueOutput": [{"Error_Message": None}], "ACSTableOutput": {"Table_Data": table}},
        }))

    def checkpoint(self, action, location, when):
        self.checkpoints.append({"checkpoint_date_time": when, "checkpoint_action": action,
                                 "checkpoint_location": location, "checkpoint_notes": " "})


def acs_summary(**changes):
    return {"voucher_no": "7400123456", "pickup_date": "2026-11-02T00:00:00", "delivery_flag": 0, "returned_flag": 0,
            "non_delivery_reason_code": "", "delivery_info": "", "shipment_status": 2, **changes}


class FakeGeniki:
    """Answers like Geniki's JobServicesV2 SOAP service (names from its WSDL and specification)."""

    def __init__(self):
        self.checkpoints = []  # (status code, description, shop)
        self.status, self.delivered_at, self.result = "IN TRANSIT", "", 0
        self.expired_keys = set()
        self.logins = 0

    def __call__(self, request, timeout=None):
        body = request.data.decode()
        if "<Authenticate " in body:
            self.logins += 1
            inner = (f"<AuthenticateResponse xmlns=\"{geniki.NAMESPACE}\"><AuthenticateResult><Result>0</Result>"
                     f"<Key>key{self.logins}</Key></AuthenticateResult></AuthenticateResponse>")
        else:
            key = re.search(r"<authKey>([^<]*)</authKey>", body).group(1)
            points = "".join(
                f"<Checkpoint><Status>{text}</Status><StatusCode>{code}</StatusCode>"
                f"<StatusDate>2026-11-0{i + 2}T09:15:00</StatusDate><Shop>{shop}</Shop></Checkpoint>"
                for i, (code, text, shop) in enumerate(self.checkpoints))
            inner = (f"<TrackAndTraceResponse xmlns=\"{geniki.NAMESPACE}\"><TrackAndTraceResult>"
                     f"<Result>{11 if key in self.expired_keys else self.result}</Result>"
                     f"<Checkpoints>{points}</Checkpoints><Status>{self.status}</Status>"
                     f"<DeliveryDate>0001-01-01T00:00:00</DeliveryDate><DeliveredAt>{self.delivered_at}</DeliveredAt>"
                     "</TrackAndTraceResult></TrackAndTraceResponse>")
        return fake_response('<?xml version="1.0" encoding="utf-8"?><soap:Envelope '
                             f'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>{inner}'
                             "</soap:Body></soap:Envelope>")


@override_settings(**ACS_SETTINGS, **GENIKI_SETTINGS)
class TrackingTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        self.acs, self.geniki = FakeACS(), FakeGeniki()
        for module, fake in [(acs, self.acs), (geniki, self.geniki)]:
            patcher = mock.patch.object(module, "urlopen", fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def shipped_order(self, courier=Order.Courier.ACS, number="7400 123 456", follow=False):
        order = self.order_for(self.customer)
        services.set_order_status(order, Order.Status.PAID)
        response = self.admin_post(order, status=Order.Status.SHIPPED, courier=courier, tracking_number=number,
                                   follow=follow)
        order.refresh_from_db()
        return (order, response) if follow else order

    def update(self):
        mail.outbox.clear()
        call_command("update_tracking", stdout=StringIO())

    def test_tracking_link_is_filled_in(self):
        order = self.shipped_order()
        self.assertEqual(order.tracking_url, acs.TRACKING_URL.format("7400123456"))
        self.assertIn(order.tracking_url, mail.outbox[-1].body)  # in the "shipped" email
        order = self.shipped_order(Order.Courier.GENIKI, "4012345678")
        self.assertEqual(order.tracking_url, "https://www.taxydromiki.com/track/4012345678")

    def test_acs_order_follows_the_courier_until_delivered(self):
        order = self.shipped_order()
        api_key, request = self.acs.requests[0]
        self.assertEqual(api_key, "acs-key")
        self.assertEqual(request["ACSInputParameters"]["Voucher_No"], "7400123456")
        self.assertEqual(request["ACSInputParameters"]["Company_ID"], "company")

        self.acs.summary = acs_summary()
        self.acs.checkpoint("Αναχώρηση προς ΜΑΡΟΥΣΙ", "ΚΑΒΑΛΑ", "2026-11-02T13:05:57.263")
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_TRANSIT)
        self.assertIn("in transit", mail.outbox[0].subject)
        self.assertIn("Αναχώρηση προς ΜΑΡΟΥΣΙ – ΚΑΒΑΛΑ", mail.outbox[0].body)
        self.assertEqual(timezone.localtime(order.tracking_event_at).strftime("%d/%m %H:%M"), "02/11 13:05")

        self.acs.checkpoint("Σε διανομή", "ΜΑΡΟΥΣΙ", "2026-11-03T07:56:00.587")
        self.update()
        self.assertEqual(len(mail.outbox), 0)  # still "in transit": no email
        self.client.force_login(self.customer)
        self.assertContains(self.client.get(order.get_absolute_url()), "Out for delivery")

        self.acs.summary = acs_summary(delivery_flag=1, shipment_status=4)
        self.acs.checkpoint("Παράδοση στον παραλήπτη", "ΜΑΡΟΥΣΙ", "2026-11-03T09:21:00")
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual([m.to for m in mail.outbox], [["maria@example.com"], ["owner@example.com"]])
        self.assertIsNotNone(order.archived_at)

    def test_acs_stages(self):
        C = Order.CourierStatus
        for summary, stage in [(acs_summary(non_delivery_reason_code="AS1"), C.DELIVERY_FAILURE),
                               (acs_summary(non_delivery_reason_code="AD1"), C.AVAILABLE_FOR_PICKUP),
                               (acs_summary(non_delivery_reason_code="LS2"), C.IN_TRANSIT),
                               (acs_summary(shipment_status=6), C.EXCEPTION),
                               (acs_summary(delivery_flag=1, returned_flag=1, shipment_status=7), C.EXCEPTION),
                               (acs_summary(pickup_date=None), C.INFO_RECEIVED)]:
            with self.subTest(summary=summary):
                self.acs.summary = summary
                self.assertEqual(acs.track("7400123456").stage, stage)

    def test_geniki_order_follows_the_checkpoints(self):
        order = self.shipped_order(Order.Courier.GENIKI, "4012345678")
        self.geniki.checkpoints = [("C_W3", "Παραλαβή αποστολής", "ΑΘΗΝΑ")]
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_TRANSIT)
        self.assertIn("Παραλαβή αποστολής – ΑΘΗΝΑ", mail.outbox[0].body)

        self.geniki.checkpoints.append(("C_A3", "Σε διανομή", "ΠΕΙΡΑΙΑΣ"))
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.tracking_status, Order.CourierStatus.OUT_FOR_DELIVERY)

        self.geniki.checkpoints.append(("C_W2", "Παραδόθηκε", "ΠΕΙΡΑΙΑΣ"))
        self.geniki.status, self.geniki.delivered_at = "DELIVERED", "RECIPIENT"
        self.update()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)

    def test_geniki_failed_delivery_emails_the_shop_once(self):
        order = self.shipped_order(Order.Courier.GENIKI, "4012345678")
        self.geniki.checkpoints = [("C_W3", "Παραλαβή", "ΑΘΗΝΑ"), ("C_EA_AS", "Απών παραλήπτης", "ΠΕΙΡΑΙΑΣ")]
        self.update()
        self.assertIn(["owner@example.com"], [m.to for m in mail.outbox])
        problem = next(m for m in mail.outbox if m.to == ["owner@example.com"])
        self.assertIn(order.number, problem.subject)
        self.assertIn("Delivery attempt failed", problem.body)
        self.update()
        self.assertEqual(len(mail.outbox), 0)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:index")), "Shipments with a delivery problem")
        self.assertContains(self.client.get(reverse("admin:orders_order_changelist") + "?tracking=problem"),
                            order.number)

    def test_geniki_cancelled_checkpoints_and_returns(self):
        C = Order.CourierStatus
        self.geniki.checkpoints = [("C_W3", "Παραλαβή", "ΑΘΗΝΑ"), ("C_E1", "Επιστροφή", "ΑΘΗΝΑ"),
                                   ("C_E2", "Ακύρωση επιστροφής", "ΑΘΗΝΑ")]
        self.assertEqual(geniki.track("4012345678").stage, C.IN_TRANSIT)
        self.geniki.status, self.geniki.delivered_at = "DELIVERED", "RETURN TO SENDER"
        self.assertEqual(geniki.track("4012345678").stage, C.EXCEPTION)

    def test_geniki_logs_in_again_when_its_key_expires(self):
        geniki.track("4012345678")
        geniki.track("4012345678")
        self.assertEqual(self.geniki.logins, 1)  # the key is reused
        self.geniki.expired_keys.add("key1")
        geniki.track("4012345678")
        self.assertEqual(self.geniki.logins, 2)

    def test_unknown_voucher_is_shown_to_the_owner(self):
        self.geniki.result = 9
        order, response = self.shipped_order(Order.Courier.GENIKI, "4099999999", follow=True)
        self.assertContains(response, "does not know this voucher number")
        self.assertEqual(order.status, Order.Status.SHIPPED)
        self.assertTrue(Order.objects.with_tracking_problem().filter(pk=order.pk).exists())

    def test_wrong_api_key(self):
        self.acs.refuse_key = True
        order, response = self.shipped_order(follow=True)
        self.assertContains(response, "ACS refused the API key")

    def test_other_couriers_are_not_tracked(self):
        order = self.shipped_order(Order.Courier.OTHER, "RE123456789GR")
        self.update()
        self.assertEqual(self.acs.requests, [])
        self.assertEqual(order.tracking_url, "")

    @override_settings(ACS_API_KEY="", GENIKI_APP_KEY="")
    def test_off_without_api_details(self):
        order = self.shipped_order()
        self.assertEqual(self.acs.requests, [])
        out = StringIO()
        call_command("update_tracking", stdout=out)
        self.assertIn("off", out.getvalue())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("admin:orders_order_change", args=[order.pk])),
                            "API details are not set")


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

    def test_confirmation_link_expires_after_24_hours(self):
        self.client.post(reverse("accounts:register"), signup_data())
        link = emailed_link(mail.outbox[0])
        later = datetime.now() + timedelta(hours=24, minutes=1)
        with mock.patch.object(email_verification_token, "_now", return_value=later):
            response = self.client.get(link, follow=True)
        self.assertContains(response, "older than 24 hours")
        self.assertFalse(User.objects.get(email="eleni@example.com").email_verified)

        almost = datetime.now() + timedelta(hours=23, minutes=59)
        with mock.patch.object(email_verification_token, "_now", return_value=almost):
            self.client.get(link)
        self.assertTrue(User.objects.get(email="eleni@example.com").email_verified)

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


class ProductAdminTests(ShopTestCase):
    """Price incl. or excl. VAT, sizes with their own prices, one-click discounts (VAT 24%)."""

    def setUp(self):
        super().setUp()
        self.small = Size.objects.create(name="14 x 18 cm", order=1)
        self.large = Size.objects.create(name="30 x 40 cm", order=2)
        self.client.force_login(self.owner)

    def save_product(self, product=None, **fields):
        data = {"name": "Άγιος Νικόλαος", "price": "", "price_net": "", "discount_percent": "0",
                "is_active": "on", "is_available": "on", "images-TOTAL_FORMS": "0", "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000", **fields}
        url = (reverse("admin:catalog_product_change", args=[product.pk]) if product
               else reverse("admin:catalog_product_add"))
        return self.client.post(url, data)

    def test_price_excl_vat_fills_in_the_price_incl_vat(self):
        self.assertEqual(self.save_product(price_net="50.00").status_code, 302)
        product = Product.objects.get(name="Άγιος Νικόλαος")
        self.assertEqual(product.price, Decimal("62.00"))
        page = self.client.get(reverse("admin:catalog_product_change", args=[product.pk]))
        self.assertContains(page, 'name="price_net" value="50.00"')

    def test_price_incl_vat_wins_when_it_is_the_one_changed(self):
        self.save_product(price="62.00", price_net="50.00")
        product = Product.objects.get(name="Άγιος Νικόλαος")
        self.save_product(product, price="74.40", price_net="50.00")  # only the incl. VAT box changed
        product.refresh_from_db()
        self.assertEqual(product.price, Decimal("74.40"))

    def test_a_price_is_required(self):
        self.assertContains(self.save_product(), "Enter the price incl. or excl. VAT.")

    def test_sizes_with_their_own_prices(self):
        self.save_product(price="62.00", **{f"size_{self.small.pk}": "on", f"size_net_{self.small.pk}": "30.00",
                                            f"size_{self.large.pk}": "on"})
        product = Product.objects.get(name="Άγιος Νικόλαος")
        prices = {option.size: option.price for option in product.sizes.all()}
        self.assertEqual(prices, {self.small: Decimal("37.20"), self.large: Decimal("62.00")})
        self.assertEqual(product.price, Decimal("37.20"))  # the lowest size price, for sorting and "from"

        self.save_product(product, price="37.20", **{f"size_{self.large.pk}": "on",
                                                     f"size_price_{self.large.pk}": "80.00"})
        self.assertEqual([(o.size, o.price) for o in product.sizes.all()], [(self.large, Decimal("80.00"))])

    def test_business_price_is_not_used_with_sizes(self):
        response = self.save_product(price="62.00", business_price="40.00", **{f"size_{self.small.pk}": "on"})
        self.assertContains(response, "Leave this empty for icons with sizes")

    def test_discount_buttons_and_bulk_discount(self):
        page = self.client.get(reverse("admin:catalog_product_add"))
        self.assertContains(page, 'data-discount="20"')
        self.client.post(reverse("admin:catalog_product_changelist"),
                         {"action": "discount_20", "_selected_action": [self.product.pk]})
        self.product.refresh_from_db()
        self.assertEqual(self.product.discount_percent, 20)


class SizeShopTests(ShopTestCase):
    def setUp(self):
        super().setUp()
        small = Size.objects.create(name="14 x 18 cm", order=1)
        large = Size.objects.create(name="30 x 40 cm", order=2)
        self.small = ProductSize.objects.create(product=self.product, size=small, price=Decimal("37.20"))
        self.large = ProductSize.objects.create(product=self.product, size=large, price=Decimal("124.00"))

    def add(self, size=None, qty=1):
        data = {"quantity": qty, **({"size": size.pk} if size else {})}
        return self.client.post(reverse("orders:cart_add", args=[self.product.pk]), data, follow=True)

    def test_product_page_and_card_show_the_sizes(self):
        page = self.client.get(self.product.get_absolute_url())
        self.assertContains(page, 'name="size"')
        self.assertContains(page, "30 x 40 cm")
        self.assertContains(page, "124.00")
        listing = self.client.get(reverse("catalog:products"))
        self.assertContains(listing, "2 sizes")
        self.assertContains(listing, "37.20")  # "from" the lowest price

    def test_a_size_must_be_chosen(self):
        self.assertContains(self.add(), "Please choose a size.")
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_order_with_two_sizes(self):
        self.client.force_login(self.customer)
        self.add(self.small, 2)
        self.add(self.large, 1)
        cart = self.client.get(reverse("orders:cart"))
        self.assertContains(cart, "14 x 18 cm")
        order = self.order_for(self.customer, qty=0)
        items = {item.product_name: (item.quantity, item.unit_price) for item in order.items.all()}
        self.assertEqual(items, {"Παναγία Γλυκοφιλούσα – 14 x 18 cm": (2, Decimal("37.20")),
                                 "Παναγία Γλυκοφιλούσα – 30 x 40 cm": (1, Decimal("124.00"))})

    def test_business_pays_each_size_without_vat(self):
        self.client.force_login(self.business)
        self.add(self.large)
        order = self.order_for(self.business, qty=0)
        self.assertEqual(order.items.get().unit_price, Decimal("100.00"))

    def test_removed_size_leaves_the_cart(self):
        self.add(self.small)
        self.small.delete()
        self.assertNotContains(self.client.get(reverse("orders:cart")), "14 x 18 cm")
