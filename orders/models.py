from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from catalog.models import money
from core.images import delete_files, process_image_field, upload_to, validate_image_size


class OrderQuerySet(models.QuerySet):
    def with_tracking_problem(self):
        """Orders on their way whose courier reports a problem, or that could not be tracked."""
        return self.filter(status__in=Order.ON_THE_WAY).filter(
            models.Q(tracking_status__in=Order.COURIER_PROBLEMS) | ~models.Q(tracking_error=""))


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Request received")
        AWAITING_PAYMENT = "awaiting_payment", _("Accepted – awaiting payment")
        PAID = "paid", _("Paid – being prepared")
        SHIPPED = "shipped", _("Shipped")
        IN_TRANSIT = "in_transit", _("In transit")
        DELIVERED = "delivered", _("Delivered")
        REJECTED = "rejected", _("Declined")
        CANCELLED = "cancelled", _("Cancelled")

    class CourierStatus(models.TextChoices):
        """Parcel stages as reported by 17TRACK."""
        NOT_FOUND = "NotFound", _("No information from the courier yet")
        INFO_RECEIVED = "InfoReceived", _("Courier has the details, parcel not picked up yet")
        IN_TRANSIT = "InTransit", _("In transit")
        OUT_FOR_DELIVERY = "OutForDelivery", _("Out for delivery")
        AVAILABLE_FOR_PICKUP = "AvailableForPickup", _("Ready for pickup")
        DELIVERY_FAILURE = "DeliveryFailure", _("Delivery attempt failed")
        DELIVERED = "Delivered", _("Delivered")
        EXCEPTION = "Exception", _("Problem with the shipment")
        EXPIRED = "Expired", _("No courier updates for a long time")

    # Courier stages you are emailed about, since you may need to contact the customer or courier.
    COURIER_PROBLEMS = [CourierStatus.DELIVERY_FAILURE, CourierStatus.EXCEPTION, CourierStatus.EXPIRED]

    OPEN_STATUSES = [Status.PENDING, Status.AWAITING_PAYMENT, Status.PAID, Status.SHIPPED, Status.IN_TRANSIT]
    ON_THE_WAY = [Status.SHIPPED, Status.IN_TRANSIT]
    # The happy path, used to draw the progress bar for customers.
    FLOW = [Status.PENDING, Status.AWAITING_PAYMENT, Status.PAID, Status.SHIPPED, Status.IN_TRANSIT,
            Status.DELIVERED]

    number = models.CharField(_("order no."), max_length=20, unique=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("customer"), related_name="orders", on_delete=models.PROTECT
    )
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)

    is_business = models.BooleanField(_("business order"), default=False)
    prices_include_vat = models.BooleanField(
        _("prices include VAT"), default=True,
        help_text=_("Ticked for individuals. Unticked for businesses: VAT is added on top of the item prices."),
    )
    vat_rate = models.DecimalField(_("VAT rate %"), max_digits=4, decimal_places=2, default=Decimal("24.00"),
                                   help_text=_("Set to 0 for EU businesses with a valid VAT number (reverse charge)."))

    full_name = models.CharField(_("full name"), max_length=150)
    email = models.EmailField(_("email"))
    phone = models.CharField(_("phone"), max_length=40)
    company_name = models.CharField(_("company name"), max_length=150, blank=True)
    vat_number = models.CharField(_("VAT number"), max_length=30, blank=True)
    tax_office = models.CharField(_("tax office"), max_length=100, blank=True)
    address = models.CharField(_("street address"), max_length=200)
    city = models.CharField(_("city"), max_length=100)
    postal_code = models.CharField(_("postal code"), max_length=20)
    country = models.CharField(_("country"), max_length=100)
    customer_notes = models.TextField(_("customer notes"), blank=True)

    shipping_cost = models.DecimalField(
        _("shipping cost (€)"), max_digits=8, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
        help_text=_("Same basis as the item prices (incl. VAT for individuals, excl. VAT for businesses)."),
    )
    tracking_number = models.CharField(_("tracking number"), max_length=100, blank=True)
    tracking_url = models.URLField(_("tracking link"), blank=True)
    # Filled in by automatic tracking (orders/tracking.py).
    tracking_registered = models.CharField(max_length=100, blank=True, editable=False)
    tracking_status = models.CharField(_("courier status"), max_length=30, blank=True, editable=False,
                                       choices=CourierStatus.choices)
    tracking_event = models.CharField(_("latest courier update"), max_length=300, blank=True, editable=False)
    tracking_event_at = models.DateTimeField(null=True, blank=True, editable=False)
    tracking_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    tracking_error = models.CharField(max_length=300, blank=True, editable=False)
    internal_notes = models.TextField(_("private notes"), blank=True, help_text=_("Only you can see these."))
    # Delivered orders leave the admin's order list but are kept, and still show up in searches.
    archived_at = models.DateTimeField(_("archived"), null=True, blank=True, editable=False, db_index=True)

    custom_request = models.OneToOneField(
        "CustomRequest", verbose_name=_("custom request"), related_name="order",
        on_delete=models.SET_NULL, null=True, blank=True,
    )
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("order")
        verbose_name_plural = _("orders")

    def __str__(self):
        return self.number or f"#{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"ORD-{1000 + self.pk}"
            super().save(update_fields=["number"])

    def get_absolute_url(self):
        return reverse("orders:order_detail", args=[self.number])

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def can_cancel(self):
        return self.status == self.Status.PENDING

    # --- Totals ------------------------------------------------------------
    @property
    def items_total(self):
        return money(sum((item.line_total for item in self.items.all()), Decimal("0")))

    @property
    def subtotal(self):
        return self.items_total + self.shipping_cost

    @property
    def net_total(self):
        if self.prices_include_vat:
            return money(self.subtotal / (1 + self.vat_rate / 100))
        return self.subtotal

    @property
    def vat_amount(self):
        if self.prices_include_vat:
            return self.subtotal - self.net_total
        return money(self.subtotal * self.vat_rate / 100)

    @property
    def total(self):
        return self.subtotal if self.prices_include_vat else self.net_total + self.vat_amount


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(
        "catalog.Product", verbose_name=_("product"), on_delete=models.SET_NULL, null=True, blank=True
    )
    product_name = models.CharField(_("description"), max_length=250)
    quantity = models.PositiveIntegerField(_("quantity"), default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(_("unit price (€)"), max_digits=10, decimal_places=2,
                                     validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = _("item")
        verbose_name_plural = _("items")

    def __str__(self):
        return f"{self.quantity} × {self.product_name}"

    @property
    def line_total(self):
        return money((self.unit_price or 0) * (self.quantity or 0))


class OrderEvent(models.Model):
    """History of every status change, shown to the customer as a timeline."""

    order = models.ForeignKey(Order, related_name="events", on_delete=models.CASCADE)
    status = models.CharField(_("status"), max_length=20, choices=Order.Status.choices)
    note = models.TextField(_("note to customer"), blank=True)
    emailed = models.BooleanField(_("email sent"), default=False)
    created_at = models.DateTimeField(_("date"), auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("status change")
        verbose_name_plural = _("status history")

    def __str__(self):
        return self.get_status_display()


class CustomRequest(models.Model):
    """A customer asks for an icon that is not in the shop."""

    class Status(models.TextChoices):
        NEW = "new", _("Received")
        DISCUSSION = "discussion", _("In discussion")
        QUOTED = "quoted", _("Offer sent")
        ACCEPTED = "accepted", _("Offer accepted")
        DECLINED = "declined", _("Declined")
        CLOSED = "closed", _("Closed")

    number = models.CharField(_("request no."), max_length=20, unique=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("customer"), related_name="custom_requests", on_delete=models.PROTECT
    )
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.NEW)

    subject = models.CharField(_("which icon"), max_length=200,
                               help_text=_("e.g. Saint Nicholas, Panagia Glykofilousa, a family patron saint…"))
    description = models.TextField(_("details"))
    dimensions = models.CharField(_("preferred size"), max_length=100, blank=True)
    quantity = models.PositiveIntegerField(_("quantity"), default=1, validators=[MinValueValidator(1)])
    budget = models.CharField(_("budget"), max_length=100, blank=True)
    needed_by = models.DateField(_("needed by"), null=True, blank=True)

    is_business = models.BooleanField(_("business request"), default=False)
    quoted_price = models.DecimalField(
        _("offer price (€)"), max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Total for all pieces. Incl. VAT for individuals, excl. VAT for businesses."),
    )
    quoted_shipping = models.DecimalField(_("offer shipping (€)"), max_digits=8, decimal_places=2,
                                          default=Decimal("0.00"), validators=[MinValueValidator(0)])
    quote_note = models.TextField(_("offer details"), blank=True,
                                  help_text=_("Shown with the offer: materials, delivery time, etc."))
    internal_notes = models.TextField(_("private notes"), blank=True)

    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated"), auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("custom request")
        verbose_name_plural = _("custom requests")

    def __str__(self):
        return f"{self.number} – {self.subject}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"REQ-{1000 + self.pk}"
            super().save(update_fields=["number"])

    def get_absolute_url(self):
        return reverse("orders:request_detail", args=[self.number])

    @property
    def quote_total(self):
        if self.quoted_price is None:
            return None
        return self.quoted_price + self.quoted_shipping

    @property
    def can_respond_to_quote(self):
        return self.status == self.Status.QUOTED and self.quoted_price is not None

    @property
    def is_open(self):
        return self.status in (self.Status.NEW, self.Status.DISCUSSION, self.Status.QUOTED)


class CustomRequestImage(models.Model):
    request = models.ForeignKey(CustomRequest, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(_("image"), upload_to=upload_to("requests"), validators=[validate_image_size])
    thumb = models.ImageField(upload_to=upload_to("requests/thumbs"), blank=True, editable=False)

    class Meta:
        verbose_name = _("reference image")
        verbose_name_plural = _("reference images")

    def save(self, *args, **kwargs):
        process_image_field(self, "image", "thumb")
        super().save(*args, **kwargs)

    @property
    def thumb_url(self):
        return (self.thumb or self.image).url


class Message(models.Model):
    """Conversation between you and a customer about an order or a custom request."""

    order = models.ForeignKey(Order, related_name="messages", on_delete=models.CASCADE, null=True, blank=True)
    custom_request = models.ForeignKey(
        CustomRequest, related_name="messages", on_delete=models.CASCADE, null=True, blank=True
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    from_shop = models.BooleanField(_("from the shop"), default=False)
    body = models.TextField(_("message"))
    read_at = models.DateTimeField(_("read"), null=True, blank=True)
    created_at = models.DateTimeField(_("date"), auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(order__isnull=False) | models.Q(custom_request__isnull=False),
                name="message_has_subject",
            )
        ]

    def __str__(self):
        return self.body[:60]

    @property
    def thread(self):
        return self.order or self.custom_request


def unread_messages(user):
    """Shop replies the customer has not opened yet."""
    return Message.objects.filter(from_shop=True, read_at__isnull=True).filter(
        models.Q(order__user=user) | models.Q(custom_request__user=user)
    )


@receiver(post_delete, sender=CustomRequestImage)
def _delete_request_image_files(sender, instance, **kwargs):
    delete_files(instance.image, instance.thumb)
