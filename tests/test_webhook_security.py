"""Regression tests for issue #1.

Covers:
- /webhooks/incoming/{webhook_type} must reject unauthenticated posts.
- Default config must not ship wildcard CORS.

Run from repo root:
    PYTHONPATH=. venv/Scripts/python.exe -m unittest tests.test_webhook_security
"""

import pathlib
import unittest

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import router
from backend.config import config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SECRET = "test-secret-123"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class WebhookAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        # Known shared secret for the happy path; reset before each test.
        config.set("server", "webhook_secret", value=SECRET)

    def test_missing_secret_rejected(self) -> None:
        r = _client().post("/webhooks/incoming/slack", json={"hello": "world"})
        self.assertIn(r.status_code, (401, 403), r.text)

    def test_wrong_secret_rejected(self) -> None:
        r = _client().post(
            "/webhooks/incoming/slack",
            json={"hello": "world"},
            headers={"X-GMini-Webhook-Secret": "wrong"},
        )
        self.assertIn(r.status_code, (401, 403), r.text)

    def test_valid_secret_accepted(self) -> None:
        r = _client().post(
            "/webhooks/incoming/slack",
            json={"hello": "world"},
            headers={"X-GMini-Webhook-Secret": SECRET},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("ok"))

    def test_disabled_when_no_secret_configured(self) -> None:
        config.set("server", "webhook_secret", value="")
        r = _client().post(
            "/webhooks/incoming/slack",
            json={"hello": "world"},
            headers={"X-GMini-Webhook-Secret": "anything"},
        )
        self.assertIn(r.status_code, (401, 403), r.text)


class CorsDefaultTests(unittest.TestCase):
    def test_default_cors_not_wildcard(self) -> None:
        data = yaml.safe_load((REPO_ROOT / "config.default.yaml").read_text(encoding="utf-8"))
        origins = data["server"]["cors_origins"]
        self.assertNotIn("*", origins, "Default config must not ship wildcard CORS")
        self.assertTrue(origins, "CORS origins must be a non-empty allowlist")


if __name__ == "__main__":
    unittest.main()
