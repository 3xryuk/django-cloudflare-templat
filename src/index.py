"""Cloudflare Python Worker entrypoint for Django via django-cf."""
import os
from pathlib import Path
import sys
from typing import Any

# Disable bytecode emission before importing project modules.
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hello_world.settings")

_cached_application: Any = None


def _sync_worker_env(env: Any) -> None:
    """Sync Worker `env` variables into `os.environ` and cache bindings."""
    if env is None:
        try:
            from workers import env as workers_env

            env = workers_env
        except ImportError:
            env = None

    if env is not None:
        raw_env = getattr(env, "_env", env)
        if isinstance(raw_env, dict):
            keys = list(raw_env.keys())
        else:
            try:
                from js import Object

                keys = list(Object.keys(raw_env))
            except Exception:
                keys = [k for k in dir(raw_env) if not k.startswith("_")]

        for key in keys:
            try:
                if isinstance(env, dict):
                    val = env[key]
                else:
                    val = getattr(env, key, None)
            except Exception:
                continue
            if isinstance(val, (str, bool, int, float)):
                os.environ[str(key)] = str(val)

    from hello_world.core.cache import set_worker_env

    set_worker_env(env)


def _get_wsgi_application(env: Any) -> Any:  # Any: returns Django WSGIHandler
    """Sync Worker env first, then lazily load Django's WSGI application."""
    global _cached_application
    _sync_worker_env(env)
    if _cached_application is None:
        from hello_world.wsgi import application

        _cached_application = application
    return _cached_application


try:
    from django_cf import DjangoCF, DjangoCFDurableObject
    from workers import DurableObject, WorkerEntrypoint

    class CacheDurableObject(DurableObject):
        """Durable Object backing cache, sessions, and atomic counters."""

        async def cache_get(self, key: str) -> str | None:
            return await self.ctx.storage.get(key)

        async def cache_set(self, key: str, value: str) -> None:
            await self.ctx.storage.put(key, value)

        async def cache_delete(self, key: str) -> bool:
            return bool(await self.ctx.storage.delete(key))

        async def cache_clear(self) -> None:
            await self.ctx.storage.deleteAll()

    class DjangoDO(DjangoCFDurableObject, DurableObject):
        """Durable Object Django entrypoint for django_cf.db.backends.do."""

        def get_app(self) -> Any:
            return _get_wsgi_application(getattr(self, "env", None))

    class Default(DjangoCF, WorkerEntrypoint):
        """Main Worker entrypoint bridging requests to Django WSGI."""

        def get_app(self) -> Any:
            return _get_wsgi_application(getattr(self, "env", None))

except ImportError:
    # Fallback for local inspection outside Cloudflare Worker runtime
    CacheDurableObject = None
    DjangoDO = None
    Default = None
