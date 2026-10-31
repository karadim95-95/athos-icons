from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _

from core import antispam
from orders import emails
from orders.cart import Cart
from orders.models import unread_messages

from .forms import EmailLoginForm, HumanPasswordResetForm, ProfileForm, RegistrationForm
from .models import User
from .tokens import email_verification_token


class LoginView(auth_views.LoginView):
    form_class = EmailLoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def _safe_next(request, url):
    return url if url_has_allowed_host_and_scheme(url, {request.get_host()}, request.is_secure()) else None


def send_verification(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    return emails.verify_email(user, settings.SITE_URL + reverse("accounts:verify_email_confirm", args=[uid, token]))


def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    account_type = "business" if request.GET.get("type") == "business" else "consumer"
    form = RegistrationForm(request.POST or None, request=request, initial={"account_type": account_type})
    context = {"form": form, "next": request.POST.get("next") or request.GET.get("next", "")}
    if request.method == "POST":
        if antispam.too_many("register", antispam.client_ip(request), limit=5, period=3600):
            messages.error(request, _("Too many sign-ups from your connection. Please try again later."))
            return render(request, "accounts/register.html", context, status=429)
        if form.is_valid():
            user = form.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            send_verification(user)
            if _safe_next(request, context["next"]):
                request.session["after_verify"] = context["next"]
            messages.success(request, _("Welcome! One last step: confirm your email address."))
            return redirect("accounts:verify_email")
    return render(request, "accounts/register.html", context)


@login_required
def verify_email(request):
    """'Check your inbox' page, with a button to send the link again."""
    user = request.user
    if user.email_verified:
        return redirect("accounts:dashboard")
    if request.method == "POST":
        if antispam.too_many("verify", user.pk, limit=3, period=3600):
            messages.error(request, _("We already sent you several links. Please check your spam folder too, "
                                      "or try again in an hour."))
        elif send_verification(user):
            messages.success(request, _("We sent you a new link."))
        else:
            messages.error(request, _("The email could not be sent. Please try again later."))
        return redirect("accounts:verify_email")
    return render(request, "accounts/verify_email.html")


def verify_email_confirm(request, uidb64, token):
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uidb64).decode())
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and user.email_verified:
        messages.info(request, _("Your email is already confirmed."))
        return redirect("accounts:dashboard" if request.user == user else "accounts:login")
    if user is None or not user.is_active or not email_verification_token.check_token(user, token):
        messages.error(request, _("This link is not valid any more: it was already used or is older than 24 hours. "
                                  "Log in to get a new one."))
        return redirect("accounts:verify_email" if request.user.is_authenticated else "accounts:login")

    user.email_verified = True
    user.save(update_fields=["email_verified"])
    if user.business_pending:
        emails.to_shop(
            _("New business account to approve: %(company)s") % {"company": user.company_name},
            [str(user), user.email, f"{_('VAT number')}: {user.vat_number}", user.business_activity, "",
             user.vat_registry],
            reverse("admin:accounts_user_change", args=[user.pk]), reply_to=[user.email],
        )
    if request.user != user:
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    if user.business_pending:
        messages.success(request, _("Thank you, your email is confirmed. We will check your business details "
                                    "and activate business prices (excl. VAT) shortly."))
    else:
        messages.success(request, _("Thank you, your email is confirmed. You can now send order requests."))
    next_url = _safe_next(request, request.session.pop("after_verify", ""))
    return redirect(next_url or ("orders:cart" if len(Cart(request)) else "accounts:dashboard"))


@login_required
def dashboard(request):
    user = request.user
    return render(request, "accounts/dashboard.html", {
        "orders": user.orders.prefetch_related("items")[:5],
        "custom_requests": user.custom_requests.all()[:5],
        "unread": unread_messages(user).count(),
    })


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Your details were saved."))
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, _("Your password was changed."))
        return super().form_valid(form)


class PasswordResetView(auth_views.PasswordResetView):
    form_class = HumanPasswordResetForm
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "request": self.request}

    def post(self, request, *args, **kwargs):
        if antispam.too_many("password_reset", antispam.client_ip(request), limit=5, period=3600):
            messages.error(request, _("Too many attempts from your connection. Please try again later."))
            return self.render_to_response(self.get_context_data(), status=429)
        return super().post(request, *args, **kwargs)


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        form.user.email_verified = True  # the reset link reached their inbox
        messages.success(self.request, _("Your new password is set. You can log in now."))
        return super().form_valid(form)
