"""
Tests for the PRism Webhook Server (FastAPI).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import agents.orchestrator.server as server_module
from agents.orchestrator.server import (
    USAGE_TRACKER,
    _build_pr_comment,
    _evict_expired_usage,
    _parse_github_webhook,
    _verify_signature,
    app,
    check_freemium_limit,
)
from agents.shared.data_contract import AgentResult, VerdictReport


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

MOCK_VERDICT_BLOCKED = VerdictReport(
    confidence_score=40,
    decision="blocked",
    risk_brief="High risk detected.",
    rollback_playbook="Step 1: Revert deployment.\nStep 2: Notify on-call.",
    agent_results=[
        AgentResult(
            agent_name="Coverage Agent",
            risk_score_modifier=55,
            status="critical",
            findings=["No test file found for agents/orchestrator/server.py"],
            recommended_action="Add tests before deploying.",
        )
    ],
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

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_webhook_accepted_for_opened_pr(self, mock_orchestrate):
        """Webhook for an opened PR is accepted and background task is queued."""
        mock_orchestrate.return_value = MOCK_VERDICT
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
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["pr_number"] == 5

    def test_webhook_rejects_malformed_payload(self):
        """Webhook with missing repo or pr_number returns 400."""
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

    @patch("agents.orchestrator.server._WEBHOOK_SECRET", "s3cr3t")
    def test_webhook_rejects_invalid_signature(self):
        """Webhook with a bad signature is rejected with 401."""
        resp = client.post(
            "/webhook/pr",
            content=b'{"action":"opened"}',
            headers={
                "x-github-event": "pull_request",
                "x-hub-signature-256": "sha256=badhash",
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 401

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_missing_client_id_returns_400(self, mock_orchestrate):
        """POST /analyze without X-Client-ID header returns 400."""
        mock_orchestrate.return_value = MOCK_VERDICT
        resp = client.post(
            "/analyze",
            json={
                "pr_number": 1,
                "repo": "org/repo",
                "changed_files": [],
                "diff": "",
            },
        )
        assert resp.status_code == 400

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_freemium_limit_enforced(self, mock_orchestrate):
        """POST /analyze returns 402 once the free-tier limit is reached."""
        from agents.orchestrator.server import FREE_TIER_LIMIT

        mock_orchestrate.return_value = MOCK_VERDICT
        USAGE_TRACKER["test-client"] = {
            "count": FREE_TIER_LIMIT,
            "first_seen": time.time(),
        }
        resp = client.post(
            "/analyze",
            json={
                "pr_number": 1,
                "repo": "org/repo",
                "changed_files": [],
                "diff": "",
            },
            headers={"X-Client-ID": "test-client"},
        )
        assert resp.status_code == 402

    @patch("agents.orchestrator.server.orchestrate", new_callable=AsyncMock)
    def test_analyze_increments_usage_counter(self, mock_orchestrate):
        """Each successful /analyze call increments the client usage counter."""
        mock_orchestrate.return_value = MOCK_VERDICT
        for _ in range(3):
            client.post(
                "/analyze",
                json={"pr_number": 1, "repo": "org/repo", "changed_files": [], "diff": ""},
                headers={"X-Client-ID": "counting-client"},
            )
        assert USAGE_TRACKER["counting-client"]["count"] == 3


# ── _build_pr_comment tests ──────────────────────────────────────────


class TestBuildPRComment:
    def test_greenlight_verdict_renders_tag(self):
        comment = _build_pr_comment(MOCK_VERDICT)
        assert "✅ GREENLIGHT" in comment
        assert "75 / 100" in comment

    def test_blocked_verdict_renders_tag(self):
        comment = _build_pr_comment(MOCK_VERDICT_BLOCKED)
        assert "🚫 BLOCKED" in comment
        assert "40 / 100" in comment

    def test_agent_results_appear_in_table(self):
        comment = _build_pr_comment(MOCK_VERDICT_BLOCKED)
        assert "Coverage Agent" in comment
        assert "No test file found" in comment

    def test_risk_brief_in_details_block(self):
        comment = _build_pr_comment(MOCK_VERDICT_BLOCKED)
        assert "High risk detected." in comment
        assert "<details>" in comment

    def test_rollback_playbook_in_details_block(self):
        comment = _build_pr_comment(MOCK_VERDICT_BLOCKED)
        assert "Rollback Playbook" in comment
        assert "Revert deployment" in comment

    def test_no_rollback_playbook_when_none(self):
        comment = _build_pr_comment(MOCK_VERDICT)
        assert "Rollback Playbook" not in comment

    def test_long_finding_is_truncated(self):
        long_finding = "A" * 200
        verdict = VerdictReport(
            confidence_score=80,
            decision="greenlight",
            risk_brief="Minor risk.",
            rollback_playbook=None,
            agent_results=[
                AgentResult(
                    agent_name="Diff Analyst",
                    risk_score_modifier=10,
                    status="warning",
                    findings=[long_finding],
                    recommended_action="Review the diff.",
                )
            ],
        )
        comment = _build_pr_comment(verdict)
        # The full 200-char finding must not appear verbatim
        assert long_finding not in comment
        assert "..." in comment

    def test_prism_footer_present(self):
        comment = _build_pr_comment(MOCK_VERDICT)
        assert "Generated by" in comment
        assert "PRism" in comment


# ── _evict_expired_usage tests ───────────────────────────────────────


class TestEvictExpiredUsage:
    def test_evicts_old_entries(self):
        from agents.orchestrator.server import _USAGE_TTL_SECONDS

        USAGE_TRACKER["stale-client"] = {
            "count": 5,
            "first_seen": time.time() - _USAGE_TTL_SECONDS - 1,
        }
        USAGE_TRACKER["fresh-client"] = {
            "count": 2,
            "first_seen": time.time(),
        }
        _evict_expired_usage()
        assert "stale-client" not in USAGE_TRACKER
        assert "fresh-client" in USAGE_TRACKER

    def test_no_error_on_empty_tracker(self):
        _evict_expired_usage()  # Should not raise


# ── _fetch_pr_details tests ──────────────────────────────────────────


class TestFetchPRDetails:
    @pytest.mark.asyncio
    async def test_returns_files_and_diff(self):
        from agents.orchestrator.server import _fetch_pr_details

        mock_resp_files = MagicMock()
        mock_resp_files.raise_for_status = MagicMock()
        mock_resp_files.json.return_value = [
            {"filename": "app/main.py"},
            {"filename": "app/utils.py"},
        ]
        mock_resp_files.links = {}

        mock_resp_diff = MagicMock()
        mock_resp_diff.raise_for_status = MagicMock()
        mock_resp_diff.text = "--- a/app/main.py\n+++ b/app/main.py\n@@ -1 +1 @@\n-old\n+new"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_files, mock_resp_diff])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client):
            files, diff = await _fetch_pr_details("org/repo", 42)

        assert "app/main.py" in files
        assert "app/utils.py" in files
        assert "old" in diff

    @pytest.mark.asyncio
    async def test_handles_api_error_gracefully(self):
        from agents.orchestrator.server import _fetch_pr_details

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client):
            files, diff = await _fetch_pr_details("org/repo", 99)

        assert files == []
        assert diff == ""


# ── _fetch_commit_timestamp tests ────────────────────────────────────


class TestFetchCommitTimestamp:
    @pytest.mark.asyncio
    async def test_parses_date_header_from_patch(self):
        from agents.orchestrator.server import _fetch_commit_timestamp

        patch_body = (
            "From abc123 Mon Sep 17 00:00:00 2001\n"
            "From: Dev <dev@example.com>\n"
            "Date: Tue, 11 Mar 2026 01:37:25 -0500\n"
            "Subject: [PATCH] Add feature\n"
        )
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.text = patch_body

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client):
            ts = await _fetch_commit_timestamp("org/repo", "abc123")

        assert ts is not None
        assert ts.hour == 1
        assert ts.minute == 37

    @pytest.mark.asyncio
    async def test_returns_none_on_non_success_response(self):
        from agents.orchestrator.server import _fetch_commit_timestamp

        mock_resp = MagicMock()
        mock_resp.is_success = False

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client):
            ts = await _fetch_commit_timestamp("org/repo", "deadbeef")

        assert ts is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        from agents.orchestrator.server import _fetch_commit_timestamp

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client):
            ts = await _fetch_commit_timestamp("org/repo", "sha123")

        assert ts is None


# ── _post_pr_comment tests ───────────────────────────────────────────


class TestPostPRComment:
    @pytest.mark.asyncio
    async def test_posts_comment_when_token_set(self):
        from agents.orchestrator.server import _post_pr_comment

        mock_resp = MagicMock()
        mock_resp.is_error = False

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("agents.orchestrator.server._GITHUB_TOKEN", "gh_tok"),
            patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client),
        ):
            await _post_pr_comment("org/repo", 5, "## PRism\nAll good.")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["body"] == "## PRism\nAll good."

    @pytest.mark.asyncio
    async def test_skips_post_when_no_token(self):
        from agents.orchestrator.server import _post_pr_comment

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("agents.orchestrator.server._GITHUB_TOKEN", None),
            patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client),
        ):
            await _post_pr_comment("org/repo", 5, "## PRism\nBlocked.")

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_post_exception_gracefully(self):
        from agents.orchestrator.server import _post_pr_comment

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("agents.orchestrator.server._GITHUB_TOKEN", "gh_tok"),
            patch("agents.orchestrator.server.httpx.AsyncClient", return_value=mock_client),
        ):
            # Should not raise
            await _post_pr_comment("org/repo", 5, "## PRism")


# ── _run_orchestration tests ─────────────────────────────────────────


class TestRunOrchestration:
    """Tests for the background task that drives the full PRism pipeline."""

    @pytest.mark.asyncio
    async def test_run_orchestration_happy_path(self):
        """Full pipeline: fetch details → orchestrate → post comment."""
        from agents.orchestrator import PRPayload
        from agents.orchestrator.server import _run_orchestration

        payload = PRPayload(
            pr_number=42,
            repo="org/repo",
            changed_files=[],
            diff="",
            timestamp=datetime.now(timezone.utc),
        )

        with (
            patch(
                "agents.orchestrator.server._fetch_pr_details",
                new_callable=AsyncMock,
                return_value=(["app/main.py"], "--- old\n+++ new"),
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

        mock_post.assert_called_once_with("org/repo", 42, mock_post.call_args.args[2])
        comment_body = mock_post.call_args.args[2]
        assert "✅ GREENLIGHT" in comment_body

    @pytest.mark.asyncio
    async def test_run_orchestration_updates_timestamp_from_sha(self):
        """Commit timestamp from head_sha is applied to payload before orchestrate."""
        from agents.orchestrator import PRPayload
        from agents.orchestrator.server import _run_orchestration

        fixed_ts = datetime(2026, 3, 11, 1, 37, 25, tzinfo=timezone.utc)
        payload = PRPayload(
            pr_number=7,
            repo="org/repo",
            changed_files=[],
            diff="",
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
                return_value=fixed_ts,
            ),
            patch(
                "agents.orchestrator.server.orchestrate",
                new_callable=AsyncMock,
                return_value=MOCK_VERDICT,
            ) as mock_orch,
            patch("agents.orchestrator.server._post_pr_comment", new_callable=AsyncMock),
        ):
            await _run_orchestration(payload)

        called_payload = mock_orch.call_args.args[0]
        assert called_payload.timestamp == fixed_ts

    @pytest.mark.asyncio
    async def test_run_orchestration_handles_exception_without_crashing(self):
        """An exception inside _run_orchestration is caught and logged."""
        from agents.orchestrator import PRPayload
        from agents.orchestrator.server import _run_orchestration

        payload = PRPayload(
            pr_number=1,
            repo="org/repo",
            changed_files=[],
            diff="",
        )

        with patch(
            "agents.orchestrator.server._fetch_pr_details",
            new_callable=AsyncMock,
            side_effect=RuntimeError("upstream failure"),
        ):
            # Must not propagate the exception
            await _run_orchestration(payload)

    @pytest.mark.asyncio
    async def test_run_orchestration_blocked_verdict_posts_comment(self):
        """Blocked verdict is still posted as a PR comment."""
        from agents.orchestrator import PRPayload
        from agents.orchestrator.server import _run_orchestration

        payload = PRPayload(pr_number=3, repo="org/repo", changed_files=[], diff="")

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
                return_value=MOCK_VERDICT_BLOCKED,
            ),
            patch(
                "agents.orchestrator.server._post_pr_comment",
                new_callable=AsyncMock,
            ) as mock_post,
        ):
            await _run_orchestration(payload)

        mock_post.assert_called_once()
        comment_body = mock_post.call_args.args[2]
        assert "🚫 BLOCKED" in comment_body
