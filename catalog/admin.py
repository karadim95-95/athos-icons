from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Max
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from core.forms import MultipleImageField
from core.models import SiteSettings
from core.templatetags.shop import euro

from .models import (Category, Material, Product, ProductImage, ProductSize, Size, Technique, Variation,
                     VariationOption, money)

DISCOUNT_BUTTONS = [10, 15, 20, 25, 30, 40, 50]


def preview(image_url, size=70):
    if not image_url:
        return "—"
    return format_html('<img src="{}" style="height:{}px;width:auto;border-radius:4px;object-fit:cover">',
                       image_url, size)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["picture", "name", "name_en", "product_count", "order", "is_active"]
    list_display_links = ["picture", "name"]
    list_editable = ["order", "is_active"]
    search_fields = ["name", "name_en"]
    fields = ["name", "name_en", "description", "description_en", "image", "order", "is_active", "slug"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n=Count("products"))

    @admin.display(description=_("products"), ordering="n")
    def product_count(self, obj):
        return obj.n

    @admin.display(description=_("image"))
    def picture(self, obj):
        return preview(obj.image.url if obj.image else None, 50)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ["picture", "image", "order"]
    readonly_fields = ["picture"]

    @admin.display(description=_("preview"))
    def picture(self, obj):
        return preview(obj.thumb_url if obj.pk else None, 90)


@admin.register(Material, Technique)
class OptionAdmin(admin.ModelAdmin):
    list_display = ["name", "name_en", "order"]
    list_editable = ["name_en", "order"]
    search_fields = ["name", "name_en"]


class VariationOptionInline(admin.TabularInline):
    model = VariationOption
    extra = 1
    fields = ["name", "name_en", "price_change", "order"]


@admin.register(Variation)
class VariationAdmin(admin.ModelAdmin):
    list_display = ["name", "name_en", "option_list", "order"]
    list_editable = ["order"]
    inlines = [VariationOptionInline]

    @admin.display(description=_("options"))
    def option_list(self, obj):
        return ", ".join(f"{o.name} ({o.price_change_label})" if o.price_change else o.name for o in obj.options.all())


@admin.register(Size)
class SizeAdmin(admin.ModelAdmin):
    list_display = ["name", "order"]
    list_editable = ["order"]


def _net_field(gross_id, vat_multiplier, label):
    """An "excl. VAT" box that stays in step with its "incl. VAT" partner (static/js/admin_product.js)."""
    return forms.DecimalField(label=label, required=False, max_digits=10, decimal_places=2, min_value=0,
                              widget=forms.NumberInput(attrs={"step": "0.01", "data-net-of": gross_id,
                                                              "data-vat": str(vat_multiplier)}))


class ProductAdminForm(forms.ModelForm):
    """Price incl. or excl. VAT (fill either), sizes with their own prices, one-click discounts."""

    new_images = MultipleImageField(
        label=_("Add photos"), required=False, max_files=20,
        help_text=_("Select one or more photos from your computer or phone. "
                    "They are resized automatically. Reorder or delete them in the list below."),
    )
    sizes = []  # the Size list, set by ProductAdmin.get_form, which also adds a row of fields per size

    class Meta:
        model = Product
        fields = "__all__"
        widgets = {"materials": forms.CheckboxSelectMultiple, "techniques": forms.CheckboxSelectMultiple,
                   "options": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vat = SiteSettings.load().vat_multiplier
        self.fields["price"].required = False
        for name, model in [("materials", Material), ("techniques", Technique)]:
            self.fields[name].help_text = format_html(
                '{} <a href="{}">{}</a>', _("Click the ones that apply."),
                reverse(f"admin:catalog_{model._meta.model_name}_changelist"), _("Add or rename"))
        # Options grouped by choice, each showing how it changes the price.
        groups = {}
        for option in VariationOption.objects.select_related("variation"):
            label = f"{option.name} ({option.price_change_label})" if option.price_change else option.name
            groups.setdefault(option.variation.name, []).append((option.pk, label))
        self.fields["options"].choices = list(groups.items())
        self.fields["options"].help_text = format_html(
            '{} <a href="{}">{}</a>',
            _("Click every option you make this icon in. The customer picks exactly one of each choice "
              "(flat or carved, rectangular or oval…); the first one is pre-selected and the percentage "
              "is added to the size's price."),
            reverse("admin:catalog_variation_changelist"), _("Add or change the choices and percentages"))
        if self.instance.pk:
            self.fields["price_net"].initial = money(self.instance.price / self.vat)
        self.fields["discount_percent"].help_text = format_html(
            '<span class="discount-buttons">{}{}</span><span class="discount-preview" data-discount-preview="{}"></span>',
            format_html_join("", '<button type="button" data-discount="{}">−{}%</button>',
                             ((d, d) for d in DISCOUNT_BUTTONS)),
            format_html('<button type="button" data-discount="0">{}</button>', _("no discount")),
            _("Customers pay"),
        )
        offered = {option.size_id: option.price for option in self.instance.sizes.all()} if self.instance.pk else {}
        for size in self.sizes:
            if size.pk in offered:
                self.fields[f"size_{size.pk}"].initial = True
                self.fields[f"size_price_{size.pk}"].initial = offered[size.pk]
                self.fields[f"size_net_{size.pk}"].initial = money(offered[size.pk] / self.vat)

    def _gross(self, data, gross_name, net_name):
        """The price incl. VAT, worked out from the excl. VAT box if that is the one that was filled in."""
        gross, net = data.get(gross_name), data.get(net_name)
        if net is not None and (gross is None or (net_name in self.changed_data and gross_name not in self.changed_data)):
            return money(net * self.vat)
        return gross

    def clean(self):
        data = super().clean()
        data["price"] = self._gross(data, "price", "price_net")
        self.chosen_sizes = {}
        for size in self.sizes:
            if not data.get(f"size_{size.pk}"):
                continue
            price = self._gross(data, f"size_price_{size.pk}", f"size_net_{size.pk}")
            if price is None:
                price = data["price"]
            if price is None:
                self.add_error(f"size_price_{size.pk}", _("Enter the price for this size."))
            else:
                self.chosen_sizes[size] = price
        if self.chosen_sizes:
            data["price"] = min(self.chosen_sizes.values())
            if data.get("business_price") is not None:
                self.add_error("business_price", _("Leave this empty for icons with sizes: businesses pay each "
                                                   "size's price without VAT."))
        elif data["price"] is None:
            self.add_error("price", _("Enter the price incl. or excl. VAT."))
        return data


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    inlines = [ProductImageInline]
    list_display = ["picture", "name", "category", "size_list", "price", "discount_percent", "final_price",
                    "business_price", "is_active", "is_available", "is_featured"]
    list_display_links = ["picture", "name"]
    list_editable = ["price", "discount_percent", "is_active", "is_available", "is_featured"]
    list_filter = ["category", "materials", "techniques", "is_active", "is_available", "is_featured"]
    search_fields = ["name", "name_en", "description"]
    list_per_page = 50
    actions = ["duplicate", *[f"discount_{d}" for d in (10, 20, 30, 50)], "remove_discount",
               "mark_unavailable", "mark_available", "watermark_photos"]
    fieldsets = [
        (None, {"fields": ["name", "name_en", "category", "description", "description_en", "dimensions",
                           "materials", "techniques"]}),
        (_("Price"), {"fields": [("price", "price_net"), "discount_percent", "business_price"],
                      "description": _("Fill in the price incl. or excl. VAT: the other is calculated. Individuals "
                                       "pay the price incl. VAT; approved businesses see prices excl. VAT.")}),
        (_("Customer choices"), {"fields": ["options"]}),
        (_("Photos"), {"fields": ["new_images"]}),
        (_("Visibility"), {"fields": [("is_active", "is_available", "is_featured"), "slug"]}),
    ]

    class Media:
        js = ["js/admin_product.js"]
        css = {"all": ["css/admin_product.css"]}

    def get_queryset(self, request):
        return (super().get_queryset(request).select_related("category")
                .prefetch_related("images", "sizes__size"))

    def get_form(self, request, obj=None, change=False, **kwargs):
        # One row per size you defined: tick it, and give its price incl. or excl. VAT.
        sizes = list(Size.objects.all())
        vat = SiteSettings.load().vat_multiplier
        fields = {"sizes": sizes, "price_net": _net_field("id_price", vat, _("price excl. VAT (€)"))}
        for size in sizes:
            fields[f"size_{size.pk}"] = forms.BooleanField(
                label=size.name, required=False,
                widget=forms.CheckboxInput(attrs={"data-size-price": f"id_size_price_{size.pk}"}))
            fields[f"size_price_{size.pk}"] = forms.DecimalField(
                label=_("incl. VAT (€)"), required=False, max_digits=10, decimal_places=2, min_value=0,
                widget=forms.NumberInput(attrs={"step": "0.01"}))
            fields[f"size_net_{size.pk}"] = _net_field(f"id_size_price_{size.pk}", vat, _("excl. VAT (€)"))
        kwargs["form"] = type("ProductAdminForm", (ProductAdminForm,), fields)
        return super().get_form(request, obj, change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        sizes = list(Size.objects.all())
        rows = [(f"size_{s.pk}", f"size_price_{s.pk}", f"size_net_{s.pk}") for s in sizes]
        link = reverse("admin:catalog_size_changelist")
        description = format_html(
            "{} <a href=\"{}\">{}</a>",
            _("Tick the sizes this icon comes in and give each one its price (empty = the price above)."),
            link, _("Add or rename sizes"),
        ) if sizes else format_html("{} <a href=\"{}\">{}</a>", _("No sizes defined yet."), link, _("Add sizes"))
        fieldsets = list(super().get_fieldsets(request, obj))
        fieldsets.insert(2, (_("Sizes"), {"fields": rows, "description": description, "classes": ["sizes"]}))
        return fieldsets

    @admin.display(description=_("sizes"))
    def size_list(self, obj):
        return ", ".join(option.size.name for option in obj.size_options) or "—"

    @admin.display(description=_("photo"))
    def picture(self, obj):
        image = obj.main_image
        return preview(image.thumb_url if image else None, 50)

    def changelist_view(self, request, extra_context=None):
        self._vat_multiplier = SiteSettings.load().vat_multiplier
        return super().changelist_view(request, extra_context)

    @admin.display(description=_("final price"))
    def final_price(self, obj):
        return euro(obj.price_for(False, self._vat_multiplier))

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        product = form.instance
        product.save(update_fields=["search_text"])  # now that the materials and techniques are saved
        for size, price in form.chosen_sizes.items():
            ProductSize.objects.update_or_create(product=product, size=size, defaults={"price": price})
        product.sizes.exclude(size__in=list(form.chosen_sizes)).delete()
        start = (product.images.aggregate(m=Max("order"))["m"] or 0) + 1
        for i, image in enumerate(form.cleaned_data.get("new_images") or []):
            ProductImage.objects.create(product=product, image=image, order=start + i)

    @admin.action(description=_("Duplicate"))
    def duplicate(self, request, queryset):
        for product in queryset:
            materials, techniques = list(product.materials.all()), list(product.techniques.all())
            options = list(product.options.all())
            sizes = list(product.sizes.all())
            product.pk = None
            product.slug = ""
            product.name = f"{product.name} ({_('copy')})"
            product.is_active = False
            product.save()
            product.materials.set(materials)
            product.techniques.set(techniques)
            product.options.set(options)
            ProductSize.objects.bulk_create(ProductSize(product=product, size=s.size, price=s.price) for s in sizes)
        self.message_user(request, _("Copies created as hidden products. Edit them, add photos and make them visible."),
                          messages.SUCCESS)

    def _discount(self, request, queryset, percent):
        n = queryset.update(discount_percent=percent)
        self.message_user(request, ngettext("%d product updated.", "%d products updated.", n) % n, messages.SUCCESS)

    @admin.action(description=format_lazy(_("Discount {}%%"), 10))
    def discount_10(self, request, queryset):
        self._discount(request, queryset, 10)

    @admin.action(description=format_lazy(_("Discount {}%%"), 20))
    def discount_20(self, request, queryset):
        self._discount(request, queryset, 20)

    @admin.action(description=format_lazy(_("Discount {}%%"), 30))
    def discount_30(self, request, queryset):
        self._discount(request, queryset, 30)

    @admin.action(description=format_lazy(_("Discount {}%%"), 50))
    def discount_50(self, request, queryset):
        self._discount(request, queryset, 50)

    @admin.action(description=_("Remove discount"))
    def remove_discount(self, request, queryset):
        self._discount(request, queryset, 0)

    @admin.action(description=_("Mark as sold out"))
    def mark_unavailable(self, request, queryset):
        n = queryset.update(is_available=False)
        self.message_user(request, ngettext("%d product updated.", "%d products updated.", n) % n)

    @admin.action(description=_("Mark as available"))
    def mark_available(self, request, queryset):
        n = queryset.update(is_available=True)
        self.message_user(request, ngettext("%d product updated.", "%d products updated.", n) % n)

    @admin.action(description=_("Add the watermark to their photos"))
    def watermark_photos(self, request, queryset):
        if not SiteSettings.load().watermark_photos:
            self.message_user(request, _("Watermarks are switched off in the shop settings."), messages.ERROR)
            return
        photos = ProductImage.objects.filter(product__in=queryset, watermarked=False)
        for photo in photos:
            photo.add_watermark()
        n = len(photos)
        self.message_user(request, ngettext("%d photo watermarked.", "%d photos watermarked.", n) % n
                          if n else _("Their photos already have the watermark."), messages.SUCCESS)
