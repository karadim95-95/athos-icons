from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from catalog.models import Category, Product

from .models import Page


class StaticSitemap(Sitemap):
    i18n = True

    def items(self):
        return ["core:home", "catalog:products", "orders:custom_request", "core:contact"]

    def location(self, item):
        return reverse(item)


class ProductSitemap(Sitemap):
    i18n = True

    def items(self):
        return Product.objects.visible()

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    i18n = True

    def items(self):
        return Category.objects.filter(is_active=True)


class PageSitemap(Sitemap):
    i18n = True

    def items(self):
        return Page.objects.all()


sitemaps = {"static": StaticSitemap, "products": ProductSitemap,
            "categories": CategorySitemap, "pages": PageSitemap}
