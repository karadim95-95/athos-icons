from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("cart/", views.cart_detail, name="cart"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("checkout/", views.checkout, name="checkout"),
    path("custom-icon/", views.custom_request_create, name="custom_request"),
    path("account/orders/", views.order_list, name="order_list"),
    path("account/orders/<str:number>/", views.order_detail, name="order_detail"),
    path("account/orders/<str:number>/thank-you/", views.order_placed, name="order_placed"),
    path("account/orders/<str:number>/cancel/", views.order_cancel, name="order_cancel"),
    path("account/requests/", views.request_list, name="request_list"),
    path("account/requests/<str:number>/", views.request_detail, name="request_detail"),
    path("account/requests/<str:number>/accept/", views.quote_accept, name="quote_accept"),
    path("account/requests/<str:number>/decline/", views.quote_decline, name="quote_decline"),
]
