from django import forms
from django.utils.translation import gettext_lazy as _

from . import antispam
from .images import validate_image_size


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """Pick several photos at once (works with the phone gallery and camera too)."""

    default_validators = [validate_image_size]

    def __init__(self, *args, max_files=10, **kwargs):
        self.max_files = max_files
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > self.max_files:
            raise forms.ValidationError(_("You can upload up to %(n)s images.") % {"n": self.max_files})
        if not files and self.required:
            raise forms.ValidationError(self.error_messages["required"])
        return [super(MultipleImageField, self).clean(f, initial) for f in files]


class HumanCheckMixin:
    """Rejects forms sent by bots (see core/antispam.py). Render it with partials/human_check.html."""

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        # Hidden from people; bots tend to fill it in.
        self.fields["website"] = forms.CharField(
            required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
        self.fields["form_ts"] = forms.CharField(
            required=False, widget=forms.HiddenInput, initial=antispam.form_timestamp())

    def clean(self):
        data = super().clean()
        if self.request is not None and not antispam.looks_human(self.request):
            raise forms.ValidationError(_("We could not confirm that you are a person. Please try again."))
        return data


class ContactForm(HumanCheckMixin, forms.Form):
    name = forms.CharField(label=_("Your name"), max_length=100)
    email = forms.EmailField(label=_("Email"))
    message = forms.CharField(label=_("Message"), widget=forms.Textarea(attrs={"rows": 6}), max_length=5000)
