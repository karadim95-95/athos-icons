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
    from_price: bool = False  # several sizes: this is the lowest ("from 35 €")

    @property
    def has_discount(self):
        return self.final < self.original


def price_info(product, user, site=None, size=None):
    """What this customer pays for the product (in the given size; without one, its lowest size price)."""
    site = site or SiteSettings.load()
    business = is_business_customer(user)
    options = product.size_options if size is None else []
    if options:
        size = min(options, key=lambda option: option.price)
    return PriceInfo(
        final=product.price_for(business, site.vat_multiplier, size),
        original=product.base_price_for(business, site.vat_multiplier, size),
        business=business,
        from_price=len({option.price for option in options}) > 1,
    )
