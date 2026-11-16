from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from hello_world.core import views as core_views

urlpatterns = [
    path("", core_views.index, name="index"),
    path("healthz/", core_views.healthz, name="healthz"),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=f"{settings.STATIC_URL}Octocat.png",
            permanent=False,
        ),
        name="favicon",
    ),
    path(
        "__run_migrations__/",
        core_views.run_migrations_view,
        name="run_migrations",
    ),
    path(
        "__create_admin__/",
        core_views.create_admin_view,
        name="create_admin",
    ),
    path("admin/", admin.site.urls),
]

if "django_browser_reload" in settings.INSTALLED_APPS:
    urlpatterns.append(
        path("__reload__/", include("django_browser_reload.urls")),
    )

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.STATIC_ROOT,
    )
