from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.product_list, name="products"),
    path("c/<str:slug>/", views.product_list, name="category"),
    path("icon/<str:slug>/", views.product_detail, name="product"),
]
