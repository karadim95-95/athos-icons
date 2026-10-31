from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.static import serve

from core.sitemaps import sitemaps
from core.views import robots_txt

admin.site.site_header = "Athos Icons – διαχείριση"
admin.site.site_title = "Athos Icons"
admin.site.index_title = "Πίνακας ελέγχου"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("robots.txt", robots_txt),
]

urlpatterns += i18n_patterns(
    path("", include("core.urls")),
    path("shop/", include("catalog.urls")),
    path("account/", include("accounts.urls")),
    path("", include("orders.urls")),
)

# In production the web server (Caddy) serves /media/ directly.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.DEMO_MODE:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]
