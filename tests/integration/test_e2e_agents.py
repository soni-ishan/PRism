"""
PRism – End-to-End Agent Integration Tests
==========================================
Tests each specialist agent's *actual* implementation end-to-end.

Strategy
--------
* No logic mocks — every code path in the agent itself is exercised for real.
* Only *external I/O* (Azure AI Search, Azure OpenAI) is stubbed so the suite
  runs without credentials in local dev and CI.
* Tests marked ``@pytest.mark.azure_required`` are auto-skipped unless the
  required env vars are present (see tests/conftest.py).

Agents under test
-----------------
1. Timing Agent       — pure datetime logic, no I/O
2. Diff Analyst       — heuristic path + optional LLM
3. History Agent      — AzureMCPServer is patched; real HistoryAgent logic runs
4. Verdict Agent      — LLM path is patched; real scoring/template logic runs
5. Coverage Agent     — stub module; tested via fallback behaviour
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agents.shared.data_contract import AgentResult, VerdictReport


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_incident(
    incident_id: str,
    timestamp: str,
    files: list[str],
    *,
    severity: str = "high",
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "id": incident_id,
        "timestamp": timestamp,
        "title": title or f"Incident {incident_id}",
        "severity": severity,
        "files_involved": files,
        "root_cause": "test root cause",
        "error_message": "test error",
    }


def _load_mock_incidents() -> list[dict]:
    """Load the bundled mock_incidents.json shipped with the History Agent."""
    mock_path = (
        Path(__file__).parent.parent.parent
        / "agents"
        / "history_agent"
        / "mock_incidents.json"
    )
    if not mock_path.exists():
        return []
    data = json.loads(mock_path.read_text(encoding="utf-8"))
    # The file wraps the list under an "incidents" key
    if isinstance(data, dict) and "incidents" in data:
        return data["incidents"]
    if isinstance(data, list):
        return data
    return []


# ── Timing Agent ──────────────────────────────────────────────────────

class TestTimingAgentE2E:
    """Timing Agent has no external I/O – every test is a true E2E run."""

    def test_clean_tuesday_morning_passes(self):
        from agents.timing_agent import run

        ts = datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc)  # Tuesday 10:00 UTC
        result = _run(run(deploy_timestamp=ts))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "Timing Agent"
        assert result.status == "pass"
        assert result.risk_score_modifier == 0
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)

    def test_friday_evening_is_critical(self):
        from agents.timing_agent import run

        ts = datetime(2026, 2, 27, 17, 0, tzinfo=timezone.utc)  # Friday 17:00 UTC
        result = _run(run(deploy_timestamp=ts))

        assert result.status == "critical"
        assert result.risk_score_modifier >= 55
        assert any("Friday" in f for f in result.findings)

    def test_christmas_day_is_warning(self):
        from agents.timing_agent import run

        ts = datetime(2026, 12, 25, 10, 0, tzinfo=timezone.utc)
        result = _run(run(deploy_timestamp=ts))

        assert result.status == "warning"
        assert result.risk_score_modifier >= 50
        assert any("Christmas" in f for f in result.findings)

    def test_no_timestamp_defaults_to_now(self):
        from agents.timing_agent import run

        result = _run(run())  # omit timestamp → uses datetime.now()

        assert result.agent_name == "Timing Agent"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")

    def test_release_proximity_raises_risk(self):
        from agents.timing_agent import run

        ts = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        result = _run(run(deploy_timestamp=ts, release_date=date(2026, 3, 10)))

        assert any("release" in f.lower() for f in result.findings)
        assert result.risk_score_modifier >= 15

    def test_result_round_trips_data_contract(self):
        from agents.timing_agent import run

        ts = datetime(2026, 2, 25, 11, 0, tzinfo=timezone.utc)
        result = _run(run(deploy_timestamp=ts))

        serialised = result.to_json()
        parsed = AgentResult.from_json(serialised)
        assert parsed == result

    def test_worst_case_score_capped_at_100(self):
        from agents.timing_agent import run

        ts = datetime(2026, 12, 25, 17, 30, tzinfo=timezone.utc)
        result = _run(run(deploy_timestamp=ts, release_date=date(2026, 12, 25)))

        assert result.risk_score_modifier <= 100
        assert result.status == "critical"


# ── Diff Analyst ──────────────────────────────────────────────────────

class TestDiffAnalystE2E:
    """Diff Analyst heuristic path requires no external calls."""

    def test_clean_diff_passes(self):
        from agents.diff_analyst import run

        diff = "+ logger.info('deployment started')\n+ version = '2.0.1'"
        result = _run(run(diff=diff, changed_files=["deploy.py"]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "Diff Analyst"
        assert result.status in ("pass", "warning", "critical")
        assert 0 <= result.risk_score_modifier <= 100

    def test_hardcoded_secret_is_critical(self):
        from agents.diff_analyst import run

        diff = '+ API_KEY = "sk-1234567890abcdef1234567890abcdef"'
        result = _run(run(diff=diff, changed_files=["config.py"]))

        assert result.status == "critical"
        assert result.risk_score_modifier >= 85

    def test_removed_retry_logic_is_warning(self):
        from agents.diff_analyst import run

        diff = "- retry_count = 3\n- backoff_factor = 2\n+ pass"
        result = _run(run(diff=diff, changed_files=["http_client.py"]))

        assert result.status in ("warning", "critical")
        assert result.risk_score_modifier > 0

    def test_removed_error_handling_is_warning(self):
        from agents.diff_analyst import run

        diff = (
            "- try:\n"
            "-     do_payment()\n"
            "- except PaymentError:\n"
            "-     handle_error()\n"
            "+ do_payment()"
        )
        result = _run(run(diff=diff, changed_files=["payment.py"]))

        assert result.status in ("warning", "critical")
        assert any("error handling" in f.lower() for f in result.findings)

    def test_destructive_sql_migration_is_warning(self):
        from agents.diff_analyst import run

        diff = "+ ALTER TABLE users DROP COLUMN password_hash"
        result = _run(run(diff=diff, changed_files=["migrations/0042.sql"]))

        assert result.status in ("warning", "critical")
        assert len(result.findings) > 0

    def test_empty_diff_returns_warning(self):
        from agents.diff_analyst import run

        result = _run(run(diff="", changed_files=["main.py"]))

        assert result.agent_name == "Diff Analyst"
        assert result.status == "warning"
        assert len(result.findings) > 0

    def test_result_conforms_to_data_contract(self):
        from agents.diff_analyst import run

        diff = "+ print('hello world')"
        result = _run(run(diff=diff, changed_files=["test.py"]))

        assert result.agent_name == "Diff Analyst"
        assert result.status in ("pass", "warning", "critical")
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)

        parsed = AgentResult.from_json(result.to_json())
        assert parsed == result


# ── History Agent ─────────────────────────────────────────────────────

class TestHistoryAgentE2E:
    """
    Tests the real HistoryAgent logic.

    AzureMCPServer is replaced with a lightweight stub whose
    ``query_incidents_by_files_search`` returns controlled incident data,
    so the full analyse/correlate/score logic runs un-mocked.
    """

    @staticmethod
    def _make_mock_mcp(incidents: list[dict]) -> MagicMock:
        mock_mcp = MagicMock()
        mock_mcp.query_incidents_by_files_search.return_value = incidents
        return mock_mcp

    def test_no_incident_history_passes(self):
        from agents.history_agent.agent import HistoryAgent

        agent = HistoryAgent(azure_mcp=self._make_mock_mcp([]))
        result = agent.analyze_pr(["brand_new_module.py"])

        assert result["status"] == "pass"
        assert result["risk_score_modifier"] == 0
        assert result["agent_name"] == "History Agent"

    def test_four_recent_incidents_is_warning(self):
        from agents.history_agent.agent import HistoryAgent

        incidents = [
            _make_incident(f"INC-{i}", f"2026-02-{20+i:02d}T10:00:00Z", ["payment_service.py"])
            for i in range(4)
        ]
        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        result = agent.analyze_pr(["payment_service.py"])

        assert result["status"] == "warning"
        assert result["risk_score_modifier"] == 40

    def test_two_files_many_incidents_is_critical(self):
        """Two files with 5 incidents each: 50+50=100 ≥ 70 → critical."""
        from agents.history_agent.agent import HistoryAgent

        incidents = [
            _make_incident(f"INC-A{i}", f"2026-02-{20+i:02d}T10:00:00Z", ["db/schema.py"])
            for i in range(5)
        ] + [
            _make_incident(f"INC-B{i}", f"2026-02-{15+i:02d}T10:00:00Z", ["payment_service.py"])
            for i in range(5)
        ]
        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        result = agent.analyze_pr(["db/schema.py", "payment_service.py"])

        assert result["status"] == "critical"
        assert result["risk_score_modifier"] >= 70

    def test_unrelated_files_produce_no_false_positive(self):
        from agents.history_agent.agent import HistoryAgent

        incidents = [
            _make_incident("INC-1", "2026-02-24T10:00:00Z", ["superuser.py"])
        ]
        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        result = agent.analyze_pr(["user.py"])

        # "superuser.py" != "user.py" — strict matching prevents false positive
        assert result["status"] == "pass"
        assert result["risk_score_modifier"] == 0

    def test_findings_enumerate_most_recent_incidents(self):
        from agents.history_agent.agent import HistoryAgent

        incidents = [
            _make_incident("INC-OLD",    "2026-01-10T10:00:00Z", ["auth.py"], title="Old Auth Breach"),
            _make_incident("INC-RECENT", "2026-02-25T10:00:00Z", ["auth.py"], title="Latest Auth Failure"),
            _make_incident("INC-MID",    "2026-02-15T10:00:00Z", ["auth.py"], title="Mid Auth Issue"),
        ]
        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        result = agent.analyze_pr(["auth.py"])

        detail_findings = [f for f in result["findings"] if f.startswith("  └─ ")]
        assert len(detail_findings) == 2
        # Most recent first
        assert "2026-02-25" in detail_findings[0]
        assert "Latest Auth Failure" in detail_findings[0]

    def test_result_round_trips_via_agent_result_model(self):
        from agents.history_agent.agent import HistoryAgent

        incidents = [
            _make_incident("INC-1", "2026-02-24T10:00:00Z", ["utils.py"])
        ]
        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        raw = agent.analyze_pr(["utils.py"])
        ar = AgentResult.model_validate(raw)

        assert ar.agent_name == "History Agent"
        assert ar.status in ("pass", "warning", "critical")
        parsed = AgentResult.from_json(ar.to_json())
        assert parsed == ar

    def test_async_run_interface_propagates_result(self):
        """Tests the async run() function exposed to the orchestrator."""
        from agents.history_agent.agent import HistoryAgent, run as history_run

        incidents = [
            _make_incident("INC-1", "2026-02-24T10:00:00Z", ["payment_service.py"])
        ]
        mock_mcp = self._make_mock_mcp(incidents)

        with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
            result = _run(history_run(changed_files=["payment_service.py"]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"

    def test_with_bundled_mock_incidents_file(self):
        """Smoke-test the real mock_incidents.json shipped in the repo."""
        from agents.history_agent.agent import HistoryAgent

        incidents = _load_mock_incidents()
        if not incidents:
            pytest.skip("mock_incidents.json not found or empty")

        agent = HistoryAgent(azure_mcp=self._make_mock_mcp(incidents))
        result = agent.analyze_pr(["payment_service.py"])

        assert result["agent_name"] == "History Agent"
        assert result["status"] in ("pass", "warning", "critical")
        assert 0 <= result["risk_score_modifier"] <= 100


# ── Coverage Agent ────────────────────────────────────────────────────

class TestCoverageAgentFallback:
    """
    The Coverage Agent now has a real run() implementation.
    These tests verify the orchestrator's fallback mechanism still works
    when the agent raises an unexpected error.
    """

    def test_coverage_agent_module_has_run(self):
        import agents.coverage_agent as ca

        assert hasattr(ca, "run"), "coverage_agent must expose a run() function"

    def test_orchestrator_returns_fallback_for_missing_coverage(self):
        from agents.orchestrator import _make_fallback

        err = ImportError("cannot import name 'run' from 'agents.coverage_agent'")
        fallback = _make_fallback("Coverage Agent", err)

        assert fallback.agent_name == "Coverage Agent"
        assert fallback.risk_score_modifier == 50
        assert fallback.status == "warning"
        assert len(fallback.findings) > 0

    def test_fallback_conforms_to_data_contract(self):
        from agents.orchestrator import _make_fallback

        fallback = _make_fallback("Coverage Agent", RuntimeError("stub agent"))
        parsed = AgentResult.from_json(fallback.to_json())
        assert parsed == fallback


# ── Verdict Agent ─────────────────────────────────────────────────────

class TestVerdictAgentE2E:
    """
    Tests the Verdict Agent's real scoring, decision, and template engines.
    The LLM calls are patched so the deterministic paths run end-to-end.
    """

    @staticmethod
    def _four_results(
        diff: int = 10,
        hist: int = 10,
        cov: int = 10,
        timing: int = 10,
        statuses: dict[str, str] | None = None,
    ) -> list[AgentResult]:
        s = statuses or {}
        return [
            AgentResult(
                agent_name="Diff Analyst",
                risk_score_modifier=diff,
                status=s.get("Diff Analyst", "pass"),
                findings=["Diff finding"],
                recommended_action="Diff recommendation",
            ),
            AgentResult(
                agent_name="History Agent",
                risk_score_modifier=hist,
                status=s.get("History Agent", "pass"),
                findings=["History finding"],
                recommended_action="History recommendation",
            ),
            AgentResult(
                agent_name="Coverage Agent",
                risk_score_modifier=cov,
                status=s.get("Coverage Agent", "pass"),
                findings=["Coverage finding"],
                recommended_action="Coverage recommendation",
            ),
            AgentResult(
                agent_name="Timing Agent",
                risk_score_modifier=timing,
                status=s.get("Timing Agent", "pass"),
                findings=["Timing finding"],
                recommended_action="Timing recommendation",
            ),
        ]

    _pr_payload = {
        "pr_number": 42,
        "repo": "acme/backend",
        "changed_files": ["api/handler.py"],
        "diff": "- old\n+ new",
    }

    def test_greenlight_low_risk(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        with patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None):
            verdict = _run(run(agent_results=self._four_results(5, 5, 5, 5), pr_payload=self._pr_payload))

        assert isinstance(verdict, VerdictReport)
        assert verdict.confidence_score == 95
        assert verdict.decision == "greenlight"
        assert verdict.rollback_playbook is None
        assert len(verdict.agent_results) == 4

    def test_blocked_high_risk(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(run(agent_results=self._four_results(80, 80, 80, 80), pr_payload=self._pr_payload))

        assert verdict.confidence_score == 20
        assert verdict.decision == "blocked"
        assert verdict.rollback_playbook is not None
        assert "git revert" in verdict.rollback_playbook

    def test_critical_single_agent_forces_block(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        results = self._four_results(0, 0, 0, 0, statuses={"Timing Agent": "critical"})

        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(run(agent_results=results, pr_payload=self._pr_payload))

        assert verdict.decision == "blocked"
        assert verdict.confidence_score == 100
        assert verdict.rollback_playbook is not None

    def test_risk_brief_contains_all_agent_names(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        results = self._four_results()
        with patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None):
            verdict = _run(run(agent_results=results, pr_payload=self._pr_payload))

        for r in results:
            assert r.agent_name in verdict.risk_brief

    def test_playbook_contains_pr_info(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        results = self._four_results(80, 80, 80, 80)
        with (
            patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None),
            patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None),
        ):
            verdict = _run(run(agent_results=results, pr_payload=self._pr_payload))

        assert "acme/backend" in verdict.rollback_playbook
        assert "42" in verdict.rollback_playbook

    def test_verdict_round_trips_data_contract(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        results = self._four_results(20, 10, 25, 5)
        with patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None):
            verdict = _run(run(agent_results=results, pr_payload=self._pr_payload))

        raw = verdict.to_json()
        parsed = VerdictReport.from_json(raw)
        assert parsed.confidence_score == verdict.confidence_score
        assert parsed.decision == verdict.decision
        assert len(parsed.agent_results) == len(verdict.agent_results)

    def test_no_pr_payload_defaults_gracefully(self):
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        results = self._four_results(5, 5, 5, 5)
        with patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None):
            verdict = _run(run(agent_results=results))

        assert isinstance(verdict, VerdictReport)
        assert verdict.confidence_score == 95

    def test_llm_enhancement_used_when_configured(self):
        """If LLM is configured the enhance function is called; result is used."""
        from agents.verdict_agent import run
        from unittest.mock import AsyncMock

        enhanced = "## LLM-Enhanced Risk Brief\nAll clear."
        results = self._four_results(5, 5, 5, 5)

        with patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=enhanced):
            verdict = _run(run(agent_results=results, pr_payload=self._pr_payload))

        assert verdict.risk_brief == enhanced


# ── Azure-live agent tests (skipped without credentials) ─────────────

@pytest.mark.azure_required
class TestHistoryAgentLive:
    """Calls the real Azure AI Search index. Requires valid env vars."""

    def test_query_payment_service_returns_results(self):
        from agents.history_agent.agent import HistoryAgent, run as history_run

        result = _run(history_run(changed_files=["payment_service.py"]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert result.risk_score_modifier > 0, (
            "Azure index should have incidents for payment_service.py from setup"
        )

    def test_query_unknown_file_passes(self):
        from agents.history_agent.agent import run as history_run

        result = _run(history_run(changed_files=["completely_new_file_xyz.py"]))

        assert isinstance(result, AgentResult)
        assert result.status == "pass"
        assert result.risk_score_modifier == 0
