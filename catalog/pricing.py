import itertools
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


def price_info(product, user, site=None, size=None, options=None):
    """What this customer pays for the product as configured (size, options).

    Without size and options: the lowest price it can have, e.g. for "from 35 €" on product cards."""
    site = site or SiteSettings.load()
    business = is_business_customer(user)
    from_price = False
    if size is None and options is None:
        sizes, groups = product.size_options, product.option_groups
        if sizes:
            size = min(sizes, key=lambda s: s.price)
        options = [min(group, key=lambda option: option.price_change) for _variation, group in groups]
        from_price = len({s.price for s in sizes}) > 1 or any(
            len({option.price_change for option in group}) > 1 for _variation, group in groups)
    options = options or ()
    return PriceInfo(
        final=product.price_for(business, site.vat_multiplier, size, options),
        original=product.base_price_for(business, site.vat_multiplier, size, options),
        business=business,
        from_price=from_price,
    )


def configuration_key(size, options):
    """How the product page and the cart name one configuration: "size id:option ids"."""
    return f"{size.pk if size else ''}:{'.'.join(sorted(str(option.pk) for option in options))}"


def price_table(product, user, site=None):
    """The price of every configuration customers can pick, for the live price on the product page."""
    from core.templatetags.shop import euro

    site = site or SiteSettings.load()
    table = {}
    for size in product.size_options or [None]:
        for options in itertools.product(*[group for _variation, group in product.option_groups]):
            price = price_info(product, user, site, size, list(options))
            table[configuration_key(size, options)] = {"final": euro(price.final), "original": euro(price.original)}
    return table
