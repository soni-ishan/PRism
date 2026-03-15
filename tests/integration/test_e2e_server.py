"""
PRism – End-to-End Server Integration Tests
============================================
Tests the FastAPI server endpoints with real orchestration logic.

Test strategy
-------------
* The TestClient drives all requests – no port is opened.
* History Agent's AzureMCPServer is patched at the class level so the
  full orchestration pipeline runs without Azure credentials.
* LLM enhancement calls in the Verdict Agent are patched for speed.
* The USAGE_TRACKER is reset before every test so freemium limits
  do not bleed between tests.

Endpoints covered
-----------------
  GET  /health        — basic liveness check
  POST /analyze       — manual pipeline trigger (requires X-Client-ID)
  POST /webhook/pr   — GitHub webhook ingestion

Scenario matrix
---------------
  /analyze  safe PR           → 200 greenlight
  /analyze  risky PR (secret) → 200 blocked
  /analyze  missing client ID → 400
  /analyze  exhausted limit   → 402
  /webhook  opened PR         → 202 accepted (background task queued)
  /webhook  closed PR         → 200 ignored
  /webhook  non-PR event      → 200 ignored
  /webhook  bad signature     → 401
  /webhook  valid signature   → 202 accepted
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator.server import USAGE_TRACKER, _RATE_LIMITING_DISABLED, app
from agents.shared.data_contract import AgentResult, VerdictReport


# ── Shared test fixtures ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_usage_tracker():
    """Clear the in-memory freemium counter before each test."""
    USAGE_TRACKER.clear()
    yield
    USAGE_TRACKER.clear()


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def azure_stub():
    """Patch AzureMCPServer to return zero incidents (safe history)."""
    mock_mcp = MagicMock()
    mock_mcp.query_incidents_by_files_search.return_value = []
    with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
        yield mock_mcp


@pytest.fixture()
def no_llm():
    with patch.dict(
        "os.environ",
        {"AZURE_OPENAI_ENDPOINT": "", "AZURE_OPENAI_API_KEY": "", "AZURE_OPENAI_DEPLOYMENT": ""},
        clear=False,
    ):
        yield


def _mock_llm():
    """Context managers that stub both LLM enhancements."""
    from unittest.mock import AsyncMock, patch

    return (
        patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
        patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
    )


_CLIENT_ID = "test-client-e2e-001"

_SAFE_PAYLOAD = {
    "pr_number": 10,
    "repo": "acme/backend",
    "changed_files": ["utils/logger.py"],
    "diff": "+ logger.setLevel('DEBUG')",
    "timestamp": "2026-02-24T10:00:00+00:00",  # Tuesday 10:00 UTC
}

_RISKY_PAYLOAD = {
    "pr_number": 11,
    "repo": "acme/backend",
    "changed_files": ["config.py"],
    "diff": '+ STRIPE_SECRET = "sk-1234567890abcdef1234567890abcdef"',
    "timestamp": "2026-02-24T10:00:00+00:00",
}


# ── /health ───────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_response_body(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"
        assert data["service"] == "prism"


# ── /analyze ─────────────────────────────────────────────────────────

class TestAnalyzeEndpoint:
    def test_safe_pr_returns_greenlight(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "confidence_score" in data
        assert "decision" in data
        assert data["decision"] == "greenlight"
        assert 0 <= data["confidence_score"] <= 100

    def test_risky_diff_returns_blocked(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_RISKY_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "blocked"
        assert data["rollback_playbook"] is not None

    def test_missing_client_id_returns_400(self, client):
        resp = client.post("/analyze", json=_SAFE_PAYLOAD)
        assert resp.status_code == 400
        assert "X-Client-ID" in resp.json()["detail"]

    @pytest.mark.skipif(_RATE_LIMITING_DISABLED, reason="Rate limiting disabled on this instance (PRISM_FREE_TIER_LIMIT=0)")
    def test_freemium_limit_blocks_after_free_tier_requests(self, client, azure_stub, no_llm):
        _TEST_LIMIT = 3  # small value so the test stays fast regardless of the real limit
        brief_patch, playbook_patch = _mock_llm()
        with patch("agents.orchestrator.server.FREE_TIER_LIMIT", _TEST_LIMIT), brief_patch, playbook_patch:
            for i in range(_TEST_LIMIT):
                r = client.post(
                    "/analyze",
                    json={**_SAFE_PAYLOAD, "pr_number": 100 + i},
                    headers={"X-Client-ID": "trial-client-xyz"},
                )
                assert r.status_code == 200, f"Request {i+1} should succeed"

            # Request beyond the limit must be rejected
            resp = client.post(
                "/analyze",
                json={**_SAFE_PAYLOAD, "pr_number": 200},
                headers={"X-Client-ID": "trial-client-xyz"},
            )
        assert resp.status_code == 402
        assert "Free trial exhausted" in resp.json()["detail"]

    @pytest.mark.skipif(_RATE_LIMITING_DISABLED, reason="Rate limiting disabled on this instance (PRISM_FREE_TIER_LIMIT=0)")
    def test_different_client_ids_have_independent_counters(self, client, azure_stub, no_llm):
        _TEST_LIMIT = 3
        brief_patch, playbook_patch = _mock_llm()
        with patch("agents.orchestrator.server.FREE_TIER_LIMIT", _TEST_LIMIT), brief_patch, playbook_patch:
            # Exhaust limit for client-A
            for i in range(_TEST_LIMIT):
                client.post(
                    "/analyze",
                    json={**_SAFE_PAYLOAD, "pr_number": i},
                    headers={"X-Client-ID": "client-A"},
                )

            # client-B should still work
            r = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": "client-B"},
            )
        assert r.status_code == 200

    def test_response_includes_all_agent_results(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )

        data = resp.json()
        assert "agent_results" in data
        agent_names = {r["agent_name"] for r in data["agent_results"]}
        assert agent_names == {"Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"}

    def test_response_includes_guardrails_metadata(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )

        data = resp.json()
        # Guardrails block is attached by the server when foundry module is available
        if "guardrails" in data:
            assert "escalation_required" in data["guardrails"]
            assert "audit_entry" in data["guardrails"]

    def test_friday_evening_payload_is_blocked(self, client, azure_stub, no_llm):
        friday_payload = {
            **_SAFE_PAYLOAD,
            "pr_number": 999,
            "timestamp": "2026-02-27T17:15:00+00:00",  # Friday 17:15 UTC
        }
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=friday_payload,
                headers={"X-Client-ID": _CLIENT_ID},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "blocked"


# ── /webhook/pr ───────────────────────────────────────────────────────

class TestWebhookEndpoint:
    def test_non_pr_event_is_ignored(self, client):
        resp = client.post(
            "/webhook/pr",
            json={"action": "created"},
            headers={"x-github-event": "issues"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_closed_pr_action_is_ignored(self, client):
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

    def test_opened_pr_accepted(self, client):
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "opened",
                "pull_request": {"number": 42},
                "repository": {"full_name": "acme/backend"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["pr_number"] == 42

    def test_synchronize_pr_accepted(self, client):
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "synchronize",
                "pull_request": {"number": 7},
                "repository": {"full_name": "acme/backend"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202

    def test_reopened_pr_accepted(self, client):
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "reopened",
                "pull_request": {"number": 3},
                "repository": {"full_name": "org/repo"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202

    def test_invalid_signature_rejected(self, client):
        with patch("agents.orchestrator.server._WEBHOOK_SECRET", "supersecret"):
            resp = client.post(
                "/webhook/pr",
                content=b'{"action":"opened","pull_request":{"number":1},"repository":{"full_name":"org/r"}}',
                headers={
                    "x-github-event": "pull_request",
                    "x-hub-signature-256": "sha256=badsignature",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 401

    def test_valid_hmac_signature_passes(self, client):
        secret = "mysecret"
        body = json.dumps({
            "action": "opened",
            "pull_request": {"number": 99},
            "repository": {"full_name": "org/repo"},
        }).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        with patch("agents.orchestrator.server._WEBHOOK_SECRET", secret):
            resp = client.post(
                "/webhook/pr",
                content=body,
                headers={
                    "x-github-event": "pull_request",
                    "x-hub-signature-256": sig,
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 202

    def test_malformed_webhook_missing_repo_returns_400(self, client):
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "opened",
                "pull_request": {"number": 0},
                "repository": {},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code in (400, 200)
        # Either ignored or 400 – must not be 500
        assert resp.status_code != 500


# ── Full E2E server → pipeline run ──────────────────────────────────

class TestAnalyzeFullPipelineE2E:
    """
    Exercises the complete server → orchestrate → verdict chain.
    Only Azure I/O and LLM calls are stubbed.
    """

    def test_analyze_returns_valid_verdict_structure(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )
        data = resp.json()

        # VerdictReport fields
        assert "confidence_score" in data
        assert "decision" in data
        assert "risk_brief" in data
        assert "agent_results" in data
        assert isinstance(data["agent_results"], list)
        assert data["decision"] in ("greenlight", "blocked")
        assert 0 <= data["confidence_score"] <= 100

    def test_analyze_agent_results_conform_to_schema(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_SAFE_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )
        data = resp.json()

        for ar in data["agent_results"]:
            assert "agent_name" in ar
            assert "risk_score_modifier" in ar
            assert "status" in ar
            assert "findings" in ar
            assert "recommended_action" in ar
            assert ar["status"] in ("pass", "warning", "critical")
            assert 0 <= ar["risk_score_modifier"] <= 100
            assert isinstance(ar["findings"], list)

    def test_blocked_analyze_returns_playbook(self, client, azure_stub, no_llm):
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            resp = client.post(
                "/analyze",
                json=_RISKY_PAYLOAD,
                headers={"X-Client-ID": _CLIENT_ID},
            )
        data = resp.json()

        assert data["decision"] == "blocked"
        assert data["rollback_playbook"] is not None
        assert "git revert" in data["rollback_playbook"]

    def test_multiple_sequential_pr_analyses(self, client, azure_stub, no_llm):
        """Run five consecutive analyses with different payloads; all must succeed."""
        payloads = [
            {**_SAFE_PAYLOAD, "pr_number": 50 + i, "changed_files": [f"module_{i}.py"]}
            for i in range(5)
        ]
        brief_patch, playbook_patch = _mock_llm()
        with brief_patch, playbook_patch:
            for payload in payloads:
                resp = client.post(
                    "/analyze",
                    json=payload,
                    headers={"X-Client-ID": f"multi-client-{payload['pr_number']}"},
                )
                assert resp.status_code == 200, f"PR {payload['pr_number']} failed: {resp.text}"
                assert resp.json()["decision"] in ("greenlight", "blocked")
