"""
Tests for the PRism Webhook Server (FastAPI).

Covers:
  - Webhook event parsing
  - HMAC-SHA256 signature verification
  - /health, /analyze, /webhook/pr API endpoints
  - _build_pr_comment() formatting
  - _post_pr_comment() with mocked httpx
  - _fetch_pr_details() with mocked httpx
  - _fetch_commit_timestamp() with mocked httpx
  - _run_orchestration() background task
  - _evict_expired_usage() TTL eviction
  - Freemium rate-limiting (check_freemium_limit)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator.server import (
    USAGE_TRACKER,
    _build_pr_comment,
    _evict_expired_usage,
    _parse_github_webhook,
    _verify_signature,
    app,
)
from agents.shared.data_contract import AgentResult, VerdictReport


# ── Helpers ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_usage_tracker():
    """Reset freemium counter so unit tests are isolated."""
    USAGE_TRACKER.clear()
    yield
    USAGE_TRACKER.clear()


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.run(coro)


def _make_agent_result(name: str, modifier: int = 10, status: str = "pass") -> AgentResult:
    return AgentResult(
        agent_name=name,
        risk_score_modifier=modifier,
        status=status,
        findings=[f"{name} looks fine"],
        recommended_action="Proceed.",
    )


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

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_endpoint(self, mock_orchestrate):
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

    @patch("agents.orchestrator.server._run_orchestration", new_callable=AsyncMock)
    def test_webhook_accepted_for_opened_pr(self, mock_run):
        """A valid opened PR webhook returns 202 and queues the background task."""
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "opened",
                "pull_request": {"number": 5, "head": {"sha": "abc123"}},
                "repository": {"full_name": "org/repo"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        assert resp.json()["pr_number"] == 5

    def test_webhook_rejects_invalid_signature(self):
        with patch("agents.orchestrator.server._WEBHOOK_SECRET", "secret"):
            resp = client.post(
                "/webhook/pr",
                content=b'{"action": "opened"}',
                headers={
                    "x-github-event": "pull_request",
                    "x-hub-signature-256": "sha256=badsig",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 401

    def test_analyze_missing_client_id_returns_400(self):
        """Requests without X-Client-ID header are rejected."""
        resp = client.post(
            "/analyze",
            json={"pr_number": 1, "repo": "org/repo"},
        )
        assert resp.status_code == 400


# ── Freemium rate-limiting tests ─────────────────────────────────────


class TestFreemiumLimit:
    from agents.orchestrator.server import FREE_TIER_LIMIT

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_first_request_succeeds(self, mock_orchestrate):
        mock_orchestrate.return_value = MOCK_VERDICT
        resp = client.post(
            "/analyze",
            json={"pr_number": 1, "repo": "org/repo"},
            headers={"X-Client-ID": "new-client"},
        )
        assert resp.status_code == 200

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_exhausted_limit_returns_402(self, mock_orchestrate):
        from agents.orchestrator.server import FREE_TIER_LIMIT
        mock_orchestrate.return_value = MOCK_VERDICT
        client_id = "exhausted-client"
        USAGE_TRACKER[client_id] = {"count": FREE_TIER_LIMIT, "first_seen": time.time()}
        resp = client.post(
            "/analyze",
            json={"pr_number": 1, "repo": "org/repo"},
            headers={"X-Client-ID": client_id},
        )
        assert resp.status_code == 402

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_counter_increments_on_each_request(self, mock_orchestrate):
        mock_orchestrate.return_value = MOCK_VERDICT
        client_id = "counting-client"
        for i in range(3):
            client.post(
                "/analyze",
                json={"pr_number": i + 1, "repo": "org/repo"},
                headers={"X-Client-ID": client_id},
            )
        assert USAGE_TRACKER[client_id]["count"] == 3


# ── _evict_expired_usage tests ────────────────────────────────────────


class TestEvictExpiredUsage:
    def test_evicts_old_entries(self):
        from agents.orchestrator.server import _USAGE_TTL_SECONDS
        USAGE_TRACKER["old-client"] = {
            "count": 5,
            "first_seen": time.time() - _USAGE_TTL_SECONDS - 1,
        }
        USAGE_TRACKER["new-client"] = {"count": 1, "first_seen": time.time()}
        _evict_expired_usage()
        assert "old-client" not in USAGE_TRACKER
        assert "new-client" in USAGE_TRACKER

    def test_does_not_evict_recent_entries(self):
        USAGE_TRACKER["active-client"] = {"count": 3, "first_seen": time.time()}
        _evict_expired_usage()
        assert "active-client" in USAGE_TRACKER

    def test_handles_empty_tracker(self):
        USAGE_TRACKER.clear()
        _evict_expired_usage()  # should not raise
        assert USAGE_TRACKER == {}


# ── _build_pr_comment tests ───────────────────────────────────────────


class TestBuildPrComment:
    def _make_verdict(self, decision: str, score: int, findings: list[str]) -> VerdictReport:
        results = [
            AgentResult(
                agent_name="Diff Analyst",
                risk_score_modifier=10,
                status="pass",
                findings=findings,
                recommended_action="Proceed.",
            )
        ]
        return VerdictReport(
            confidence_score=score,
            decision=decision,
            risk_brief="Brief summary.",
            rollback_playbook=None,
            agent_results=results,
        )

    def test_greenlight_verdict_uses_correct_tag(self):
        verdict = self._make_verdict("greenlight", 80, ["No issues found."])
        comment = _build_pr_comment(verdict)
        assert "✅ GREENLIGHT" in comment
        assert "BLOCKED" not in comment

    def test_blocked_verdict_uses_correct_tag(self):
        verdict = self._make_verdict("blocked", 40, ["Critical risk detected."])
        comment = _build_pr_comment(verdict)
        assert "🚫 BLOCKED" in comment

    def test_comment_includes_confidence_score(self):
        verdict = self._make_verdict("greenlight", 78, ["OK"])
        comment = _build_pr_comment(verdict)
        assert "78 / 100" in comment

    def test_comment_includes_agent_finding(self):
        verdict = self._make_verdict("greenlight", 90, ["Retry logic intact."])
        comment = _build_pr_comment(verdict)
        assert "Retry logic intact." in comment

    def test_long_finding_is_truncated(self):
        long_finding = "A" * 120
        verdict = self._make_verdict("greenlight", 85, [long_finding])
        comment = _build_pr_comment(verdict)
        # Finding should be truncated to ≤80 chars (77 + "...")
        assert "..." in comment
        # Full 120-char string should not appear
        assert long_finding not in comment

    def test_risk_brief_in_details_block(self):
        verdict = self._make_verdict("greenlight", 75, ["fine"])
        comment = _build_pr_comment(verdict)
        assert "Full Risk Brief" in comment
        assert "Brief summary." in comment

    def test_rollback_playbook_included_when_present(self):
        results = [
            AgentResult(
                agent_name="Timing Agent",
                risk_score_modifier=60,
                status="warning",
                findings=["Deploy on Friday"],
                recommended_action="Delay.",
            )
        ]
        verdict = VerdictReport(
            confidence_score=45,
            decision="blocked",
            risk_brief="Risky.",
            rollback_playbook="Step 1: revert\nStep 2: notify",
            agent_results=results,
        )
        comment = _build_pr_comment(verdict)
        assert "Rollback Playbook" in comment
        assert "Step 1: revert" in comment

    def test_prism_footer_present(self):
        verdict = self._make_verdict("greenlight", 80, ["OK"])
        comment = _build_pr_comment(verdict)
        assert "PRism" in comment
        assert "https://github.com/soni-ishan/PRism" in comment


# ── _post_pr_comment tests ────────────────────────────────────────────


class TestPostPrComment:
    def test_skips_post_when_no_token(self):
        from agents.orchestrator.server import _post_pr_comment
        with patch("agents.orchestrator.server._GITHUB_TOKEN", None):
            # Should return without raising even without a real HTTP call
            _run(_post_pr_comment("org/repo", 1, "test"))

    def test_posts_comment_successfully(self):
        from agents.orchestrator.server import _post_pr_comment
        mock_response = MagicMock()
        mock_response.is_error = False
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("agents.orchestrator.server._GITHUB_TOKEN", "ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            _run(_post_pr_comment("org/repo", 42, "Hello PR!"))

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "org/repo" in call_args.args[0]
        assert "42" in call_args.args[0]
        assert call_args.kwargs["json"]["body"] == "Hello PR!"

    def test_logs_warning_on_http_error(self):
        from agents.orchestrator.server import _post_pr_comment
        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("agents.orchestrator.server._GITHUB_TOKEN", "ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            # Should not raise even on HTTP error
            _run(_post_pr_comment("org/repo", 1, "msg"))


# ── _fetch_pr_details tests ───────────────────────────────────────────


class TestFetchPrDetails:
    def test_returns_files_and_diff(self):
        from agents.orchestrator.server import _fetch_pr_details
        files_resp = MagicMock()
        files_resp.raise_for_status = MagicMock()
        files_resp.json.return_value = [
            {"filename": "src/app.py"},
            {"filename": "tests/test_app.py"},
        ]
        files_resp.links = {}

        diff_resp = MagicMock()
        diff_resp.raise_for_status = MagicMock()
        diff_resp.text = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[files_resp, diff_resp])

        with patch("httpx.AsyncClient", return_value=mock_client):
            changed_files, diff = _run(_fetch_pr_details("org/repo", 1))

        assert changed_files == ["src/app.py", "tests/test_app.py"]
        assert "old" in diff

    def test_handles_http_error_gracefully(self):
        from agents.orchestrator.server import _fetch_pr_details
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            changed_files, diff = _run(_fetch_pr_details("org/repo", 99))

        assert changed_files == []
        assert diff == ""


# ── _fetch_commit_timestamp tests ─────────────────────────────────────


class TestFetchCommitTimestamp:
    def test_parses_date_from_patch_response(self):
        from agents.orchestrator.server import _fetch_commit_timestamp
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.text = (
            "From abc123\n"
            "From: Author <author@example.com>\n"
            "Date: Tue, 11 Mar 2026 01:37:25 -0500\n"
            "Subject: [PATCH] fix something\n"
        )
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(_fetch_commit_timestamp("org/repo", "abc123"))

        assert result is not None
        assert result.tzinfo is not None  # timezone-aware datetime
        assert result.year == 2026
        assert result.month == 3

    def test_returns_none_on_http_failure(self):
        from agents.orchestrator.server import _fetch_commit_timestamp
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(_fetch_commit_timestamp("org/repo", "deadbeef"))

        assert result is None

    def test_returns_none_when_no_date_header(self):
        from agents.orchestrator.server import _fetch_commit_timestamp
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.text = "From abc\nSubject: no date here\n"
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run(_fetch_commit_timestamp("org/repo", "abc"))

        assert result is None


# ── _run_orchestration tests ──────────────────────────────────────────


class TestRunOrchestration:
    """Tests for the background orchestration task — all external I/O is mocked."""

    def _make_payload(self, **kwargs):
        from agents.orchestrator import PRPayload
        defaults = dict(
            pr_number=42,
            repo="org/repo",
            changed_files=[],
            diff="",
            timestamp=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
        )
        defaults.update(kwargs)
        return PRPayload(**defaults)

    @patch("agents.orchestrator.server._post_pr_comment", new_callable=AsyncMock)
    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    @patch("agents.orchestrator.server._fetch_pr_details", new_callable=AsyncMock)
    def test_happy_path_posts_comment(self, mock_fetch, mock_orchestrate, mock_post):
        from agents.orchestrator.server import _run_orchestration
        mock_fetch.return_value = (["src/main.py"], "--- a\n+++ b\n")
        mock_orchestrate.return_value = MOCK_VERDICT

        payload = self._make_payload()
        _run(_run_orchestration(payload))

        mock_orchestrate.assert_called_once()
        mock_post.assert_called_once()
        posted_body = mock_post.call_args.args[2]
        assert "GREENLIGHT" in posted_body

    @patch("agents.orchestrator.server._post_pr_comment", new_callable=AsyncMock)
    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    @patch("agents.orchestrator.server._fetch_pr_details", new_callable=AsyncMock)
    @patch("agents.orchestrator.server._fetch_commit_timestamp", new_callable=AsyncMock)
    def test_updates_timestamp_from_head_sha(
        self, mock_ts, mock_fetch, mock_orchestrate, mock_post
    ):
        from agents.orchestrator.server import _run_orchestration
        tz_aware = datetime(2026, 3, 11, 1, 37, 25, tzinfo=timezone.utc)
        mock_ts.return_value = tz_aware
        mock_fetch.return_value = ([], "")
        mock_orchestrate.return_value = MOCK_VERDICT

        payload = self._make_payload(head_sha="abc123")
        _run(_run_orchestration(payload))

        assert payload.timestamp == tz_aware

    @patch("agents.orchestrator.server._post_pr_comment", new_callable=AsyncMock)
    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    @patch("agents.orchestrator.server._fetch_pr_details", new_callable=AsyncMock)
    def test_exception_in_orchestrate_does_not_propagate(
        self, mock_fetch, mock_orchestrate, mock_post
    ):
        from agents.orchestrator.server import _run_orchestration
        mock_fetch.return_value = ([], "")
        mock_orchestrate.side_effect = RuntimeError("orchestration exploded")

        payload = self._make_payload()
        # Should swallow the exception and log it, not raise
        _run(_run_orchestration(payload))
        mock_post.assert_not_called()
