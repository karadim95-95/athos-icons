from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified", True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """A customer (or you, the shop owner). Customers log in with their email."""

    class AccountType(models.TextChoices):
        CONSUMER = "consumer", _("Individual")
        BUSINESS = "business", _("Business")

    username = None
    email = models.EmailField(_("email"), unique=True)
    phone = models.CharField(_("phone"), max_length=40, blank=True)
    language = models.CharField(_("email language"), max_length=5, default="el")
    email_verified = models.BooleanField(
        _("email confirmed"), default=False,
        help_text=_("The customer clicked the link we emailed them. Only confirmed customers can order."),
    )

    account_type = models.CharField(
        _("account type"), max_length=10, choices=AccountType.choices, default=AccountType.CONSUMER
    )
    company_name = models.CharField(_("company name"), max_length=150, blank=True)
    vat_number = models.CharField(
        _("VAT number"), max_length=30, blank=True,
        help_text=_("9 digits. Businesses from other EU countries: VAT number with the country code, "
                    "e.g. CY12345678X."),
    )
    tax_office = models.CharField(_("tax office"), max_length=100, blank=True,
                                  help_text=_("e.g. Α' Αθηνών. Not needed for businesses outside Greece."))
    vat_registry = models.TextField(_("registry details"), blank=True, editable=False,
                                    help_text=_("What AADE (or the EU VIES service) says about this number."))
    vat_checked_at = models.DateTimeField(_("registry checked"), null=True, blank=True, editable=False)
    business_activity = models.CharField(_("business activity"), max_length=150, blank=True)
    business_approved = models.BooleanField(
        _("business approved"), default=False,
        help_text=_("Tick after checking the company. Only approved businesses see prices without VAT."),
    )

    address = models.CharField(_("street address"), max_length=200, blank=True)
    city = models.CharField(_("city"), max_length=100, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    country = models.CharField(_("country"), max_length=100, blank=True, default="Ελλάδα")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("customer")
        verbose_name_plural = _("customers")

    def __str__(self):
        return self.get_full_name() or self.email

    @property
    def is_business(self):
        """True only for businesses you have approved (they get net prices)."""
        return self.account_type == self.AccountType.BUSINESS and self.business_approved

    @property
    def business_pending(self):
        return self.account_type == self.AccountType.BUSINESS and not self.business_approved

    @property
    def can_order(self):
        """Customers with a confirmed email. Admin accounts cannot place orders."""
        return self.email_verified and not self.is_staff

    @property
    def display_name(self):
        return self.company_name if self.account_type == self.AccountType.BUSINESS and self.company_name else str(self)
