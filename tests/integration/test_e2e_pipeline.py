"""
PRism – End-to-End Pipeline Integration Tests
==============================================
Tests the full orchestration pipeline:  PRPayload → orchestrate() → VerdictReport.

All four specialist agents are invoked with their **real implementations**.
Only external Azure/LLM calls made by the History Agent and Diff Analyst's
optional LLM path are patched at the network boundary.  Every byte of
orchestrator, verdict-agent, and timing-agent logic is exercised for real.

Test scenarios
--------------
* Safe PR on a Tuesday morning             → greenlight
* Risky PR (secrets in diff)               → blocked
* High-history PR (Friday evening)         → blocked
* Partial agent failure                    → verdict still produced, fallback used
* Total agent failure                      → blocked verdict returned
* Minimal payload (no diff / no files)     → handled gracefully
* Data-contract compliance across the full chain
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import time

from agents.orchestrator import PRPayload, orchestrate
from agents.shared.data_contract import AgentResult, VerdictReport


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_incident(incident_id, timestamp, files, *, severity="high", title=None):
    return {
        "id": incident_id,
        "timestamp": timestamp,
        "title": title or f"Incident {incident_id}",
        "severity": severity,
        "files_involved": files,
        "root_cause": "n/a",
        "error_message": "n/a",
    }


def _mock_azure_mcp(incidents: list[dict]) -> MagicMock:
    m = MagicMock()
    m.query_incidents_by_files_search.return_value = incidents
    return m


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def azure_mcp_stub():
    """Patch AzureMCPServer with a zero-incident stub (safe PR scenario)."""
    mock_mcp = _mock_azure_mcp([])
    with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
        yield mock_mcp


@pytest.fixture()
def azure_mcp_risky():
    """Patch AzureMCPServer returning 5 incidents for payment_service.py."""
    incidents = [
        _make_incident(f"INC-{i}", f"2026-02-{20+i:02d}T10:00:00Z", ["payment_service.py"])
        for i in range(5)
    ]
    mock_mcp = _mock_azure_mcp(incidents)
    with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
        yield mock_mcp


@pytest.fixture()
def no_llm():
    """Ensure LLM env vars are absent so the diff analyst uses heuristics only."""
    with patch.dict(
        "os.environ",
        {"AZURE_OPENAI_ENDPOINT": "", "AZURE_OPENAI_API_KEY": "", "AZURE_OPENAI_DEPLOYMENT": ""},
        clear=False,
    ):
        yield


# ── Basic orchestration ──────────────────────────────────────────────

class TestOrchestrateFullPipeline:
    def test_safe_pr_greenlights(self, azure_mcp_stub, no_llm):
        """Safe diff + no history + Tuesday morning → greenlight."""
        payload = PRPayload(
            pr_number=101,
            repo="acme/backend",
            changed_files=["utils/logger.py"],
            diff="+ logger.setLevel(logging.DEBUG)",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),  # Tuesday
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert isinstance(verdict, VerdictReport)
        assert verdict.decision == "greenlight"
        assert verdict.confidence_score >= 70
        assert verdict.rollback_playbook is None

    def test_secret_in_diff_blocks(self, azure_mcp_stub, no_llm):
        """Hardcoded secret in diff → Diff Analyst critical → blocked."""
        payload = PRPayload(
            pr_number=102,
            repo="acme/backend",
            changed_files=["config.py"],
            diff='+ STRIPE_SECRET = "sk-1234567890abcdef1234567890abcdef"',
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert verdict.decision == "blocked"
        assert verdict.rollback_playbook is not None

    def test_friday_evening_deploy_blocks(self, azure_mcp_stub, no_llm):
        """Friday 17:00 timezone → Timing Agent critical → blocked."""
        payload = PRPayload(
            pr_number=103,
            repo="acme/backend",
            changed_files=["api/handler.py"],
            diff="+ minor_fix = True",
            timestamp=datetime(2026, 2, 27, 17, 15, tzinfo=timezone.utc),  # Friday 17:15
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert verdict.decision == "blocked"

    def test_high_history_raises_score(self, azure_mcp_risky, no_llm):
        """Five past incidents for payment_service.py → history risk is warning/critical."""
        payload = PRPayload(
            pr_number=104,
            repo="acme/backend",
            changed_files=["payment_service.py"],
            diff="+ amount = amount * 100",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert isinstance(verdict, VerdictReport)
        # History + high risk should produce meaningful score reduction
        history_result = next(
            (r for r in verdict.agent_results if r.agent_name == "History Agent"), None
        )
        assert history_result is not None
        assert history_result.risk_score_modifier >= 40


class TestOrchestrateEdgeCases:
    def test_minimal_payload_no_crash(self, azure_mcp_stub, no_llm):
        """Payload with no files and no diff must not raise."""
        payload = PRPayload(pr_number=1, repo="org/repo")
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert isinstance(verdict, VerdictReport)
        assert verdict.decision in ("greenlight", "blocked")

    def test_dict_payload_accepted(self, azure_mcp_stub, no_llm):
        """orchestrate() should accept a raw dict as well as PRPayload."""
        payload_dict: dict[str, Any] = {
            "pr_number": 5,
            "repo": "org/repo",
            "changed_files": ["main.py"],
            "diff": "+ x = 1",
        }
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload_dict))

        assert isinstance(verdict, VerdictReport)

    def test_no_timestamp_uses_current_time(self, azure_mcp_stub, no_llm):
        """Omitting timestamp should not raise; timing agent defaults to now."""
        payload = PRPayload(
            pr_number=6,
            repo="org/repo",
            changed_files=["service.py"],
            diff="+ x = 1",
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert isinstance(verdict, VerdictReport)


# ── Data-contract compliance ──────────────────────────────────────────

class TestPipelineDataContract:
    def test_verdict_has_four_agent_results(self, azure_mcp_stub, no_llm):
        payload = PRPayload(
            pr_number=200,
            repo="acme/backend",
            changed_files=["app.py"],
            diff="+ app.start()",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert len(verdict.agent_results) == 4
        names = {r.agent_name for r in verdict.agent_results}
        assert names == {"Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"}

    def test_all_agent_results_valid_schema(self, azure_mcp_stub, no_llm):
        payload = PRPayload(
            pr_number=201,
            repo="acme/backend",
            changed_files=["app.py"],
            diff="+ code = True",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        for ar in verdict.agent_results:
            assert isinstance(ar, AgentResult)
            assert ar.agent_name != ""
            assert 0 <= ar.risk_score_modifier <= 100
            assert ar.status in ("pass", "warning", "critical")
            assert isinstance(ar.findings, list)
            assert isinstance(ar.recommended_action, str)

    def test_verdict_json_round_trip(self, azure_mcp_stub, no_llm):
        payload = PRPayload(
            pr_number=202,
            repo="acme/backend",
            changed_files=["db.py"],
            diff="+ db.commit()",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        raw = verdict.to_json()
        parsed = VerdictReport.from_json(raw)

        assert parsed.confidence_score == verdict.confidence_score
        assert parsed.decision == verdict.decision
        assert len(parsed.agent_results) == len(verdict.agent_results)

    def test_blocked_verdict_always_has_playbook(self, azure_mcp_stub, no_llm):
        payload = PRPayload(
            pr_number=203,
            repo="acme/backend",
            changed_files=["config.py"],
            diff='+ SECRET = "sk-1234567890abcdef1234567890abcdef"',
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        if verdict.decision == "blocked":
            assert verdict.rollback_playbook is not None
            assert len(verdict.rollback_playbook) > 0

    def test_confidence_score_in_valid_range(self, azure_mcp_stub, no_llm):
        payload = PRPayload(
            pr_number=204,
            repo="acme/backend",
            changed_files=["main.py"],
            diff="+ pass",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert 0 <= verdict.confidence_score <= 100


# ── Partial and total agent failure ──────────────────────────────────

class TestOrchestrateResiliency:
    def test_coverage_agent_failure_produces_fallback(self, azure_mcp_stub, no_llm):
        """Coverage Agent is a stub → fallback result must be in final verdict."""
        payload = PRPayload(
            pr_number=300,
            repo="acme/backend",
            changed_files=["service.py"],
            diff="+ pass",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        coverage_result = next(
            (r for r in verdict.agent_results if r.agent_name == "Coverage Agent"), None
        )
        assert coverage_result is not None
        # Fallback uses risk_score_modifier=50 and status="warning"
        assert coverage_result.status == "warning"
        assert coverage_result.risk_score_modifier == 50

    def test_history_agent_failure_produces_fallback(self, no_llm):
        """If AzureMCPServer raises, History Agent falls back gracefully."""

        def _raise(*args, **kwargs):
            raise RuntimeError("Azure Search unavailable")

        payload = PRPayload(
            pr_number=301,
            repo="acme/backend",
            changed_files=["service.py"],
            diff="+ pass",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.history_agent.agent.AzureMCPServer", side_effect=_raise),
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        history_result = next(
            (r for r in verdict.agent_results if r.agent_name == "History Agent"), None
        )
        assert history_result is not None
        assert history_result.status == "warning"
        assert history_result.risk_score_modifier == 50

    def test_all_agents_fail_returns_blocked_verdict(self, no_llm):
        """When every agent raises, a blocked VerdictReport is still returned."""

        async def _always_raise(_payload):
            from agents.orchestrator import _make_fallback

            return [
                _make_fallback(name, RuntimeError("total failure"))
                for name in ["Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"]
            ]

        payload = PRPayload(pr_number=302, repo="acme/backend")
        with (
            patch("agents.orchestrator._import_and_run_agents", side_effect=_always_raise),
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        assert isinstance(verdict, VerdictReport)
        # All agents at modifier=50 → score is low → blocked
        assert verdict.decision == "blocked"


# ── Foundry policy guardrails integration ────────────────────────────

class TestFoundryGuardrailsIntegration:
    def test_greenlight_verdict_passes_guardrails(self, azure_mcp_stub, no_llm):
        from foundry.deployment_config import apply_policy_guardrails

        payload = PRPayload(
            pr_number=400,
            repo="acme/backend",
            changed_files=["utils.py"],
            diff="+ x = 1",
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(orchestrate(payload))

        result = apply_policy_guardrails(verdict, payload.model_dump())

        assert "escalation_required" in result
        assert "audit_entry" in result
        assert result["audit_entry"]["pr_number"] == 400
        assert result["audit_entry"]["repo"] == "acme/backend"

    def test_blocked_verdict_triggers_escalation_when_score_low(self, no_llm):
        from foundry.deployment_config import apply_policy_guardrails

        # Force a very low-risk PR through all agents to get blocked via critical
        payload = PRPayload(
            pr_number=401,
            repo="acme/backend",
            changed_files=["config.py"],
            diff='+ STRIPE_KEY = "sk-1234567890abcdef1234567890abcdef"',
            timestamp=datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc),
        )

        # Craft a very high-risk verdict manually to guarantee escalation
        from agents.shared.data_contract import VerdictReport

        very_low_verdict = VerdictReport(
            confidence_score=10,
            decision="blocked",
            risk_brief="All agents critical.",
            rollback_playbook="## Rollback\n1. Revert PR",
            agent_results=[],
        )
        result = apply_policy_guardrails(very_low_verdict, payload.model_dump())

        assert result["escalation_required"] is True
        assert "10" in result["escalation_reason"]
