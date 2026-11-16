"""Multi-tier Cloudflare cache backends for Django (R2, DO, Workers KV).

Designed to avoid Django's default LocMemCache in Cloudflare Python Workers
(128 MB V8 isolate memory limit). When running outside the Cloudflare Workers
runtime (e.g. local dev or tests), entries fall back to `/tmp/django_cf_cache`
so `src/` never accumulates cache files.
"""

import asyncio
import base64
from collections.abc import Callable
import contextvars
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import pickle
import tempfile
import threading
import time
from typing import Any

from django.conf import settings
from django.core.cache.backends.base import DEFAULT_TIMEOUT, BaseCache

logger = logging.getLogger(__name__)

_worker_env_var: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "cf_worker_env",
    default=None,
)
_fallback_worker_env: Any = None

DEFAULT_LOCAL_CACHE_ROOT = Path(tempfile.gettempdir()) / "django_cf_cache"


def _get_local_cache_root() -> Path:
    """Resolve the local fallback cache root from settings or system temp."""
    configured = getattr(settings, "LOCAL_CACHE_ROOT", None)
    if configured:
        return Path(configured)
    return DEFAULT_LOCAL_CACHE_ROOT


def set_worker_env(env: Any) -> None:
    """Register the active Cloudflare Worker `env` for cache bindings."""
    global _fallback_worker_env
    _worker_env_var.set(env)
    _fallback_worker_env = env


def get_worker_env() -> Any:
    """Return the active Cloudflare Worker `env` object, or None locally."""
    env = _worker_env_var.get()
    if env is not None:
        return env
    if _fallback_worker_env is not None:
        return _fallback_worker_env
    try:
        from workers import env as workers_env

        return workers_env
    except ImportError:
        return None


def _resolve_awaitable(value: Any) -> Any:
    """Resolve JS/Python awaitables returned by Cloudflare Worker bindings."""
    if not (
        inspect.isawaitable(value)
        or (hasattr(value, "then") and callable(value.then))
    ):
        return value

    try:
        from pyodide.ffi import run_sync

        return run_sync(value)
    except ImportError:
        pass

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    if hasattr(value, "sync"):
        return value.sync()
    raise RuntimeError(
        "Cannot synchronously await Worker binding inside a running "
        "event loop; use Django's async cache methods (aget/aset) instead."
    )


class _CloudflareBaseCache(BaseCache):
    """Shared base backend handling serialization, TTL, and /tmp fallback."""

    tier_name = "base"

    def __init__(self, location: str, params: dict[str, Any]) -> None:
        super().__init__(params)
        options = params.get("OPTIONS") or {}
        self._location = (location or "cache").strip("/")
        self._binding_name = str(options.get("binding", ""))
        subdir = self._location or "default"
        self._local_dir = _get_local_cache_root() / self.tier_name / subdir
        self._lock = threading.RLock()

    def _get_binding(self) -> Any:
        env = get_worker_env()
        if env is None or not self._binding_name:
            return None
        try:
            if isinstance(env, dict):
                return env.get(self._binding_name)
            return getattr(env, self._binding_name, None)
        except Exception:
            return None

    def _call_with_fallback(
        self,
        remote_fn: Callable[[Any], Any],
        local_fn: Callable[[], Any],
    ) -> tuple[Any, bool]:
        binding = self._get_binding()
        if binding is not None:
            try:
                return remote_fn(binding), True
            except Exception:
                logger.warning(
                    "Cloudflare %s binding '%s' unavailable; using /tmp.",
                    self.tier_name,
                    self._binding_name,
                    exc_info=True,
                )
        return local_fn(), False

    def _storage_key(self, key: Any, version: int | None = None) -> str:
        full_key = self.make_and_validate_key(key, version=version)
        digest = hashlib.sha256(full_key.encode("utf-8")).hexdigest()
        prefix = f"{self._location}/" if self._location else ""
        return f"{prefix}{digest}.json"

    def _local_path(self, storage_key: str) -> Path:
        safe_name = storage_key.replace("/", "__")
        return self._local_dir / safe_name

    def _encode_entry(
        self,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> tuple[str, float | None]:
        expires_at = self.get_backend_timeout(timeout)
        pickled = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        envelope = {
            "expires_at": expires_at,
            "payload": base64.b64encode(pickled).decode("ascii"),
        }
        return json.dumps(envelope, separators=(",", ":")), expires_at

    def _decode_entry(self, raw_data: str | bytes | None) -> tuple[Any, bool]:
        if raw_data is None:
            return self._missing_key, False
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        envelope = json.loads(raw_data)
        expires_at = envelope.get("expires_at")
        if expires_at is not None and time.time() >= expires_at:
            return self._missing_key, True
        payload = base64.b64decode(envelope["payload"].encode("ascii"))
        return pickle.loads(payload), False

    # --- Local /tmp fallback operations ---

    def _local_read(self, storage_key: str) -> str | None:
        path = self._local_path(storage_key)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def _local_write(self, storage_key: str, raw_envelope: str) -> None:
        self._local_dir.mkdir(parents=True, exist_ok=True)
        path = self._local_path(storage_key)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(raw_envelope, encoding="utf-8")
        os.replace(tmp_path, path)

    def _local_delete(self, storage_key: str) -> bool:
        path = self._local_path(storage_key)
        if path.exists():
            path.unlink()
            return True
        return False

    def _local_clear(self) -> None:
        if not self._local_dir.exists():
            return
        for entry in self._local_dir.iterdir():
            if entry.is_file():
                entry.unlink()

    # --- Subclass transport hooks ---

    def _remote_read(
        self,
        binding: Any,
        storage_key: str,
    ) -> str | bytes | None:
        raise NotImplementedError

    def _remote_write(
        self,
        binding: Any,
        storage_key: str,
        raw_envelope: str,
        expires_at: float | None,
    ) -> None:
        raise NotImplementedError

    def _remote_delete(self, binding: Any, storage_key: str) -> bool:
        raise NotImplementedError

    def _remote_clear(self, binding: Any) -> None:
        raise NotImplementedError

    # --- Django BaseCache API ---

    def get(
        self,
        key: Any,
        default: Any = None,
        version: int | None = None,
    ) -> Any:
        storage_key = self._storage_key(key, version=version)
        with self._lock:
            raw, used_remote = self._call_with_fallback(
                lambda b: self._remote_read(b, storage_key),
                lambda: self._local_read(storage_key),
            )
            if raw is None:
                return default
            value, expired = self._decode_entry(raw)
            if expired:
                if used_remote:
                    self._call_with_fallback(
                        lambda b: self._remote_delete(b, storage_key),
                        lambda: self._local_delete(storage_key),
                    )
                else:
                    self._local_delete(storage_key)
                return default
            return value

    def set(
        self,
        key: Any,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        storage_key = self._storage_key(key, version=version)
        raw_envelope, expires_at = self._encode_entry(value, timeout=timeout)
        with self._lock:
            self._call_with_fallback(
                lambda b: self._remote_write(
                    b,
                    storage_key,
                    raw_envelope,
                    expires_at,
                ),
                lambda: self._local_write(storage_key, raw_envelope),
            )

    def add(
        self,
        key: Any,
        value: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        with self._lock:
            existing = self.get(key, self._missing_key, version=version)
            if existing is not self._missing_key:
                return False
            self.set(key, value, timeout=timeout, version=version)
            return True

    def touch(
        self,
        key: Any,
        timeout: float | None = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        with self._lock:
            value = self.get(key, self._missing_key, version=version)
            if value is self._missing_key:
                return False
            self.set(key, value, timeout=timeout, version=version)
            return True

    def delete(self, key: Any, version: int | None = None) -> bool:
        storage_key = self._storage_key(key, version=version)
        with self._lock:
            result, _ = self._call_with_fallback(
                lambda b: self._remote_delete(b, storage_key),
                lambda: self._local_delete(storage_key),
            )
            return bool(result)

    def has_key(self, key: Any, version: int | None = None) -> bool:
        val = self.get(key, self._missing_key, version=version)
        return val is not self._missing_key

    def clear(self) -> None:
        with self._lock:
            self._call_with_fallback(
                self._remote_clear,
                self._local_clear,
            )


class R2Cache(_CloudflareBaseCache):
    """Cloudflare R2-backed cache for templates, payloads, and blobs."""

    tier_name = "r2"

    def _remote_read(self, binding: Any, storage_key: str) -> str | None:
        obj = _resolve_awaitable(binding.get(storage_key))
        if obj is None:
            return None
        if hasattr(obj, "text"):
            return str(_resolve_awaitable(obj.text()))
        return bytes(_resolve_awaitable(obj.arrayBuffer())).decode("utf-8")

    def _remote_write(
        self,
        binding: Any,
        storage_key: str,
        raw_envelope: str,
        expires_at: float | None,
    ) -> None:
        payload: Any = raw_envelope.encode("utf-8")
        try:
            from js import Uint8Array

            payload = Uint8Array.new(payload)
        except ImportError:
            pass
        _resolve_awaitable(binding.put(storage_key, payload))

    def _remote_delete(self, binding: Any, storage_key: str) -> bool:
        _resolve_awaitable(binding.delete(storage_key))
        return True

    def _remote_clear(self, binding: Any) -> None:
        prefix = f"{self._location}/" if self._location else ""
        listed = _resolve_awaitable(
            binding.list({"prefix": prefix}) if prefix else binding.list()
        )
        objects = (
            listed.get("objects", [])
            if isinstance(listed, dict)
            else (getattr(listed, "objects", None) or [])
        )
        keys = [
            obj.get("key")
            if isinstance(obj, dict)
            else getattr(obj, "key", None)
            for obj in objects
        ]
        valid_keys = [k for k in keys if k]
        if valid_keys:
            _resolve_awaitable(binding.delete(valid_keys))


class DurableObjectCache(_CloudflareBaseCache):
    """Durable Object cache for consistent state, sessions, and counters."""

    tier_name = "do"

    def _get_stub(self, binding: Any) -> Any:
        obj_id = binding.idFromName(self._location or "default")
        return binding.get(obj_id)

    def _remote_read(
        self,
        binding: Any,
        storage_key: str,
    ) -> str | bytes | None:
        stub = self._get_stub(binding)
        return _resolve_awaitable(stub.cache_get(storage_key))

    def _remote_write(
        self,
        binding: Any,
        storage_key: str,
        raw_envelope: str,
        expires_at: float | None,
    ) -> None:
        stub = self._get_stub(binding)
        _resolve_awaitable(stub.cache_set(storage_key, raw_envelope))

    def _remote_delete(self, binding: Any, storage_key: str) -> bool:
        stub = self._get_stub(binding)
        return bool(_resolve_awaitable(stub.cache_delete(storage_key)))

    def _remote_clear(self, binding: Any) -> None:
        stub = self._get_stub(binding)
        _resolve_awaitable(stub.cache_clear())

    def incr(
        self,
        key: Any,
        delta: int = 1,
        version: int | None = None,
    ) -> int:
        with self._lock:
            return int(super().incr(key, delta=delta, version=version))

    def decr(
        self,
        key: Any,
        delta: int = 1,
        version: int | None = None,
    ) -> int:
        return self.incr(key, delta=-delta, version=version)


class CloudflareKVCache(_CloudflareBaseCache):
    """Cloudflare Workers KV cache for edge-replicated, read-heavy lookups."""

    tier_name = "kv"

    def _remote_read(
        self,
        binding: Any,
        storage_key: str,
    ) -> str | bytes | None:
        return _resolve_awaitable(binding.get(storage_key))

    def _remote_write(
        self,
        binding: Any,
        storage_key: str,
        raw_envelope: str,
        expires_at: float | None,
    ) -> None:
        if expires_at is not None:
            # Cloudflare KV requires expirationTtl >= 60 seconds when provided.
            ttl = max(60, int(expires_at - time.time()))
            _resolve_awaitable(
                binding.put(storage_key, raw_envelope, {"expirationTtl": ttl})
            )
        else:
            _resolve_awaitable(binding.put(storage_key, raw_envelope))

    def _remote_delete(self, binding: Any, storage_key: str) -> bool:
        _resolve_awaitable(binding.delete(storage_key))
        return True

    def _remote_clear(self, binding: Any) -> None:
        prefix = f"{self._location}/" if self._location else ""
        listed = _resolve_awaitable(
            binding.list({"prefix": prefix}) if prefix else binding.list()
        )
        keys = (
            listed.get("keys", [])
            if isinstance(listed, dict)
            else (getattr(listed, "keys", None) or [])
        )
        for entry in keys:
            key_name = (
                entry.get("name")
                if isinstance(entry, dict)
                else getattr(entry, "name", None)
            )
            if key_name:
                _resolve_awaitable(binding.delete(key_name))
