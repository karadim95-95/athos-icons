from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Max
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from core.forms import MultipleImageField
from core.models import SiteSettings
from core.templatetags.shop import euro

from .models import Category, Product, ProductImage


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


class ProductAdminForm(forms.ModelForm):
    new_images = MultipleImageField(
        label=_("Add photos"), required=False, max_files=20,
        help_text=_("Select one or more photos from your computer or phone. "
                    "They are resized automatically. Reorder or delete them in the list below."),
    )

    class Meta:
        model = Product
        fields = "__all__"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    inlines = [ProductImageInline]
    list_display = ["picture", "name", "category", "price", "discount_percent", "final_price",
                    "business_price", "is_active", "is_available", "is_featured"]
    list_display_links = ["picture", "name"]
    list_editable = ["price", "discount_percent", "is_active", "is_available", "is_featured"]
    list_filter = ["category", "is_active", "is_available", "is_featured"]
    search_fields = ["name", "name_en", "description"]
    list_per_page = 50
    actions = ["duplicate", "remove_discount", "mark_unavailable", "mark_available"]
    fieldsets = [
        (None, {"fields": ["name", "name_en", "category", "description", "description_en",
                           ("dimensions", "materials")]}),
        (_("Price"), {"fields": [("price", "discount_percent"), "business_price"],
                      "description": _("Individuals pay the price incl. VAT. Approved businesses see "
                                       "prices excl. VAT (the business price, or the retail price minus VAT).")}),
        (_("Photos"), {"fields": ["new_images"]}),
        (_("Visibility"), {"fields": [("is_active", "is_available", "is_featured"), "slug"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category").prefetch_related("images")

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
        start = (product.images.aggregate(m=Max("order"))["m"] or 0) + 1
        for i, image in enumerate(form.cleaned_data.get("new_images") or []):
            ProductImage.objects.create(product=product, image=image, order=start + i)

    @admin.action(description=_("Duplicate (e.g. to create another size)"))
    def duplicate(self, request, queryset):
        for product in queryset:
            product.pk = None
            product.slug = ""
            product.name = f"{product.name} ({_('copy')})"
            product.is_active = False
            product.save()
        self.message_user(request, _("Copies created as hidden products. Edit them, add photos and make them visible."),
                          messages.SUCCESS)

    @admin.action(description=_("Remove discount"))
    def remove_discount(self, request, queryset):
        queryset.update(discount_percent=0)

    @admin.action(description=_("Mark as sold out"))
    def mark_unavailable(self, request, queryset):
        n = queryset.update(is_available=False)
        self.message_user(request, ngettext("%d product updated.", "%d products updated.", n) % n)

    @admin.action(description=_("Mark as available"))
    def mark_available(self, request, queryset):
        n = queryset.update(is_available=True)
        self.message_user(request, ngettext("%d product updated.", "%d products updated.", n) % n)
