import os
from pathlib import Path
import sys
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import DeploymentNote


class CoreTests(TestCase):
    """Tests for models, views, management endpoints, and Cloudflare caches."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="admin_test",
            email="admin@example.com",
            password="StrongTestPassword123!",
        )
        self.regular_user = user_model.objects.create_user(
            username="user_test",
            email="user@example.com",
            password="StrongTestPassword123!",
        )
        for alias in ("r2", "do", "kv"):
            caches[alias].clear()

    def tearDown(self) -> None:
        for alias in ("r2", "do", "kv"):
            caches[alias].clear()

    def test_deployment_note_model(self) -> None:
        note = DeploymentNote.objects.create(
            title="Initial D1 setup",
            environment=DeploymentNote.Environment.WORKERS,
        )
        self.assertTrue(note.is_active)
        self.assertEqual(str(note), "Initial D1 setup (workers)")

    def test_index_view_returns_200(self) -> None:
        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "django-cf")
        self.assertContains(response, "R2Cache")

    def test_favicon_redirects_to_static_icon(self) -> None:
        response = self.client.get(reverse("favicon"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/static/Octocat.png")

    def test_healthz_view_returns_expected_json(self) -> None:
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn(payload["runtime"], ("local", "cloudflare-workers"))
        self.assertIn("db_engine", payload)
        self.assertEqual(
            payload["cache_backend"],
            settings.CACHES["default"]["BACKEND"],
        )
        self.assertEqual(payload["cache_tiers"], list(settings.CACHES.keys()))
        self.assertTrue(payload["bytecode_disabled"])

    def test_run_migrations_requires_superuser_or_token(self) -> None:
        url = reverse("run_migrations")

        # Unauthenticated request is rejected
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Non-superuser is rejected
        self.client.force_login(self.regular_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Superuser session succeeds
        self.client.force_login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @override_settings(CF_MANAGEMENT_TOKEN="secret-cf-token")
    def test_run_migrations_accepts_management_token(self) -> None:
        url = reverse("run_migrations")
        response = self.client.get(
            url,
            HTTP_X_CF_MANAGEMENT_TOKEN="secret-cf-token",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_r2_cache_operations_and_serialization(self) -> None:
        r2_cache = caches["r2"]
        complex_payload = {
            "items": [1, 2, {"nested": True}],
            "tags": ("edge", "r2"),
        }
        r2_cache.set("r2:complex", complex_payload, timeout=60)
        self.assertEqual(r2_cache.get("r2:complex"), complex_payload)

        # TTL=0 immediately expires entry
        r2_cache.set("r2:expired", "stale", timeout=0)
        self.assertIsNone(r2_cache.get("r2:expired"))

        # Delete removes entry
        self.assertTrue(r2_cache.delete("r2:complex"))
        self.assertIsNone(r2_cache.get("r2:complex"))

    def test_durable_object_cache_operations_and_counters(self) -> None:
        do_cache = caches["do"]
        do_cache.set("do:session", {"user_id": 42}, timeout=60)
        self.assertEqual(do_cache.get("do:session"), {"user_id": 42})

        do_cache.set("do:hits", 10, timeout=60)
        self.assertEqual(do_cache.incr("do:hits", 5), 15)
        self.assertEqual(do_cache.decr("do:hits", 3), 12)

        self.assertTrue(do_cache.delete("do:session"))
        self.assertIsNone(do_cache.get("do:session"))

    def test_kv_cache_operations_and_ttl(self) -> None:
        kv_cache = caches["kv"]
        kv_cache.set("kv:feature_flag", {"beta": True}, timeout=120)
        self.assertTrue(kv_cache.has_key("kv:feature_flag"))
        self.assertEqual(kv_cache.get("kv:feature_flag"), {"beta": True})

        kv_cache.set("kv:expired", "gone", timeout=0)
        self.assertIsNone(kv_cache.get("kv:expired"))

        self.assertTrue(kv_cache.delete("kv:feature_flag"))
        self.assertFalse(kv_cache.has_key("kv:feature_flag"))

    def test_bytecode_disabled_and_src_clean_of_pycache(self) -> None:
        self.assertTrue(sys.dont_write_bytecode)
        src_root = Path(settings.BASE_DIR)
        pycache_dirs = list(src_root.rglob("__pycache__"))
        self.assertEqual(pycache_dirs, [])

    def test_secret_key_loaded_and_non_empty(self) -> None:
        self.assertTrue(bool(settings.SECRET_KEY))
        from hello_world import settings as project_settings

        dev_vars = Path(settings.REPO_ROOT) / ".dev.vars"
        if dev_vars.is_file():
            parsed = project_settings._parse_env_file(dev_vars)
            if parsed.get("SECRET_KEY"):
                self.assertEqual(settings.SECRET_KEY, parsed["SECRET_KEY"])

    def test_get_env_fallback_on_empty_string(self) -> None:
        from hello_world import settings as project_settings

        with patch.dict(os.environ, {"EMPTY_TEST_KEY": ""}, clear=False):
            resolved = project_settings.get_env(
                "EMPTY_TEST_KEY",
                default="fallback-value",
            )
            self.assertEqual(resolved, "fallback-value")
