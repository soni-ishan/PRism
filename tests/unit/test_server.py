"""
Tests for the PRism Webhook Server (FastAPI).
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from agents.orchestrator.server import app, _parse_github_webhook, _verify_signature, USAGE_TRACKER
from agents.shared.data_contract import VerdictReport


# ── Helpers ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_usage_tracker():
    """Reset freemium counter so unit tests are isolated."""
    USAGE_TRACKER.clear()
    yield
    USAGE_TRACKER.clear()


MOCK_VERDICT = VerdictReport(
    confidence_score=75,
    decision="greenlight",
    risk_brief="Looks good.",
    rollback_playbook=None,
    agent_results=[],
)


# ── Webhook parsing tests ────────────────────────────────────────────


class TestWebhookParsing:
    def test_parse_opened_event(self):
        body = {
            "action": "opened",
            "number": 46,
            "pull_request": {"number": 46},
            "repository": {"full_name": "team-prism/backend"},
        }
        payload = _parse_github_webhook(body)
        assert payload is not None
        assert payload.pr_number == 46
        assert payload.repo == "team-prism/backend"

    def test_parse_synchronize_event(self):
        body = {
            "action": "synchronize",
            "pull_request": {"number": 10},
            "repository": {"full_name": "org/repo"},
        }
        payload = _parse_github_webhook(body)
        assert payload is not None
        assert payload.pr_number == 10

    def test_parse_closed_event_returns_none(self):
        body = {
            "action": "closed",
            "pull_request": {"number": 10},
            "repository": {"full_name": "org/repo"},
        }
        payload = _parse_github_webhook(body)
        assert payload is None

    def test_parse_reopened_event(self):
        body = {
            "action": "reopened",
            "pull_request": {"number": 7},
            "repository": {"full_name": "org/repo"},
        }
        payload = _parse_github_webhook(body)
        assert payload is not None


# ── Signature verification tests ─────────────────────────────────────


class TestSignature:
    def test_no_secret_configured_passes(self):
        assert _verify_signature(b"body", None) is True

    @patch("agents.orchestrator.server._WEBHOOK_SECRET", "mysecret")
    def test_missing_signature_fails(self):
        assert _verify_signature(b"body", None) is False

    @patch("agents.orchestrator.server._WEBHOOK_SECRET", "mysecret")
    def test_valid_signature_passes(self):
        import hashlib
        import hmac

        body = b'{"test": true}'
        sig = "sha256=" + hmac.new(b"mysecret", body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig) is True

    @patch("agents.orchestrator.server._WEBHOOK_SECRET", "mysecret")
    def test_invalid_signature_fails(self):
        assert _verify_signature(b"body", "sha256=wrong") is False


# ── API endpoint tests ───────────────────────────────────────────────

client = TestClient(app)


class TestAPIEndpoints:
    def test_healthcheck(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("agents.orchestrator.server._lookup_repo_context", new_callable=AsyncMock, return_value=None)
    @patch("agents.orchestrator.server._build_fallback_context", return_value=None)
    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_endpoint(self, mock_orchestrate, _mock_fallback, _mock_lookup):
        mock_orchestrate.return_value = MOCK_VERDICT

        resp = client.post(
            "/analyze",
            json={
                "pr_number": 46,
                "repo": "team-prism/backend",
                "changed_files": ["payment_service.py"],
                "diff": "- old\n+ new",
            },
            headers={"X-Client-ID": "unit-test-client"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence_score"] == 75
        assert data["decision"] == "greenlight"

    def test_webhook_ignores_non_pr_event(self):
        resp = client.post(
            "/webhook/pr",
            json={"action": "created"},
            headers={"x-github-event": "issues"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_webhook_ignores_closed_action(self):
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "closed",
                "pull_request": {"number": 1},
                "repository": {"full_name": "org/repo"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
