from dataclasses import dataclass
from decimal import Decimal

from core.models import SiteSettings


def is_business_customer(user):
    return bool(user and user.is_authenticated and user.is_business)


@dataclass
class PriceInfo:
    final: Decimal
    original: Decimal
    business: bool

    @property
    def has_discount(self):
        return self.final < self.original


def price_info(product, user, site=None):
    site = site or SiteSettings.load()
    business = is_business_customer(user)
    return PriceInfo(
        final=product.price_for(business, site.vat_multiplier),
        original=product.base_price_for(business, site.vat_multiplier),
        business=business,
    )
