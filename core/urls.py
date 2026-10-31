from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("contact/", views.contact, name="contact"),
    path("p/<slug:slug>/", views.page, name="page"),
    path("demo/mailbox/", views.demo_mailbox, name="demo_mailbox"),
]
