from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from .images import process_image_field, upload_to, validate_image_size


def localized(obj, field):
    """Return the English version of a field when browsing in English (if filled in)."""
    if (get_language() or "").startswith("en"):
        value = getattr(obj, f"{field}_en", "")
        if value:
            return value
    return getattr(obj, field)


class SiteSettings(models.Model):
    """One row of shop-wide settings, editable from the admin."""

    shop_name = models.CharField(_("shop name"), max_length=100, default="Athos Icons")
    tagline = models.CharField(_("tagline (Greek)"), max_length=200, blank=True, default="Βυζαντινές εικόνες με παράδοση")
    tagline_en = models.CharField(_("tagline (English)"), max_length=200, blank=True, default="Byzantine icons, made with tradition")
    announcement = models.CharField(
        _("top bar message (Greek)"), max_length=200, blank=True,
        help_text=_("Short message in the thin bar at the very top, e.g. a shipping offer."),
    )
    announcement_en = models.CharField(_("top bar message (English)"), max_length=200, blank=True)
    hero_image = models.ImageField(
        _("homepage image"), upload_to=upload_to("site"), blank=True, validators=[validate_image_size]
    )

    contact_email = models.EmailField(
        _("contact email"), blank=True,
        help_text=_("Shown to customers, and where new order/request notifications are sent."),
    )
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    address = models.CharField(_("address"), max_length=200, blank=True)
    business_hours = models.CharField(_("business hours"), max_length=100, blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)

    vat_rate = models.DecimalField(
        _("VAT rate %"), max_digits=4, decimal_places=2, default=Decimal("24.00"),
        validators=[MinValueValidator(0), MaxValueValidator(50)],
    )
    shipping_cost = models.DecimalField(
        _("default shipping cost (€)"), max_digits=8, decimal_places=2, default=Decimal("0.00"),
        help_text=_("Pre-filled on every new order. You can change it per order before accepting."),
    )
    free_shipping_over = models.DecimalField(
        _("free shipping over (€)"), max_digits=8, decimal_places=2, null=True, blank=True,
        help_text=_("Leave empty for no free-shipping offer."),
    )

    bank_name = models.CharField(_("bank"), max_length=100, blank=True)
    account_holder = models.CharField(_("account holder"), max_length=150, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC / SWIFT", max_length=20, blank=True)
    extra_bank_accounts = models.TextField(
        _("other bank accounts"), blank=True, help_text=_("Optional: more banks, one per line.")
    )
    payment_days = models.PositiveSmallIntegerField(
        _("days to pay"), default=5, help_text=_("Deadline mentioned in the payment email.")
    )

    class Meta:
        verbose_name = _("shop settings")
        verbose_name_plural = _("shop settings")

    def __str__(self):
        return str(_("Shop settings"))

    def save(self, *args, **kwargs):
        self.pk = 1
        process_image_field(self, "hero_image")
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def vat_multiplier(self):
        return 1 + self.vat_rate / 100

    @property
    def notify_email(self):
        return self.contact_email or settings.SHOP_NOTIFY_EMAIL

    @property
    def local_tagline(self):
        return localized(self, "tagline")

    @property
    def local_announcement(self):
        return localized(self, "announcement")


class Page(models.Model):
    """Simple text pages: About us, Shipping, Terms, Privacy..."""

    slug = models.SlugField(_("address"), unique=True, help_text=_("Used in the link, e.g. 'about'."))
    title = models.CharField(_("title (Greek)"), max_length=150)
    title_en = models.CharField(_("title (English)"), max_length=150, blank=True)
    body = models.TextField(_("text (Greek)"), help_text=_("Plain text. Leave an empty line between paragraphs."))
    body_en = models.TextField(_("text (English)"), blank=True)
    show_in_footer = models.BooleanField(_("show in footer"), default=True)
    order = models.PositiveSmallIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = _("page")
        verbose_name_plural = _("pages")

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("core:page", args=[self.slug])

    @property
    def local_title(self):
        return localized(self, "title")

    @property
    def local_body(self):
        return localized(self, "body")
