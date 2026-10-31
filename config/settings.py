"""
Django settings for the icon shop.

Everything that differs between your laptop and the live server is read from
environment variables (or a `.env` file next to manage.py). See `.env.example`.
"""

import os
from pathlib import Path

from django.utils.translation import gettext_lazy as _
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, default)).lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)
SECRET_KEY = env("SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Public address of the site, used for links inside emails.
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

# Hide the admin under a less obvious address in production, e.g. "manage-7f3k/".
ADMIN_URL = env("ADMIN_URL", "admin/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django.contrib.humanize",
    "core",
    "accounts",
    "catalog",
    "orders",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "core.context_processors.shop",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

SQLITE_PATH = Path(env("SQLITE_PATH", BASE_DIR / "data" / "db.sqlite3"))
SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": SQLITE_PATH,
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Languages -------------------------------------------------------------
LANGUAGE_CODE = "el"
LANGUAGES = [
    ("el", _("Greek")),
    ("en", _("English")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Europe/Athens"
USE_I18N = True
USE_TZ = True

# --- Static & uploaded files -------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))

# Shared by all web workers; used to count attempts for the anti-bot limits.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": SQLITE_PATH.parent / "cache",
    }
}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Phone photos can be large; uploads are shrunk automatically after saving.
MAX_UPLOAD_MB = 20
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 30

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Email -------------------------------------------------------------------
# Without EMAIL_HOST, emails are printed in the terminal instead of being sent.
EMAIL_HOST = env("EMAIL_HOST", "")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(env("EMAIL_PORT", 587))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Athos Icons <shop@example.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Where "new order" / "new request" notifications for you are sent.
# Can also be changed later in the admin under "Shop settings".
SHOP_NOTIFY_EMAIL = env("SHOP_NOTIFY_EMAIL", "")

# --- Automatic parcel tracking -----------------------------------------------
# API key from https://api.17track.net. Without it, you set "In transit" and
# "Delivered" by hand in the admin.
TRACKING_API_KEY = env("TRACKING_API_KEY", "")

# --- Bot protection ----------------------------------------------------------
# Optional Cloudflare Turnstile check on sign-up, contact and password reset
# (free, https://dash.cloudflare.com → Turnstile). Leave empty to skip it.
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", "")

# --- ΑΦΜ check against the AADE registry ----------------------------------------
# Optional "special codes" for AADE's "Αναζήτηση Βασικών Στοιχείων Μητρώου Επιχειρήσεων"
# (free, see README). Without them only the ΑΦΜ check digit is verified.
AADE_USERNAME = env("AADE_USERNAME", "")
AADE_PASSWORD = env("AADE_PASSWORD", "")

# --- Downloadable demo --------------------------------------------------------
# Set by demo/launch.py: emails go to a "Demo mailbox" page instead of being sent,
# photos are served by Django, and the site runs over plain http on this computer.
DEMO_MODE = env_bool("DEMO_MODE", False)
if DEMO_MODE:
    EMAIL_BACKEND = "core.demo.DemoMailBackend"
DEMO_MAILBOX = SQLITE_PATH.parent / "demo-mailbox.jsonl"

# --- Production hardening ----------------------------------------------------
if not DEBUG and not DEMO_MODE:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", 3600))
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
