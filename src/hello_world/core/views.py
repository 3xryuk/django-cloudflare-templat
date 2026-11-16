import io
import logging
import os
import secrets
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _is_management_authorized(request: HttpRequest) -> bool:
    """Authorize management endpoints via superuser or management token."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.is_superuser:
        return True

    expected_token = getattr(settings, "CF_MANAGEMENT_TOKEN", "")
    provided_token = request.headers.get("X-CF-Management-Token", "")
    if expected_token and provided_token:
        return secrets.compare_digest(provided_token, expected_token)

    return False


def index(request: HttpRequest) -> HttpResponse:
    """Render the template homepage with runtime and Cloudflare metadata."""
    context = {
        "title": "Django + Cloudflare Workers",
        "is_cloudflare_worker": settings.IS_CLOUDFLARE_WORKER,
        "db_engine": settings.DATABASES["default"]["ENGINE"],
        "cache_backend": settings.CACHES["default"]["BACKEND"],
        "cache_tiers": list(settings.CACHES.keys()),
        "bytecode_disabled": sys.dont_write_bytecode,
        "cf_ray": request.headers.get("CF-Ray", ""),
        "cf_country": request.headers.get("CF-IPCountry", ""),
    }
    return render(request, "index.html", context)


def healthz(request: HttpRequest) -> JsonResponse:
    """Lightweight health check endpoint for Workers and uptime monitors."""
    is_worker = settings.IS_CLOUDFLARE_WORKER
    runtime = "cloudflare-workers" if is_worker else "local"
    return JsonResponse(
        {
            "status": "ok",
            "runtime": runtime,
            "db_engine": settings.DATABASES["default"]["ENGINE"],
            "cache_backend": settings.CACHES["default"]["BACKEND"],
            "cache_tiers": list(settings.CACHES.keys()),
            "bytecode_disabled": sys.dont_write_bytecode,
        }
    )


@require_http_methods(["GET", "POST"])
def run_migrations_view(request: HttpRequest) -> JsonResponse:
    """Apply Django migrations programmatically for serverless D1."""
    if not _is_management_authorized(request):
        return JsonResponse(
            {"status": "error", "message": "Forbidden"},
            status=403,
        )

    output = io.StringIO()
    try:
        call_command("migrate", interactive=False, stdout=output)
        return JsonResponse(
            {
                "status": "ok",
                "message": "Migrations applied successfully.",
                "output": output.getvalue(),
            }
        )
    except Exception as exc:
        logger.exception("Failed to apply database migrations")
        return JsonResponse(
            {"status": "error", "message": str(exc)},
            status=500,
        )


@require_http_methods(["GET", "POST"])
def create_admin_view(request: HttpRequest) -> JsonResponse:
    """Ensure an initial superuser exists for serverless D1 deployments."""
    if not _is_management_authorized(request):
        return JsonResponse(
            {"status": "error", "message": "Forbidden"},
            status=403,
        )

    user_model = get_user_model()
    if user_model.objects.filter(is_superuser=True).exists():
        return JsonResponse(
            {"status": "ok", "message": "Superuser already exists."}
        )

    username = (
        request.POST.get("username")
        or os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
    )
    email = (
        request.POST.get("email")
        or os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
    )
    password = (
        request.POST.get("password")
        or os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
    )

    if not password:
        msg = (
            "Provide 'password' in POST data "
            "or set DJANGO_SUPERUSER_PASSWORD."
        )
        return JsonResponse({"status": "error", "message": msg}, status=400)

    user_model.objects.create_superuser(
        username=username,
        email=email,
        password=password,
    )
    return JsonResponse(
        {
            "status": "ok",
            "message": f"Superuser '{username}' created successfully.",
        },
        status=201,
    )
