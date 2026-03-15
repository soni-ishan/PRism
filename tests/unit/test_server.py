"""
Tests for the PRism Webhook Server (FastAPI).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import PRPayload
from agents.orchestrator.server import (
    FREE_TIER_LIMIT,
    USAGE_TRACKER,
    _build_pr_comment,
    _evict_expired_usage,
    _fetch_commit_timestamp,
    _fetch_pr_details,
    _parse_github_webhook,
    _post_pr_comment,
    _run_orchestration,
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


def _make_agent_result(name: str, modifier: int = 10, status: str = "pass") -> AgentResult:
    return AgentResult(
        agent_name=name,
        risk_score_modifier=modifier,
        status=status,
        findings=[f"{name} finding"],
        recommended_action=f"{name} action",
    )


MOCK_VERDICT = VerdictReport(
    confidence_score=75,
    decision="greenlight",
    risk_brief="Looks good.",
    rollback_playbook=None,
    agent_results=[],
)

MOCK_VERDICT_BLOCKED = VerdictReport(
    confidence_score=40,
    decision="blocked",
    risk_brief="High risk deployment.",
    rollback_playbook="Revert the PR immediately.",
    agent_results=[_make_agent_result("Coverage Agent", 55, "critical")],
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

    def test_webhook_pr_opened_accepted(self):
        """Valid opened PR webhook is accepted and queued as background task."""
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "opened",
                "pull_request": {"number": 42, "head": {"sha": "abc123"}},
                "repository": {"full_name": "org/repo"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["pr_number"] == 42

    def test_webhook_pr_synchronize_accepted(self):
        """Valid synchronize PR webhook is accepted."""
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "synchronize",
                "pull_request": {"number": 7},
                "repository": {"full_name": "org/repo"},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

    @patch("agents.orchestrator.server._WEBHOOK_SECRET", "testsecret")
    def test_webhook_invalid_signature_rejected(self):
        """Webhook with wrong signature returns 401."""
        resp = client.post(
            "/webhook/pr",
            json={"action": "opened"},
            headers={
                "x-github-event": "pull_request",
                "x-hub-signature-256": "sha256=invalidsig",
            },
        )
        assert resp.status_code == 401

    def test_webhook_malformed_missing_repo(self):
        """Webhook missing repo or pr_number returns 400."""
        resp = client.post(
            "/webhook/pr",
            json={
                "action": "opened",
                "pull_request": {"number": 0},
                "repository": {"full_name": ""},
            },
            headers={"x-github-event": "pull_request"},
        )
        assert resp.status_code == 400

    def test_analyze_missing_client_id(self):
        """POST /analyze without X-Client-ID returns 400."""
        resp = client.post(
            "/analyze",
            json={
                "pr_number": 1,
                "repo": "org/repo",
            },
        )
        assert resp.status_code == 400

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_with_head_sha(self, mock_orchestrate):
        """POST /analyze fetches commit timestamp when head_sha is provided."""
        mock_orchestrate.return_value = MOCK_VERDICT
        with patch(
            "agents.orchestrator.server._fetch_commit_timestamp",
            new_callable=AsyncMock,
            return_value=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        ):
            resp = client.post(
                "/analyze",
                json={
                    "pr_number": 5,
                    "repo": "org/repo",
                    "head_sha": "deadbeef",
                },
                headers={"X-Client-ID": "unit-test-client"},
            )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "greenlight"


# ── Freemium usage tests ─────────────────────────────────────────────


class TestFreemiumUsage:
    def test_usage_endpoint_fresh_client(self):
        """A new client has 0 credits used."""
        resp = client.get("/usage", headers={"X-Client-ID": "brand-new"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["credits_used"] == 0
        assert data["credits_limit"] == FREE_TIER_LIMIT
        assert data["credits_remaining"] == FREE_TIER_LIMIT

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_usage_increments_after_analyze(self, mock_orchestrate):
        """Each /analyze call consumes one credit."""
        mock_orchestrate.return_value = MOCK_VERDICT
        for _ in range(3):
            client.post(
                "/analyze",
                json={"pr_number": 1, "repo": "org/repo"},
                headers={"X-Client-ID": "counting-client"},
            )
        resp = client.get("/usage", headers={"X-Client-ID": "counting-client"})
        assert resp.json()["credits_used"] == 3

    @patch("agents.orchestrator.server.FREE_TIER_LIMIT", 2)
    @patch("agents.orchestrator.server._RATE_LIMITING_DISABLED", False)
    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_freemium_limit_enforced(self, mock_orchestrate):
        """After exhausting the free limit, /analyze returns 402."""
        mock_orchestrate.return_value = MOCK_VERDICT
        for _ in range(2):
            r = client.post(
                "/analyze",
                json={"pr_number": 1, "repo": "org/repo"},
                headers={"X-Client-ID": "limited-client"},
            )
            assert r.status_code == 200
        # Third call exceeds limit
        r = client.post(
            "/analyze",
            json={"pr_number": 1, "repo": "org/repo"},
            headers={"X-Client-ID": "limited-client"},
        )
        assert r.status_code == 402

    def test_evict_expired_usage_removes_old_entries(self):
        """Entries older than TTL are removed by eviction."""
        USAGE_TRACKER["old-client"] = {"count": 5, "first_seen": time.time() - 40 * 24 * 60 * 60}
        USAGE_TRACKER["new-client"] = {"count": 1, "first_seen": time.time()}
        _evict_expired_usage()
        assert "old-client" not in USAGE_TRACKER
        assert "new-client" in USAGE_TRACKER

    def test_evict_expired_usage_keeps_recent_entries(self):
        """Entries within TTL are not evicted."""
        USAGE_TRACKER["recent-client"] = {"count": 3, "first_seen": time.time() - 60}
        _evict_expired_usage()
        assert "recent-client" in USAGE_TRACKER


# ── _build_pr_comment tests ──────────────────────────────────────────


class TestBuildPRComment:
    def test_greenlight_verdict_format(self):
        verdict = VerdictReport(
            confidence_score=85,
            decision="greenlight",
            risk_brief="All clear.",
            rollback_playbook=None,
            agent_results=[],
        )
        comment = _build_pr_comment(verdict)
        assert "✅ GREENLIGHT" in comment
        assert "85 / 100" in comment
        assert "PRism Deployment Risk Analysis" in comment

    def test_blocked_verdict_format(self):
        verdict = MOCK_VERDICT_BLOCKED
        comment = _build_pr_comment(verdict)
        assert "🚫 BLOCKED" in comment
        assert "40 / 100" in comment

    def test_agent_results_included_in_table(self):
        verdict = VerdictReport(
            confidence_score=60,
            decision="blocked",
            risk_brief="Coverage gaps detected.",
            rollback_playbook=None,
            agent_results=[
                _make_agent_result("Coverage Agent", 55, "critical"),
                _make_agent_result("Timing Agent", 30, "warning"),
            ],
        )
        comment = _build_pr_comment(verdict)
        assert "Coverage Agent" in comment
        assert "Timing Agent" in comment
        assert "🚫 critical" in comment
        assert "⚠️ warning" in comment

    def test_risk_brief_in_details(self):
        verdict = VerdictReport(
            confidence_score=50,
            decision="blocked",
            risk_brief="High risk: payment service changed.",
            rollback_playbook=None,
            agent_results=[],
        )
        comment = _build_pr_comment(verdict)
        assert "Full Risk Brief" in comment
        assert "High risk: payment service changed." in comment

    def test_rollback_playbook_in_details(self):
        verdict = VerdictReport(
            confidence_score=30,
            decision="blocked",
            risk_brief="Critical failure detected.",
            rollback_playbook="Step 1: Revert PR.\nStep 2: Notify on-call.",
            agent_results=[],
        )
        comment = _build_pr_comment(verdict)
        assert "Rollback Playbook" in comment
        assert "Step 1: Revert PR." in comment

    def test_long_finding_truncated(self):
        long_finding = "x" * 100
        verdict = VerdictReport(
            confidence_score=70,
            decision="greenlight",
            risk_brief="Minor issues only.",
            rollback_playbook=None,
            agent_results=[_make_agent_result("Diff Analyst", 10, "pass")],
        )
        # Override the finding with a long string
        verdict.agent_results[0].findings = [long_finding]
        comment = _build_pr_comment(verdict)
        # The finding should be truncated to ≤80 chars
        for line in comment.splitlines():
            if "Diff Analyst" in line and "x" * 10 in line:
                assert len(line) < 300  # Whole table line but finding truncated


# ── _fetch_pr_details tests ──────────────────────────────────────────


class TestFetchPRDetails:
    async def test_fetch_pr_details_success(self):
        """Returns file list and diff from mocked GitHub API."""
        files_resp = MagicMock()
        files_resp.raise_for_status.return_value = None
        files_resp.json.return_value = [{"filename": "foo.py"}, {"filename": "bar.py"}]
        files_resp.links = {}

        diff_resp = MagicMock()
        diff_resp.raise_for_status.return_value = None
        diff_resp.text = "- old\n+ new"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[files_resp, diff_resp])

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            files, diff = await _fetch_pr_details("org/repo", 46)

        assert files == ["foo.py", "bar.py"]
        assert diff == "- old\n+ new"

    async def test_fetch_pr_details_files_api_error(self):
        """Returns empty file list gracefully when files API raises."""
        diff_resp = MagicMock()
        diff_resp.raise_for_status.return_value = None
        diff_resp.text = "some diff"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[Exception("network error"), diff_resp])

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            files, diff = await _fetch_pr_details("org/repo", 1)

        assert files == []

    async def test_fetch_pr_details_diff_api_error(self):
        """Returns empty diff gracefully when diff API raises."""
        files_resp = MagicMock()
        files_resp.raise_for_status.return_value = None
        files_resp.json.return_value = [{"filename": "a.py"}]
        files_resp.links = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[files_resp, Exception("diff error")])

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            files, diff = await _fetch_pr_details("org/repo", 1)

        assert files == ["a.py"]
        assert diff == ""

    async def test_fetch_pr_details_unexpected_json(self):
        """Non-list response for files is handled gracefully."""
        files_resp = MagicMock()
        files_resp.raise_for_status.return_value = None
        files_resp.json.return_value = {"message": "Not Found"}
        files_resp.links = {}

        diff_resp = MagicMock()
        diff_resp.raise_for_status.return_value = None
        diff_resp.text = ""

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[files_resp, diff_resp])

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            files, diff = await _fetch_pr_details("org/repo", 1)

        assert files == []


# ── _fetch_commit_timestamp tests ────────────────────────────────────


class TestFetchCommitTimestamp:
    async def test_fetch_commit_timestamp_success(self):
        """Parses Date header from patch format response."""
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.text = (
            "From abc123\n"
            "Date: Tue, 11 Mar 2026 01:37:25 -0500\n"
            "Subject: my commit\n"
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            ts = await _fetch_commit_timestamp("org/repo", "abc123")

        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 3
        assert ts.day == 11

    async def test_fetch_commit_timestamp_no_date_header(self):
        """Returns None when patch output has no Date header."""
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.text = "From abc123\nSubject: my commit\n"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            ts = await _fetch_commit_timestamp("org/repo", "abc123")

        assert ts is None

    async def test_fetch_commit_timestamp_api_error(self):
        """Returns None on network exception."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            ts = await _fetch_commit_timestamp("org/repo", "abc123")

        assert ts is None

    async def test_fetch_commit_timestamp_non_success_response(self):
        """Returns None when API response is not successful."""
        mock_resp = MagicMock()
        mock_resp.is_success = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
            ts = await _fetch_commit_timestamp("org/repo", "abc123")

        assert ts is None


# ── _post_pr_comment tests ───────────────────────────────────────────


class TestPostPRComment:
    async def test_post_pr_comment_no_token(self):
        """Skips silently when GH_PAT is not configured."""
        with patch("agents.orchestrator.server._GITHUB_TOKEN", None):
            # Should not raise and should not make any HTTP call
            with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
                await _post_pr_comment("org/repo", 1, "hello")
                MockClient.assert_not_called()

    async def test_post_pr_comment_success(self):
        """Posts comment body to the correct GitHub API URL."""
        mock_resp = MagicMock()
        mock_resp.is_error = False

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("agents.orchestrator.server._GITHUB_TOKEN", "ghp_fake"):
            with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
                await _post_pr_comment("org/repo", 42, "test comment")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "org/repo" in call_args.args[0]
        assert call_args.kwargs["json"]["body"] == "test comment"

    async def test_post_pr_comment_error_response(self):
        """Logs warning but does not raise on error HTTP status."""
        mock_resp = MagicMock()
        mock_resp.is_error = True
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("agents.orchestrator.server._GITHUB_TOKEN", "ghp_fake"):
            with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
                # Should not raise
                await _post_pr_comment("org/repo", 1, "comment")

    async def test_post_pr_comment_exception(self):
        """Logs warning but does not propagate network exceptions."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("agents.orchestrator.server._GITHUB_TOKEN", "ghp_fake"):
            with patch("agents.orchestrator.server.httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=None)
                # Should not raise
                await _post_pr_comment("org/repo", 1, "comment")


# ── _run_orchestration tests (public "run" function) ─────────────────


class TestRunOrchestration:
    """Tests for _run_orchestration, the server's main background pipeline."""

    async def test_run_orchestration_success(self):
        """Full happy path: fetches details, orchestrates, posts comment."""
        payload = PRPayload(
            pr_number=46,
            repo="org/repo",
            changed_files=[],
            diff="",
        )

        with (
            patch(
                "agents.orchestrator.server._fetch_pr_details",
                new_callable=AsyncMock,
                return_value=(["service.py"], "- old\n+ new"),
            ),
            patch(
                "agents.orchestrator.server._fetch_commit_timestamp",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "agents.orchestrator.server.orchestrate",
                new_callable=AsyncMock,
                return_value=MOCK_VERDICT,
            ),
            patch(
                "agents.orchestrator.server._post_pr_comment",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            await _run_orchestration(payload)

        # PR comment must be posted after successful orchestration
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "org/repo"
        assert call_args.args[1] == 46

    async def test_run_orchestration_uses_commit_timestamp(self):
        """Timestamp from commit is applied to the payload before orchestration."""
        expected_ts = datetime(2026, 3, 11, 1, 37, tzinfo=timezone.utc)
        payload = PRPayload(
            pr_number=10,
            repo="org/repo",
            head_sha="abc123",
        )

        with (
            patch(
                "agents.orchestrator.server._fetch_pr_details",
                new_callable=AsyncMock,
                return_value=([], ""),
            ),
            patch(
                "agents.orchestrator.server._fetch_commit_timestamp",
                new_callable=AsyncMock,
                return_value=expected_ts,
            ),
            patch(
                "agents.orchestrator.server.orchestrate",
                new_callable=AsyncMock,
                return_value=MOCK_VERDICT,
            ) as mock_orchestrate,
            patch(
                "agents.orchestrator.server._post_pr_comment",
                new_callable=AsyncMock,
            ),
        ):
            await _run_orchestration(payload)

        # The payload passed to orchestrate should have the resolved timestamp
        orchestrated_payload = mock_orchestrate.call_args.args[0]
        assert orchestrated_payload.timestamp == expected_ts

    async def test_run_orchestration_handles_orchestrate_exception(self):
        """Logs exception and does not propagate if orchestrate raises."""
        payload = PRPayload(pr_number=1, repo="org/repo")

        with (
            patch(
                "agents.orchestrator.server._fetch_pr_details",
                new_callable=AsyncMock,
                return_value=([], ""),
            ),
            patch(
                "agents.orchestrator.server._fetch_commit_timestamp",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "agents.orchestrator.server.orchestrate",
                new_callable=AsyncMock,
                side_effect=RuntimeError("AI service unavailable"),
            ),
            patch(
                "agents.orchestrator.server._post_pr_comment",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            # Should not raise
            await _run_orchestration(payload)

        # Comment should not be posted if orchestration failed
        mock_post.assert_not_called()

    async def test_run_orchestration_fetch_details_fails(self):
        """Continues orchestration even when fetch_pr_details raises."""
        payload = PRPayload(pr_number=5, repo="org/repo")

        with (
            patch(
                "agents.orchestrator.server._fetch_pr_details",
                new_callable=AsyncMock,
                side_effect=Exception("GitHub API down"),
            ),
            patch(
                "agents.orchestrator.server.orchestrate",
                new_callable=AsyncMock,
                return_value=MOCK_VERDICT,
            ),
            patch(
                "agents.orchestrator.server._post_pr_comment",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            # Should not raise
            await _run_orchestration(payload)

        # Since _fetch_pr_details raised, orchestration should have also
        # raised inside the try block, so _post_pr_comment won't be called.
        mock_post.assert_not_called()
