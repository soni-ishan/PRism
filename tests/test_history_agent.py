"""
Tests for the PRism History Agent.

Covers risk-score/status thresholds, file↔incident correlation behavior,
and recency ordering for incident detail findings.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.history_agent.agent import HistoryAgent


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
    """Create a HistoryAgent with in-memory test data only."""
    agent = HistoryAgent()
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
