"""Django settings for hello_world project (Cloudflare Workers + Local Dev).

Configured for dual-mode operation:
- Local development (SQLite + optional django-browser-reload)
- Cloudflare Python Workers via django-cf (D1, R2, Static Assets, Access)

Non-secret configuration is defined in `wrangler.jsonc` under `"vars"`.
Secrets (`SECRET_KEY`, `DJANGO_SUPERUSER_PASSWORD`) are loaded from
`.dev.vars` locally or Cloudflare Worker Secrets in production.
"""

from collections.abc import Callable
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

# Avoid writing __pycache__/*.pyc files to save Worker V8 memory.
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

# BASE_DIR points to src/; REPO_ROOT points to the repository root.
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse key=value pairs from .dev.vars or .env files."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        values[key] = val
    return values


def _parse_wrangler_vars(path: Path) -> dict[str, str]:
    """Parse non-secret configuration variables from wrangler.jsonc 'vars'."""
    if not path.is_file():
        return {}
    clean_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    ]
    try:
        payload = json.loads("\n".join(clean_lines))
    except ValueError:
        return {}
    raw_vars = payload.get("vars", {})
    if not isinstance(raw_vars, dict):
        return {}
    parsed: dict[str, str] = {}
    for key, val in raw_vars.items():
        if val is None:
            continue
        if sys.platform != "emscripten" and key in (
            "USE_CLOUDFLARE_WORKER",
            "DEBUG",
        ):
            continue
        parsed[str(key)] = str(val)
    return parsed


_WRANGLER_VARS_CACHE: dict[str, str] = _parse_wrangler_vars(
    REPO_ROOT / "wrangler.jsonc",
)

_DOTENV_CACHE: dict[str, str] = {
    **_parse_env_file(REPO_ROOT / ".env"),
    **_parse_env_file(REPO_ROOT / ".dev.vars"),
}


def _cast_env_value(
    value: Any,
    cast: Callable[[Any], Any] | type | None,
) -> Any:
    """Cast a raw environment value using the requested callable or bool."""
    if cast is None:
        return value
    if cast is bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"Invalid boolean value: {value!r}")
    return cast(value)


def get_env(
    key: str,
    default: Any = None,
    cast: Callable[[Any], Any] | type | None = None,
) -> Any:
    """Resolve config: workers.env -> os.environ -> .dev.vars -> wrangler."""
    raw: Any = None

    if sys.platform == "emscripten":
        try:
            from workers import env as workers_env

            candidate = getattr(workers_env, key, None)
            if isinstance(candidate, (str, bool, int, float)):
                raw = candidate
        except (ImportError, AttributeError, RuntimeError):
            raw = None

    if raw is None:
        raw = os.environ.get(key)

    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raw = _DOTENV_CACHE.get(key)

    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raw = _WRANGLER_VARS_CACHE.get(key)

    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default

    return _cast_env_value(raw, cast)


# Detect Cloudflare Python Workers runtime or explicit flag.
IS_CLOUDFLARE_WORKER = (
    sys.platform == "emscripten"
    or get_env("USE_CLOUDFLARE_WORKER", default=False, cast=bool)
)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = get_env(
    "SECRET_KEY",
    default="django-cf-dev-secret-key-change-in-production",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = get_env("DEBUG", default=True, cast=bool)

ALLOWED_HOSTS = [
    host.strip()
    for host in get_env("ALLOWED_HOSTS", default="*").split(",")
    if host.strip()
]

if get_env("CSRF_TRUSTED_ORIGINS", default=""):
    CSRF_TRUSTED_ORIGINS = [
        origin.strip()
        for origin in get_env("CSRF_TRUSTED_ORIGINS", default="").split(",")
        if origin.strip()
    ]
elif "CODESPACE_NAME" in os.environ:
    codespace_name = get_env("CODESPACE_NAME", default="")
    codespace_domain = get_env(
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
        default="app.github.dev",
    )
    if codespace_name:
        CSRF_TRUSTED_ORIGINS = [
            f"https://{codespace_name}-8000.{codespace_domain}",
        ]

# Standard Django superuser settings (also authorizes management endpoints)
DJANGO_SUPERUSER_USERNAME = get_env(
    "DJANGO_SUPERUSER_USERNAME",
    default="admin",
)
DJANGO_SUPERUSER_EMAIL = get_env(
    "DJANGO_SUPERUSER_EMAIL",
    default="admin@example.com",
)
DJANGO_SUPERUSER_PASSWORD = get_env(
    "DJANGO_SUPERUSER_PASSWORD",
    default="",
)

USE_CLOUDFLARE_R2 = get_env("USE_CLOUDFLARE_R2", default=False, cast=bool)
USE_CLOUDFLARE_ACCESS = get_env(
    "USE_CLOUDFLARE_ACCESS",
    default=False,
    cast=bool,
)
CLOUDFLARE_ACCESS_TEAM_NAME = get_env(
    "CLOUDFLARE_ACCESS_TEAM_NAME",
    default=None,
)
CLOUDFLARE_ACCESS_AUD = get_env("CLOUDFLARE_ACCESS_AUD", default=None)
CLOUDFLARE_ACCESS_EXEMPT_PATHS = [
    path.strip()
    for path in get_env(
        "CLOUDFLARE_ACCESS_EXEMPT_PATHS",
        default="/healthz/,/static/,/favicon.ico",
    ).split(",")
    if path.strip()
]
HAS_BROWSER_RELOAD = (
    DEBUG
    and not IS_CLOUDFLARE_WORKER
    and importlib.util.find_spec("django_browser_reload") is not None
)

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "hello_world.core",
]

if importlib.util.find_spec("django_cf") is not None:
    INSTALLED_APPS.append("django_cf")

if HAS_BROWSER_RELOAD:
    INSTALLED_APPS.append("django_browser_reload")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if USE_CLOUDFLARE_ACCESS and (
    CLOUDFLARE_ACCESS_TEAM_NAME or CLOUDFLARE_ACCESS_AUD
):
    auth_middleware = "django.contrib.auth.middleware.AuthenticationMiddleware"
    auth_index = MIDDLEWARE.index(auth_middleware)
    MIDDLEWARE.insert(
        auth_index + 1,
        "django_cf.middleware.CloudflareAccessMiddleware",
    )

if HAS_BROWSER_RELOAD:
    MIDDLEWARE.append(
        "django_browser_reload.middleware.BrowserReloadMiddleware"
    )

X_FRAME_OPTIONS = get_env("X_FRAME_OPTIONS", default="SAMEORIGIN")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = get_env(
    "SESSION_COOKIE_SECURE",
    default=not DEBUG,
    cast=bool,
)
CSRF_COOKIE_SECURE = get_env(
    "CSRF_COOKIE_SECURE",
    default=not DEBUG,
    cast=bool,
)

ROOT_URLCONF = "hello_world.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "hello_world" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "hello_world.wsgi.application"
ASGI_APPLICATION = "hello_world.asgi.application"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

if IS_CLOUDFLARE_WORKER:
    DATABASES = {
        "default": {
            # Supports "django_cf.db.backends.d1" or "django_cf.d1_binding"
            "ENGINE": get_env(
                "CF_DB_ENGINE",
                default="django_cf.db.backends.d1",
            ),
            "CLOUDFLARE_BINDING": get_env("CF_D1_BINDING", default="DB"),
        }
    }
elif get_env("USE_CLOUDFLARE_D1_API", default=False, cast=bool):
    DATABASES = {
        "default": {
            "ENGINE": "django_cf.d1_api",
            "CLOUDFLARE_DATABASE_ID": get_env(
                "CLOUDFLARE_DATABASE_ID",
                default="",
            ),
            "CLOUDFLARE_ACCOUNT_ID": get_env(
                "CLOUDFLARE_ACCOUNT_ID",
                default="",
            ),
            "CLOUDFLARE_TOKEN": get_env("CLOUDFLARE_TOKEN", default=""),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": REPO_ROOT / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

_VALIDATOR_PREFIX = "django.contrib.auth.password_validation"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"{_VALIDATOR_PREFIX}.UserAttributeSimilarityValidator"},
    {"NAME": f"{_VALIDATOR_PREFIX}.MinimumLengthValidator"},
    {"NAME": f"{_VALIDATOR_PREFIX}.CommonPasswordValidator"},
    {"NAME": f"{_VALIDATOR_PREFIX}.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = get_env("LANGUAGE_CODE", default="en-us")

TIME_ZONE = get_env("TIME_ZONE", default="UTC")

USE_I18N = True

USE_TZ = True


# Static files & Storage (Workers Static Assets + optional Cloudflare R2)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATICFILES_DIRS = [
    BASE_DIR / "hello_world" / "static",
]

STATIC_URL = "/static/"
STATIC_ROOT = REPO_ROOT / "staticfiles" / "static"

MEDIA_URL = "/media/"
MEDIA_ROOT = REPO_ROOT / "media"

if USE_CLOUDFLARE_R2:
    STORAGES = {
        "default": {
            "BACKEND": "django_cf.storage.R2Storage",
            "OPTIONS": {
                "binding": get_env("CF_R2_BINDING", default="BUCKET"),
                "location": get_env("CF_R2_MEDIA_LOCATION", default="media"),
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


# Multi-tier Cloudflare Caching (avoids LocMemCache inside V8 isolate RAM)

LOCAL_CACHE_ROOT = Path(
    get_env(
        "LOCAL_CACHE_DIR",
        default=str(Path(tempfile.gettempdir()) / "django_cf_cache"),
    )
)
CF_CACHE_TIMEOUT = get_env("CF_CACHE_TIMEOUT", default=300, cast=int)
DEFAULT_CACHE_TIER = get_env("CF_DEFAULT_CACHE_BACKEND", default="r2").lower()

_CACHE_TIERS: dict[str, dict[str, Any]] = {
    "r2": {
        "BACKEND": "hello_world.core.cache.R2Cache",
        "LOCATION": get_env("CF_R2_CACHE_PREFIX", default="cache"),
        "TIMEOUT": get_env("CF_R2_CACHE_TIMEOUT", default=3600, cast=int),
        "OPTIONS": {
            "binding": get_env("CF_R2_CACHE_BINDING", default="BUCKET"),
        },
    },
    "do": {
        "BACKEND": "hello_world.core.cache.DurableObjectCache",
        "LOCATION": get_env("CF_DO_CACHE_LOCATION", default="do-cache"),
        "TIMEOUT": get_env("CF_DO_CACHE_TIMEOUT", default=300, cast=int),
        "OPTIONS": {
            "binding": get_env("CF_DO_CACHE_BINDING", default="CACHE_DO"),
        },
    },
    "kv": {
        "BACKEND": "hello_world.core.cache.CloudflareKVCache",
        "LOCATION": get_env("CF_KV_CACHE_LOCATION", default="kv-cache"),
        "TIMEOUT": get_env("CF_KV_CACHE_TIMEOUT", default=600, cast=int),
        "OPTIONS": {
            "binding": get_env("CF_KV_CACHE_BINDING", default="CACHE_KV"),
        },
    },
    "db": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": get_env(
            "CF_DB_CACHE_LOCATION",
            default="django_cache_table",
        ),
        "TIMEOUT": get_env("CF_DB_CACHE_TIMEOUT", default=300, cast=int),
    },
}

_selected_default_cache = _CACHE_TIERS.get(
    DEFAULT_CACHE_TIER,
    _CACHE_TIERS["r2"],
).copy()
_selected_default_cache["TIMEOUT"] = CF_CACHE_TIMEOUT

CACHES = {
    "default": _selected_default_cache,
    **_CACHE_TIERS,
}

# Store sessions in Durable Objects/R2 on Workers instead of V8 RAM.
if IS_CLOUDFLARE_WORKER:
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = get_env("SESSION_CACHE_ALIAS", default="do")
else:
    SESSION_ENGINE = "django.contrib.sessions.backends.db"


# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
