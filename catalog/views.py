from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F
from django.shortcuts import get_object_or_404, render

from .models import Category, Product, normalize_search

SORTS = {
    "new": "-created_at",
    "price": "effective_price",
    "-price": "-effective_price",
    "name": "name",
}


def product_list(request, slug=None):
    category = get_object_or_404(Category, slug=slug, is_active=True) if slug else None
    products = Product.objects.visible().select_related("category").prefetch_related("images").annotate(
        effective_price=ExpressionWrapper(
            F("price") * (100 - F("discount_percent")) / 100, output_field=DecimalField()
        )
    )
    if category:
        products = products.filter(category=category)

    query = request.GET.get("q", "").strip()
    for word in normalize_search(query).split():
        products = products.filter(search_text__contains=word)
    if request.GET.get("sale"):
        products = products.filter(discount_percent__gt=0)

    sort = request.GET.get("sort", "new")
    products = products.order_by("-is_available", SORTS.get(sort, "-created_at"))
    page = Paginator(products, 24).get_page(request.GET.get("page"))

    return render(request, "catalog/product_list.html", {
        "category": category,
        "categories": Category.objects.filter(is_active=True),
        "page_obj": page,
        "query": query,
        "sort": sort,
    })


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.visible().select_related("category").prefetch_related("images"), slug=slug
    )
    related = (
        Product.objects.visible().filter(category=product.category).exclude(pk=product.pk)
        .prefetch_related("images")[:4]
        if product.category else []
    )
    return render(request, "catalog/product_detail.html", {"product": product, "related": related})
