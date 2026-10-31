from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils.translation import gettext as _


def refuse_non_customer(request):
    """A redirect if the logged-in user may not order (admin account or unconfirmed email), else None."""
    user = request.user
    if user.is_staff:
        messages.warning(request, _("You are logged in with the shop's admin account, which cannot place orders. "
                                    "Log out and use a customer account to try the shop."))
        return redirect("core:home")
    if not user.email_verified:
        return redirect("accounts:verify_email")
    return None


def customer_required(view):
    """Logged in as a customer with a confirmed email."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        return refuse_non_customer(request) or view(request, *args, **kwargs)

    return wrapper
