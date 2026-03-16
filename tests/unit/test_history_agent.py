"""
Tests for the PRism History Agent.

Covers risk-score/status thresholds, file↔incident correlation behavior,
recency ordering for incident detail findings, and the public run() entrypoint.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agents.history_agent.agent import HistoryAgent, run
from agents.shared.data_contract import AgentResult, RepoContext


def _incident(
    incident_id: str,
    timestamp: str,
    files: list[str],
    *,
    severity: str = "high",
    title: str | None = None,
) -> dict:
    """Build a minimal incident payload used by HistoryAgent tests."""
    return {
        "id": incident_id,
        "timestamp": timestamp,
        "title": title or f"Incident {incident_id}",
        "severity": severity,
        "files_involved": files,
    }


def _agent_with_data(incidents: list[dict], deployment_events: list[dict] | None = None) -> HistoryAgent:
    """Create a HistoryAgent with in-memory test data only (no Azure needed)."""
    mock_mcp = MagicMock()
    # Return test incidents when Azure Search is queried
    mock_mcp.query_incidents_by_files_search.return_value = incidents
    agent = HistoryAgent(azure_mcp=mock_mcp)
    agent.incidents = incidents
    agent.deployment_events = deployment_events or []
    return agent


class TestHistoryAgentScoringThresholds:
    def test_status_pass_below_40(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["payment_service.py"]),
            _incident("INC-2", "2026-02-23T14:30:00Z", ["payment_service.py"]),
            _incident("INC-3", "2026-02-22T14:30:00Z", ["payment_service.py"]),
        ]
        agent = _agent_with_data(incidents)

        result = agent.analyze_pr(["payment_service.py"])

        assert result["risk_score_modifier"] == 30
        assert result["status"] == "pass"

    def test_status_warning_at_40_boundary(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["payment_service.py"]),
            _incident("INC-2", "2026-02-23T14:30:00Z", ["payment_service.py"]),
            _incident("INC-3", "2026-02-22T14:30:00Z", ["payment_service.py"]),
            _incident("INC-4", "2026-02-21T14:30:00Z", ["payment_service.py"]),
        ]
        agent = _agent_with_data(incidents)

        result = agent.analyze_pr(["payment_service.py"])

        assert result["risk_score_modifier"] == 40
        assert result["status"] == "warning"

    def test_status_critical_at_70_boundary(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["alpha.py"]),
            _incident("INC-2", "2026-02-23T14:30:00Z", ["alpha.py"]),
            _incident("INC-3", "2026-02-22T14:30:00Z", ["beta.py"]),
            _incident("INC-4", "2026-02-21T14:30:00Z", ["beta.py"]),
        ]
        now = datetime.now(timezone.utc)
        deployment_events = [
            {"timestamp": now.isoformat(), "files_changed": ["alpha.py"]},
            {"timestamp": now.isoformat(), "files_changed": ["alpha.py"]},
            {"timestamp": now.isoformat(), "files_changed": ["beta.py"]},
        ]
        agent = _agent_with_data(incidents, deployment_events=deployment_events)

        result = agent.analyze_pr(["alpha.py", "beta.py"])

        assert result["risk_score_modifier"] == 70
        assert result["status"] == "critical"


class TestHistoryAgentCorrelation:
    def test_no_false_positive_for_substring_filename(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["superuser.py"]),
        ]
        agent = _agent_with_data(incidents)

        correlation = agent._correlate_files_with_incidents(["user.py"])

        assert correlation["user.py"] == []

    def test_correlates_by_basename_when_incident_stores_full_path(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["src/services/user.py"]),
        ]
        agent = _agent_with_data(incidents)

        correlation = agent._correlate_files_with_incidents(["user.py"])

        assert len(correlation["user.py"]) == 1


class TestHistoryAgentRecencyOrdering:
    def test_findings_use_most_recent_two_incidents(self):
        incidents = [
            _incident("INC-OLD", "2026-02-10T10:00:00Z", ["payment_service.py"], title="Oldest"),
            _incident("INC-NEWEST", "2026-02-25T10:00:00Z", ["payment_service.py"], title="Newest"),
            _incident("INC-MID", "2026-02-20T10:00:00Z", ["payment_service.py"], title="Middle"),
        ]
        agent = _agent_with_data(incidents)

        result = agent.analyze_pr(["payment_service.py"])
        findings = result["findings"]

        detail_findings = [f for f in findings if f.startswith("  └─ ")]

        assert len(detail_findings) == 2
        assert "2026-02-25" in detail_findings[0]
        assert "Newest" in detail_findings[0]
        assert "2026-02-20" in detail_findings[1]
        assert "Middle" in detail_findings[1]


# ── Tests for the public run() orchestrator entrypoint ───────────────


def _run_async(coro):
    """Helper to drive an async coroutine from synchronous test code."""
    return asyncio.run(coro)


class TestRunNoDeploymentConnection:
    """run() with no repo_ctx creates a disconnected agent and reports clearly."""

    def test_returns_agent_result_type(self):
        result = _run_async(run(changed_files=["api.py"]))
        assert isinstance(result, AgentResult)

    def test_agent_name_is_correct(self):
        result = _run_async(run(changed_files=["api.py"]))
        assert result.agent_name == "History Agent"

    def test_status_is_pass_when_no_connection(self):
        result = _run_async(run(changed_files=["api.py"]))
        assert result.status == "pass"

    def test_risk_score_is_zero_when_no_connection(self):
        result = _run_async(run(changed_files=["api.py"]))
        assert result.risk_score_modifier == 0

    def test_findings_mention_no_deployment_connection(self):
        result = _run_async(run(changed_files=["api.py"]))
        assert any("deployment connection" in f.lower() for f in result.findings)

    def test_empty_changed_files_returns_pass(self):
        result = _run_async(run(changed_files=[]))
        assert result.status == "pass"
        assert result.risk_score_modifier == 0

    def test_repo_ctx_without_index_acts_as_disconnected(self):
        ctx = RepoContext(owner="acme", repo="backend")  # no azure_search_index
        result = _run_async(run(changed_files=["app.py"], repo_ctx=ctx))
        assert result.status == "pass"
        assert result.risk_score_modifier == 0


class TestRunWithMockedAzure:
    """run() with a mock AzureMCPServer exercises the full pipeline."""

    def _make_ctx_with_index(self) -> RepoContext:
        return RepoContext(
            owner="acme",
            repo="backend",
            azure_search_endpoint="https://fake.search.windows.net",
            azure_search_key="fake-key",
            azure_search_index="incidents-acme-backend",
        )

    def test_run_returns_agent_result_with_incidents(self):
        incidents = [
            _incident("INC-1", "2026-02-24T14:30:00Z", ["payment.py"]),
            _incident("INC-2", "2026-02-23T14:30:00Z", ["payment.py"]),
        ]
        mock_mcp = MagicMock()
        mock_mcp.query_incidents_by_files_search.return_value = incidents

        with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
            ctx = self._make_ctx_with_index()
            result = _run_async(run(changed_files=["payment.py"], repo_ctx=ctx))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert result.status in ("pass", "warning", "critical")
        assert result.risk_score_modifier >= 0
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)

    def test_run_reflects_incident_count_in_risk_score(self):
        # 4 incidents on one file → risk_score_modifier == 40 → "warning"
        incidents = [
            _incident(f"INC-{i}", f"2026-02-{20+i:02d}T10:00:00Z", ["db.py"])
            for i in range(4)
        ]
        mock_mcp = MagicMock()
        mock_mcp.query_incidents_by_files_search.return_value = incidents

        with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
            ctx = self._make_ctx_with_index()
            result = _run_async(run(changed_files=["db.py"], repo_ctx=ctx))

        assert result.risk_score_modifier == 40
        assert result.status == "warning"

    def test_run_no_matching_incidents_returns_pass(self):
        mock_mcp = MagicMock()
        mock_mcp.query_incidents_by_files_search.return_value = []

        with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
            ctx = self._make_ctx_with_index()
            result = _run_async(run(changed_files=["new_feature.py"], repo_ctx=ctx))

        assert result.status == "pass"
        assert result.risk_score_modifier == 0

    def test_run_data_contract_roundtrip(self):
        """AgentResult returned by run() must survive JSON serialisation."""
        mock_mcp = MagicMock()
        mock_mcp.query_incidents_by_files_search.return_value = []

        with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
            ctx = self._make_ctx_with_index()
            result = _run_async(run(changed_files=["handler.py"], repo_ctx=ctx))

        parsed = AgentResult.from_json(result.to_json())
        assert parsed == result
