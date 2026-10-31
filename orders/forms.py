from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import MultipleImageField

from .models import CustomRequest


class AddressForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = forms.CharField(label=_("Phone"), max_length=40)
    company_name = forms.CharField(label=_("Company name"), max_length=150, required=False)
    vat_number = forms.CharField(label=_("VAT number"), max_length=30, required=False)
    tax_office = forms.CharField(label=_("Tax office"), max_length=100, required=False)
    address = forms.CharField(label=_("Street address"), max_length=200)
    city = forms.CharField(label=_("City"), max_length=100)
    postal_code = forms.CharField(label=_("Postal code"), max_length=20)
    country = forms.CharField(label=_("Country"), max_length=100)
    save_to_profile = forms.BooleanField(label=_("Save this address to my account"), required=False, initial=True)

    BUSINESS_ONLY = ("company_name", "vat_number", "tax_office")

    def __init__(self, *args, user, **kwargs):
        self.user = user
        kwargs.setdefault("initial", {
            "full_name": user.get_full_name(), "phone": user.phone,
            "company_name": user.company_name, "vat_number": user.vat_number, "tax_office": user.tax_office,
            "address": user.address, "city": user.city, "postal_code": user.postal_code,
            "country": user.country, "save_to_profile": True,
        })
        super().__init__(*args, **kwargs)
        if user.account_type != user.AccountType.BUSINESS:
            for name in self.BUSINESS_ONLY:
                del self.fields[name]
        else:
            self.fields["company_name"].required = True
            for name in ("vat_number", "tax_office"):
                self.fields[name].disabled = True
            self.fields["vat_number"].help_text = _("From your account details. To change them, contact us.")

    def address_data(self):
        return {**self.cleaned_data, "email": self.user.email}

    def save_to_profile(self):
        if not self.cleaned_data.get("save_to_profile"):
            return
        for name in ("phone", "address", "city", "postal_code", "country", *self.BUSINESS_ONLY):
            if name in self.cleaned_data:
                setattr(self.user, name, self.cleaned_data[name])
        self.user.save()


class CheckoutForm(AddressForm):
    notes = forms.CharField(
        label=_("Notes for us"), required=False, max_length=3000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("e.g. engraving text, preferred delivery time…")}),
    )
    field_order = ["full_name", "phone", "company_name", "vat_number", "tax_office",
                   "address", "city", "postal_code", "country", "notes", "save_to_profile"]


class CustomRequestForm(forms.ModelForm):
    images = MultipleImageField(label=_("Reference photos"), required=False, max_files=8,
                                help_text=_("A photo of the icon you have in mind helps a lot. Up to 8 images."))

    class Meta:
        model = CustomRequest
        fields = ["subject", "description", "dimensions", "quantity", "budget", "needed_by"]
        widgets = {
            "description": forms.Textarea(attrs={
                "rows": 5,
                "placeholder": _("Describe the icon: saint or scene, style, background (gold, blue…), "
                                 "wood, any inscription, who it is for…"),
            }),
            "dimensions": forms.TextInput(attrs={"placeholder": _("e.g. 30 x 40 cm")}),
            "budget": forms.TextInput(attrs={"placeholder": _("e.g. up to €300")}),
            "needed_by": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class MessageForm(forms.Form):
    body = forms.CharField(
        label=_("Your message"), max_length=5000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Write a message to the shop…")}),
    )
