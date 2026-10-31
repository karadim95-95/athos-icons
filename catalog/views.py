from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F
from django.shortcuts import get_object_or_404, render

from core.models import SiteSettings

from .models import Category, Product, normalize_search
from .pricing import price_info, price_table

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
    site = SiteSettings.load()
    groups = product.option_groups
    sizes = [{"option": option, "price": price_info(product, request.user, site, option)}
             for option in product.size_options]
    preselected = [group[0] for _variation, group in groups]  # the first option of each choice
    first_size = sizes[0]["option"] if sizes else None
    # Every option of each choice: the ones this icon is not made in are shown greyed out.
    choices = [{"variation": variation, "selected": group[0].pk,
                "options": [(option, option in group) for option in variation.options.all()]}
               for variation, group in groups]
    # Shown in this order: the first choice (surface), the dimensions, then the other choices.
    summary = [o.local_name for o in preselected[:1]] + ([str(first_size.size)] if first_size else []) + \
        [o.local_name for o in preselected[1:]]
    return render(request, "catalog/product_detail.html", {
        "product": product, "related": related, "sizes": sizes, "groups": groups, "choices": choices,
        "summary": " · ".join(summary),
        "p": price_info(product, request.user, site, first_size, preselected),
        "price_table": price_table(product, request.user, site) if groups or sizes else None,
    })
