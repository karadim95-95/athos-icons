from django.contrib import admin
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from .models import Page, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fieldsets = [
        (_("Shop"), {"fields": ["shop_name", ("tagline", "tagline_en"), ("announcement", "announcement_en"),
                                "hero_image"]}),
        (_("Contact"), {"fields": ["contact_email", "phone", "address", "business_hours",
                                   "facebook_url", "instagram_url"]}),
        (_("Prices & shipping"), {"fields": ["vat_rate", "shipping_cost", "free_shipping_over"]}),
        (_("Bank transfer details (sent in the payment email)"), {
            "fields": ["bank_name", "account_holder", "iban", "bic", "extra_bank_accounts", "payment_days"]}),
    ]

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect("admin:core_sitesettings_change", SiteSettings.load().pk)


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "show_in_footer", "order"]
    list_editable = ["show_in_footer", "order"]
    prepopulated_fields = {"slug": ["title_en"]}
    fields = ["title", "title_en", "slug", "body", "body_en", "show_in_footer", "order"]
