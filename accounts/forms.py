from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from core import antispam
from core.forms import HumanCheckMixin

from . import vat
from .models import User

LOGIN_FAILURES_ALLOWED = 10  # per connection, then a 15-minute pause
BUSINESS_FIELDS = ["company_name", "vat_number", "tax_office", "business_activity"]


class LoginLimitMixin:
    """Emails are stored in lower case; repeated wrong passwords pause logins from that connection."""

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()

    def clean(self):
        ip = antispam.client_ip(self.request) if self.request else None
        if ip and antispam.attempts("login", ip) >= LOGIN_FAILURES_ALLOWED:
            raise forms.ValidationError(_("Too many failed attempts. Please wait 15 minutes and try again."))
        try:
            return super().clean()
        except forms.ValidationError:
            if ip:
                antispam.record_attempt("login", ip, period=15 * 60)
            raise


class EmailLoginForm(LoginLimitMixin, AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = _("Email")
        self.fields["username"].widget.attrs.update({"autocomplete": "email", "autofocus": True})


class AdminLoginForm(LoginLimitMixin, AdminAuthenticationForm):
    pass


class HumanPasswordResetForm(HumanCheckMixin, PasswordResetForm):
    pass


class BusinessFieldsMixin:
    """Business accounts need company name, ΑΦΜ and (Greek businesses) ΔΟΥ; the ΑΦΜ is checked (accounts/vat.py)."""

    vat_result = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("company_name", "vat_number", "tax_office"):
            if name in self.fields:
                self.fields[name].show_required = True  # required for businesses: no "(optional)" label

    def clean(self):
        data = super().clean()
        if data.get("account_type") != User.AccountType.BUSINESS:
            return data
        for field in ("company_name", "vat_number"):
            if not data.get(field):
                self.add_error(field, _("Required for business accounts."))
        if data.get("vat_number") and "vat_number" in self.changed_data:
            try:
                self.vat_result = vat.check(data["vat_number"])
            except forms.ValidationError as error:
                self.add_error("vat_number", error)
            else:
                data["vat_number"] = self.vat_result.number
        if vat.split(data.get("vat_number"))[0] == "EL" and not data.get("tax_office"):
            self.add_error("tax_office", _("Required for Greek businesses."))
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.vat_result:
            user.vat_registry = self.vat_result.registry
            user.vat_checked_at = timezone.now() if self.vat_result.checked else None
            if self.vat_result.tax_office:
                user.tax_office = self.vat_result.tax_office  # the official spelling
        if commit:
            user.save()
        return user


class RegistrationForm(HumanCheckMixin, BusinessFieldsMixin, UserCreationForm):
    accept_terms = forms.BooleanField(label=_("I accept the terms of use and the privacy policy"), required=True)

    class Meta:
        model = User
        fields = ["account_type", "first_name", "last_name", "email", "phone", *BUSINESS_FIELDS]
        widgets = {"account_type": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "phone"):
            self.fields[name].required = True
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email already exists. Try logging in."))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.language = (get_language() or "el")[:2]
        if user.account_type != User.AccountType.BUSINESS:
            for field in BUSINESS_FIELDS:
                setattr(user, field, "")
        if commit:
            user.save()
        return user


class ProfileForm(BusinessFieldsMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "language", *BUSINESS_FIELDS,
                  "address", "city", "postal_code", "country"]
        widgets = {"language": forms.Select(choices=[("el", "Ελληνικά"), ("en", "English")])}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.account_type != User.AccountType.BUSINESS:
            for field in BUSINESS_FIELDS:
                del self.fields[field]
        elif self.instance.business_approved:
            # You approved the business with this number; changing it goes through you.
            self.fields["vat_number"].disabled = True
            self.fields["vat_number"].help_text = _("To change your ΑΦΜ, please contact us.")

    def clean(self):
        self.cleaned_data["account_type"] = self.instance.account_type
        return super().clean()
