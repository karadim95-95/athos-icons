"""A shopping cart kept in the visitor's session (no database rows needed)."""

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

    def set(self, product_id, quantity):
        quantity = max(0, min(int(quantity), MAX_QTY))
        if quantity:
            self.data[str(product_id)] = quantity
        else:
            self.data.pop(str(product_id), None)
        self._save()

    def add(self, product_id, quantity=1):
        self.set(product_id, self.data.get(str(product_id), 0) + int(quantity))

    def clear(self):
        self.data = {}
        self._save()

    def __len__(self):
        return sum(self.data.values())

    def lines(self, site=None):
        """Products in the cart with the prices this customer pays. Unavailable items are dropped."""
        products = (
            Product.objects.visible()
            .filter(pk__in=[int(pk) for pk in self.data], is_available=True)
            .prefetch_related("images")
        )
        found = {str(p.pk): p for p in products}
        if set(found) != set(self.data):
            self.data = {pk: qty for pk, qty in self.data.items() if pk in found}
            self._save()
        lines = []
        for pk, qty in self.data.items():
            product = found[pk]
            price = price_info(product, self.request.user, site)
            lines.append({
                "product": product,
                "quantity": qty,
                "price": price,
                "line_total": money(price.final * qty),
            })
        return lines

    @staticmethod
    def total(lines):
        return money(sum((line["line_total"] for line in lines), Decimal("0")))
