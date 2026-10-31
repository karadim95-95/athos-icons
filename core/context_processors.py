from django.conf import settings

from orders.cart import Cart
from orders.models import unread_messages

from . import antispam
from .models import Page, SiteSettings


def shop(request):
    user = getattr(request, "user", None)
    return {
        "shop": SiteSettings.load(),
        "cart_count": len(Cart(request)) if hasattr(request, "session") else 0,
        "footer_pages": Page.objects.filter(show_in_footer=True),
        "unread_count": unread_messages(user).count() if user and user.is_authenticated else 0,
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY if antispam.turnstile_enabled() else "",
        "demo_mode": settings.DEMO_MODE,
    }
