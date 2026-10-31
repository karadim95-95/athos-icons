import math
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.images import delete_files, process_image_field, upload_to, validate_image_size
from core.models import localized

CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def normalize_search(text):
    """Lowercase and strip accents, so 'παναγια' finds 'Παναγία'."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def unique_slug(instance, text):
    base = slugify(text, allow_unicode=True)[:80] or "item"
    slug, n = base, 2
    model = type(instance)
    while model.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


class Category(models.Model):
    name = models.CharField(_("name (Greek)"), max_length=120)
    name_en = models.CharField(_("name (English)"), max_length=120, blank=True)
    slug = models.SlugField(_("address"), max_length=140, unique=True, blank=True, allow_unicode=True)
    description = models.TextField(_("description (Greek)"), blank=True)
    description_en = models.TextField(_("description (English)"), blank=True)
    image = models.ImageField(_("image"), upload_to=upload_to("categories"), blank=True, validators=[validate_image_size])
    order = models.PositiveSmallIntegerField(_("position"), default=0)
    is_active = models.BooleanField(_("visible"), default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = _("category")
        verbose_name_plural = _("categories")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name_en or self.name)
        process_image_field(self, "image")
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:category", args=[self.slug])

    @property
    def local_name(self):
        return localized(self, "name")

    @property
    def local_description(self):
        return localized(self, "description")


class ProductQuerySet(models.QuerySet):
    def visible(self):
        return (self.filter(is_active=True)
                .filter(models.Q(category__isnull=True) | models.Q(category__is_active=True))
                .prefetch_related("sizes__size", "options__variation"))


class Option(models.Model):
    """An item of a list you choose from on each product (materials, techniques), in Greek and English."""

    name = models.CharField(_("name (Greek)"), max_length=80, unique=True)
    name_en = models.CharField(_("name (English)"), max_length=80, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        abstract = True
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    @property
    def local_name(self):
        return localized(self, "name")


class Material(Option):
    class Meta(Option.Meta):
        verbose_name = _("material")
        verbose_name_plural = _("materials")


class Technique(Option):
    class Meta(Option.Meta):
        verbose_name = _("technique")
        verbose_name_plural = _("techniques")


class Variation(models.Model):
    """Something customers choose on the product page, e.g. Type (flat or carved) or Shape."""

    name = models.CharField(_("name (Greek)"), max_length=60, unique=True)
    name_en = models.CharField(_("name (English)"), max_length=60, blank=True)
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = _("customer choice")
        verbose_name_plural = _("customer choices")

    def __str__(self):
        return self.name

    @property
    def local_name(self):
        return localized(self, "name")


class VariationOption(models.Model):
    """One of the options of a choice, e.g. "Carved", which can change the price by a percentage."""

    variation = models.ForeignKey(Variation, verbose_name=_("customer choice"), related_name="options",
                                  on_delete=models.CASCADE)
    name = models.CharField(_("name (Greek)"), max_length=60)
    name_en = models.CharField(_("name (English)"), max_length=60, blank=True)
    price_change = models.DecimalField(
        _("price change %"), max_digits=5, decimal_places=1, default=Decimal("0"),
        validators=[MinValueValidator(-90), MaxValueValidator(500)],
        help_text=_("e.g. 30 for 30% more than the size's price; 0 for the basic version."),
    )
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["variation__order", "variation__id", "order", "id"]
        constraints = [models.UniqueConstraint(fields=["variation", "name"], name="unique_option_name")]
        verbose_name = _("option")
        verbose_name_plural = _("options")

    def __str__(self):
        return f"{self.variation}: {self.name}"

    @property
    def local_name(self):
        return localized(self, "name")

    @property
    def factor(self):
        return 1 + Decimal(self.price_change) / 100

    @property
    def price_change_label(self):
        """"+30%", "−10%", or "" for no change."""
        change = Decimal(self.price_change).normalize()
        return f"{change:+f}%".replace("-", "−") if change else ""


class Size(models.Model):
    """A size you offer, e.g. 20 x 26 cm. On each product you tick the sizes it comes in."""

    name = models.CharField(_("size"), max_length=50, unique=True, help_text=_("e.g. 20 x 26 cm"))
    order = models.PositiveIntegerField(_("position"), default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = _("size")
        verbose_name_plural = _("sizes")

    def __str__(self):
        return self.name


class Product(models.Model):
    category = models.ForeignKey(
        Category, verbose_name=_("category"), related_name="products", on_delete=models.SET_NULL, null=True, blank=True
    )
    name = models.CharField(_("name (Greek)"), max_length=200)
    name_en = models.CharField(_("name (English)"), max_length=200, blank=True)
    slug = models.SlugField(_("address"), max_length=220, unique=True, blank=True, allow_unicode=True)
    description = models.TextField(_("description (Greek)"), blank=True)
    description_en = models.TextField(_("description (English)"), blank=True)
    dimensions = models.CharField(_("dimensions"), max_length=100, blank=True,
                                  help_text=_("e.g. 20 x 26 cm. For icons that come in one size only; "
                                              "otherwise tick the sizes below."))
    materials = models.ManyToManyField(Material, verbose_name=_("materials"), related_name="products", blank=True)
    techniques = models.ManyToManyField(Technique, verbose_name=_("technique"), related_name="products",
                                        blank=True)
    options = models.ManyToManyField(VariationOption, verbose_name=_("customer choices"), related_name="products",
                                     blank=True)

    price = models.DecimalField(
        _("price incl. VAT (€)"), max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
        help_text=_("Retail price that individuals pay, VAT included. "
                    "With sizes, it is set to the lowest size price."),
    )
    discount_percent = models.PositiveSmallIntegerField(
        _("discount %"), default=0, validators=[MaxValueValidator(90)],
        help_text=_("0 = no discount. Applies to individuals and businesses."),
    )
    business_price = models.DecimalField(
        _("business price excl. VAT (€)"), max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Optional wholesale price. If empty, businesses see the retail price without VAT. "
                    "Not used with sizes: businesses then pay each size's price without VAT."),
    )

    is_active = models.BooleanField(_("visible in shop"), default=True)
    is_available = models.BooleanField(
        _("available to order"), default=True,
        help_text=_("Untick when sold out. The product stays visible but cannot be added to the cart."),
    )
    is_featured = models.BooleanField(_("show on homepage"), default=False)
    search_text = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(_("created"), auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("product")
        verbose_name_plural = _("products")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name_en or self.name)
        options = [o for o in [*self.materials.all(), *self.techniques.all()]] if self.pk else []
        self.search_text = normalize_search(" ".join([
            self.name, self.name_en, self.description, self.description_en,
            self.category.name if self.category else "", self.category.name_en if self.category else "",
            *[name for o in options for name in (o.name, o.name_en)],
        ]))
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:product", args=[self.slug])

    @property
    def local_name(self):
        return localized(self, "name")

    @property
    def local_description(self):
        return localized(self, "description")

    @property
    def main_image(self):
        images = list(self.images.all())  # uses prefetch_related when present
        return images[0] if images else None

    @property
    def size_options(self):
        return list(self.sizes.all())  # uses prefetch_related when present

    @property
    def option_groups(self):
        """The choices customers make on this product: [(variation, [its options offered here])]."""
        groups = {}
        for option in self.options.all():  # uses prefetch_related when present
            groups.setdefault(option.variation, []).append(option)
        return sorted(groups.items(), key=lambda group: (group[0].order, group[0].pk))

    # --- Pricing -----------------------------------------------------------
    # Individuals: price includes VAT. Approved businesses: price excludes VAT.
    # `size` is one of the product's ProductSize rows; `options` the customer's choices (e.g. carved, arched),
    # each changing the price by its percentage.
    def _apply_discount(self, amount):
        if self.discount_percent:
            amount = amount * (100 - self.discount_percent) / 100
        return money(amount)

    def base_price_for(self, business, vat_multiplier, size=None, options=()):
        factor = math.prod((option.factor for option in options), start=Decimal(1))
        gross = (size.price if size else self.price) * factor
        if business:
            if size is None and self.business_price is not None:
                return money(self.business_price * factor)
            return money(gross / vat_multiplier)
        return money(gross)

    def price_for(self, business, vat_multiplier, size=None, options=()):
        return self._apply_discount(self.base_price_for(business, vat_multiplier, size, options))


class ProductSize(models.Model):
    """A size a product comes in, with its own price."""

    product = models.ForeignKey(Product, related_name="sizes", on_delete=models.CASCADE)
    size = models.ForeignKey(Size, verbose_name=_("size"), related_name="+", on_delete=models.PROTECT)
    price = models.DecimalField(_("price incl. VAT (€)"), max_digits=10, decimal_places=2,
                                validators=[MinValueValidator(0)])

    class Meta:
        ordering = ["size__order", "size__id"]
        constraints = [models.UniqueConstraint(fields=["product", "size"], name="unique_product_size")]
        verbose_name = _("size")
        verbose_name_plural = _("sizes")

    def __str__(self):
        return f"{self.product} – {self.size}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(_("image"), upload_to=upload_to("products"), validators=[validate_image_size])
    thumb = models.ImageField(upload_to=upload_to("products/thumbs"), blank=True, editable=False)
    order = models.PositiveSmallIntegerField(_("position"), default=0, help_text=_("The lowest number is the main photo."))

    class Meta:
        ordering = ["order", "id"]
        verbose_name = _("image")
        verbose_name_plural = _("images")

    def __str__(self):
        return f"{self.product} #{self.order}"

    def save(self, *args, **kwargs):
        process_image_field(self, "image", "thumb")
        super().save(*args, **kwargs)

    @property
    def thumb_url(self):
        return (self.thumb or self.image).url


@receiver(post_delete, sender=ProductImage)
def _delete_product_image_files(sender, instance, **kwargs):
    delete_files(instance.image, instance.thumb)
