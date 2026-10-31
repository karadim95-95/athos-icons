from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Group
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from orders import emails

from .forms import AdminLoginForm
from .models import User

# Permission groups are not needed for a one-person shop; hide them to keep the menu short.
admin.site.unregister(Group)
admin.site.login_form = AdminLoginForm


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ["email"]


class UserChangeAdminForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    ordering = ["-date_joined"]
    list_display = ["email", "full_name", "phone", "account_type", "company_name", "email_verified",
                    "business_approved", "order_links", "date_joined"]
    list_filter = ["email_verified", "account_type", "business_approved", "is_staff", "is_active"]
    search_fields = ["email", "first_name", "last_name", "company_name", "vat_number", "phone"]
    actions = ["approve_business", "confirm_email"]
    readonly_fields = ["last_login", "date_joined", "vat_registry", "vat_checked_at"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        (_("Customer"), {"fields": [("first_name", "last_name"), "phone", "language", "account_type",
                                    "email_verified"]}),
        (_("Business"), {"fields": ["company_name", ("vat_number", "tax_office"), "business_activity",
                                    ("vat_registry", "vat_checked_at"), "business_approved"]}),
        (_("Address"), {"fields": ["address", ("city", "postal_code"), "country"]}),
        (_("Permissions"), {"classes": ["collapse"],
                            "fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        (_("Dates"), {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (None, {"classes": ["wide"], "fields": ["email", "usable_password", "password1", "password2"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n_orders=Count("orders"))

    @admin.display(description=_("name"), ordering="last_name")
    def full_name(self, obj):
        return obj.get_full_name()

    @admin.display(description=_("orders"), ordering="n_orders")
    def order_links(self, obj):
        if not obj.n_orders:
            return "0"
        url = reverse("admin:orders_order_changelist") + f"?user__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, obj.n_orders)

    def save_model(self, request, obj, form, change):
        just_approved = change and "business_approved" in form.changed_data and obj.business_approved
        super().save_model(request, obj, form, change)
        if just_approved and emails.business_approved(obj):
            messages.info(request, _("The customer was emailed that business prices are now active."))

    @admin.action(description=_("Approve selected business accounts (and email them)"))
    def approve_business(self, request, queryset):
        users = list(queryset.filter(account_type=User.AccountType.BUSINESS, business_approved=False))
        for user in users:
            user.business_approved = True
            user.save(update_fields=["business_approved"])
            emails.business_approved(user)
        self.message_user(request, _("%(n)s business account(s) approved.") % {"n": len(users)}, messages.SUCCESS)

    @admin.action(description=_("Confirm email (e.g. customers you know or who called you)"))
    def confirm_email(self, request, queryset):
        n = queryset.filter(email_verified=False).update(email_verified=True)
        self.message_user(request, _("%(n)s account(s) can now order.") % {"n": n}, messages.SUCCESS)
