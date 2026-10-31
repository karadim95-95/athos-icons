"""
A shopping cart kept in the visitor's session (no database rows needed).

Each line is keyed "product id", or "product id:size id" for icons that come in several sizes.
"""

import re
from decimal import Decimal

from catalog.models import Product, money
from catalog.pricing import price_info

MAX_QTY = 999


class Cart:
    SESSION_KEY = "cart"

    def __init__(self, request):
        self.request = request
        self.session = request.session
        self.data = self.session.get(self.SESSION_KEY) or {}

    def _save(self):
        self.session[self.SESSION_KEY] = self.data
        self.session.modified = True

    @staticmethod
    def key(product, size=None):
        return f"{product.pk}:{size.pk}" if size else str(product.pk)

    def set(self, key, quantity):
        if not re.fullmatch(r"\d+(:\d+)?", str(key)):
            return
        quantity = max(0, min(int(quantity), MAX_QTY))
        if quantity:
            self.data[key] = quantity
        else:
            self.data.pop(key, None)
        self._save()

    def add(self, key, quantity=1):
        self.set(key, self.data.get(key, 0) + int(quantity))

    def clear(self):
        self.data = {}
        self._save()

    def __len__(self):
        return sum(self.data.values())

    def lines(self, site=None):
        """Products in the cart with the prices this customer pays. Unavailable items and sizes are dropped."""
        products = {
            str(p.pk): p for p in Product.objects.visible()
            .filter(pk__in=[int(key.split(":")[0]) for key in self.data], is_available=True)
            .prefetch_related("images")
        }
        lines, still_valid = [], {}
        for key, qty in self.data.items():
            product_id, _, size_id = key.partition(":")
            product = products.get(product_id)
            if product is None:
                continue
            sizes = {str(option.pk): option for option in product.size_options}
            size = sizes.get(size_id)
            if (size_id and size is None) or (not size_id and sizes):
                continue  # that size is no longer offered, or a size must be chosen
            still_valid[key] = qty
            price = price_info(product, self.request.user, site, size)
            lines.append({
                "key": key,
                "product": product,
                "size": size,
                "quantity": qty,
                "price": price,
                "line_total": money(price.final * qty),
            })
        if still_valid != self.data:
            self.data = still_valid
            self._save()
        return lines

    @staticmethod
    def total(lines):
        return money(sum((line["line_total"] for line in lines), Decimal("0")))
