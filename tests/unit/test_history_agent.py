"""
Tests for the PRism History Agent.

Covers risk-score/status thresholds, file↔incident correlation behavior,
recency ordering for incident detail findings, and the public run() coroutine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agents.history_agent.agent import HistoryAgent, run
from agents.shared.data_contract import AgentResult


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


# ── run() public coroutine tests ────────────────────────────────────


class TestRunPublicInterface:
    """Tests for the public async run() coroutine used by the orchestrator."""

    def test_run_no_files_no_repo_ctx_returns_pass(self):
        """Empty file list with no repo_ctx should return pass with minimal risk."""
        result = asyncio.run(run(changed_files=[]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert result.status == "pass"
        assert result.risk_score_modifier == 0

    def test_run_files_no_repo_ctx_reports_no_deployment_connection(self):
        """With files but no repo_ctx, agent reports no deployment connection."""
        result = asyncio.run(run(changed_files=["payment_service.py"]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert result.status == "pass"
        assert any("No deployment connection" in f for f in result.findings)

    def test_run_with_mock_agent_returns_valid_agent_result(self):
        """run() with a mock HistoryAgent returns a properly validated AgentResult."""
        mock_agent = MagicMock()
        mock_agent.analyze_pr.return_value = {
            "agent_name": "History Agent",
            "risk_score_modifier": 30,
            "status": "pass",
            "findings": ["payment_service.py involved in 3 incident(s)"],
            "recommended_action": "No significant incident history.",
        }

        with patch("agents.history_agent.agent.HistoryAgent", return_value=mock_agent):
            result = asyncio.run(run(changed_files=["payment_service.py"]))

        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert result.risk_score_modifier == 30
        assert result.status == "pass"

    def test_run_data_contract_compliance(self):
        """run() must always return a valid, serialisable AgentResult."""
        result = asyncio.run(run(changed_files=["any_file.py"]))

        assert isinstance(result, AgentResult)
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")
        assert isinstance(result.findings, list)
        assert isinstance(result.recommended_action, str)
        # Round-trip through JSON serialization must be lossless
        parsed = AgentResult.from_json(result.to_json())
        assert parsed == result
