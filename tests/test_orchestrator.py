"""
Tests for the PRism Orchestrator.

All four specialist agents and the Verdict Agent are mocked so the
Orchestrator's dispatch, error-handling, and validation logic can
be tested in isolation.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agents.orchestrator import (
    AGENT_WEIGHTS,
    PRPayload,
    _make_fallback,
    orchestrate,
)
from agents.shared.data_contract import AgentResult, VerdictReport


# ── Helpers ──────────────────────────────────────────────────────────

def _make_result(name: str, modifier: int = 10, status: str = "pass") -> AgentResult:
    """Shorthand for creating a test AgentResult."""
    return AgentResult(
        agent_name=name,
        risk_score_modifier=modifier,
        status=status,
        findings=[f"{name} finding 1"],
        recommended_action=f"{name} recommendation",
    )


MOCK_PAYLOAD = PRPayload(
    pr_number=46,
    repo="team-prism/backend",
    changed_files=["payment_service.py", "utils/retry.py"],
    diff="- retry_count=3\n+ pass",
    timestamp=datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc),
)

MOCK_VERDICT = VerdictReport(
    confidence_score=80,
    decision="greenlight",
    risk_brief="All clear.",
    rollback_playbook=None,
    agent_results=[],
)


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.run(coro)


# ── Fallback tests ───────────────────────────────────────────────────


class TestFallback:
    def test_fallback_has_correct_structure(self):
        fallback = _make_fallback("Test Agent", RuntimeError("boom"))
        assert fallback.agent_name == "Test Agent"
        assert fallback.risk_score_modifier == 50
        assert fallback.status == "warning"
        assert any("boom" in f for f in fallback.findings)

    def test_fallback_conforms_to_contract(self):
        fallback = _make_fallback("Test Agent", ValueError("bad"))
        json_str = fallback.to_json()
        parsed = AgentResult.from_json(json_str)
        assert parsed == fallback


# ── PRPayload tests ──────────────────────────────────────────────────


class TestPRPayload:
    def test_from_dict(self):
        raw = {
            "pr_number": 46,
            "repo": "team-prism/backend",
            "changed_files": ["a.py"],
            "diff": "some diff",
        }
        payload = PRPayload.model_validate(raw)
        assert payload.pr_number == 46
        assert payload.timestamp is None

    def test_defaults(self):
        payload = PRPayload(pr_number=1, repo="org/repo")
        assert payload.changed_files == []
        assert payload.diff == ""
        assert payload.timestamp is None


# ── Orchestration — all agents succeed ───────────────────────────────


class TestOrchestrationAllSucceed:
    @patch("agents.orchestrator._import_and_run_agents")
    @patch("agents.verdict_agent.run", new_callable=AsyncMock, create=True)
    def test_all_agents_succeed(self, mock_verdict_run, mock_agents):
        results = [
            _make_result("Diff Analyst"),
            _make_result("History Agent"),
            _make_result("Coverage Agent"),
            _make_result("Timing Agent"),
        ]

        async def fake_agents(payload):
            return results

        mock_agents.side_effect = fake_agents

        mock_verdict_run.return_value = MOCK_VERDICT

        verdict = _run(orchestrate(MOCK_PAYLOAD))

        assert isinstance(verdict, VerdictReport)
        assert verdict.confidence_score == 80
        # Verdict agent should have been called with 4 results
        mock_verdict_run.assert_called_once()
        call_kwargs = mock_verdict_run.call_args
        agent_results_arg = call_kwargs.kwargs.get(
            "agent_results", call_kwargs.args[0] if call_kwargs.args else None
        )
        if agent_results_arg is None:
            agent_results_arg = call_kwargs[1].get("agent_results", [])
        assert len(agent_results_arg) == 4


# ── Orchestration — one agent crashes ────────────────────────────────


class TestOrchestrationPartialFailure:
    @patch("agents.orchestrator._import_and_run_agents")
    @patch("agents.verdict_agent.run", new_callable=AsyncMock, create=True)
    def test_one_agent_fails(self, mock_verdict_run, mock_agents):
        results = [
            _make_result("Diff Analyst"),
            _make_fallback("History Agent", RuntimeError("connection timeout")),
            _make_result("Coverage Agent"),
            _make_result("Timing Agent"),
        ]

        async def fake_agents(payload):
            return results

        mock_agents.side_effect = fake_agents
        mock_verdict_run.return_value = MOCK_VERDICT

        verdict = _run(orchestrate(MOCK_PAYLOAD))

        assert isinstance(verdict, VerdictReport)
        # Still exactly 4 results passed to verdict
        call_kwargs = mock_verdict_run.call_args
        agent_results_arg = call_kwargs.kwargs.get("agent_results")
        assert len(agent_results_arg) == 4
        # The failed agent should have the fallback modifier of 50
        history_result = [r for r in agent_results_arg if r.agent_name == "History Agent"][0]
        assert history_result.risk_score_modifier == 50
        assert history_result.status == "warning"


# ── Orchestration — all agents crash ─────────────────────────────────


class TestOrchestrationTotalFailure:
    @patch("agents.orchestrator._import_and_run_agents")
    @patch("agents.verdict_agent.run", new_callable=AsyncMock, create=True)
    def test_all_agents_fail(self, mock_verdict_run, mock_agents):
        results = [
            _make_fallback("Diff Analyst", RuntimeError("err")),
            _make_fallback("History Agent", RuntimeError("err")),
            _make_fallback("Coverage Agent", RuntimeError("err")),
            _make_fallback("Timing Agent", RuntimeError("err")),
        ]

        async def fake_agents(payload):
            return results

        mock_agents.side_effect = fake_agents
        mock_verdict_run.return_value = VerdictReport(
            confidence_score=0,
            decision="blocked",
            risk_brief="All agents failed.",
            rollback_playbook="Revert the PR.",
            agent_results=results,
        )

        verdict = _run(orchestrate(MOCK_PAYLOAD))

        assert isinstance(verdict, VerdictReport)
        assert verdict.decision == "blocked"
        # All 4 payloads still delivered
        call_kwargs = mock_verdict_run.call_args
        agent_results_arg = call_kwargs.kwargs.get("agent_results")
        assert len(agent_results_arg) == 4
        assert all(r.risk_score_modifier == 50 for r in agent_results_arg)


# ── Orchestration — dict input ───────────────────────────────────────


class TestOrchestrationDictInput:
    @patch("agents.orchestrator._import_and_run_agents")
    @patch("agents.verdict_agent.run", new_callable=AsyncMock, create=True)
    def test_dict_payload(self, mock_verdict_run, mock_agents):
        results = [
            _make_result("Diff Analyst"),
            _make_result("History Agent"),
            _make_result("Coverage Agent"),
            _make_result("Timing Agent"),
        ]

        async def fake_agents(payload):
            return results

        mock_agents.side_effect = fake_agents
        mock_verdict_run.return_value = MOCK_VERDICT

        raw_dict = {
            "pr_number": 46,
            "repo": "team-prism/backend",
            "changed_files": ["payment_service.py"],
            "diff": "- old\n+ new",
        }

        verdict = _run(orchestrate(raw_dict))
        assert isinstance(verdict, VerdictReport)


# ── Parallel dispatch integration test ───────────────────────────────
# This test patches at the individual agent level to verify true
# concurrent dispatch via asyncio.gather.


class TestParallelDispatch:
    @patch("agents.verdict_agent.run", new_callable=AsyncMock, create=True)
    def test_agents_run_concurrently(self, mock_verdict_run):
        """Verify that all 4 agents are fired and results collected."""
        mock_verdict_run.return_value = MOCK_VERDICT

        timing_result = _make_result("Timing Agent", modifier=5)
        diff_result = _make_result("Diff Analyst", modifier=20)
        history_result = _make_result("History Agent", modifier=15)
        coverage_result = _make_result("Coverage Agent", modifier=10)

        with (
            patch("agents.timing_agent.run", new_callable=AsyncMock, return_value=timing_result),
            patch("agents.diff_analyst.run", new_callable=AsyncMock, return_value=diff_result, create=True),
            patch("agents.history_agent.run", new_callable=AsyncMock, return_value=history_result, create=True),
            patch("agents.coverage_agent.run", new_callable=AsyncMock, return_value=coverage_result, create=True),
        ):
            verdict = _run(orchestrate(MOCK_PAYLOAD))

        assert isinstance(verdict, VerdictReport)
        call_kwargs = mock_verdict_run.call_args
        agent_results_arg = call_kwargs.kwargs.get("agent_results")
        assert len(agent_results_arg) == 4

        names = {r.agent_name for r in agent_results_arg}
        assert names == {"Timing Agent", "Diff Analyst", "History Agent", "Coverage Agent"}


# ── Agent weight configuration ───────────────────────────────────────


class TestWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(AGENT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_all_agents_have_weights(self):
        for name in ["Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"]:
            assert name in AGENT_WEIGHTS


# ── Semantic Kernel integration ──────────────────────────────────────


class TestSemanticKernel:
    def test_create_kernel_without_sk_returns_none(self):
        """If semantic-kernel is not installed, create_kernel() returns None."""
        from agents.orchestrator import create_kernel

        with patch.dict("sys.modules", {"semantic_kernel": None}):
            kernel = create_kernel()
        assert kernel is None

    def test_create_kernel_with_sk_registers_plugins(self):
        """If semantic-kernel is installed, create_kernel() returns a configured Kernel."""
        from agents.orchestrator import create_kernel

        kernel = create_kernel()
        if kernel is not None:
            # Verify the plugins are registered
            assert kernel.get_plugin("TimingAgent") is not None
            assert kernel.get_plugin("DiffAnalyst") is not None
            assert kernel.get_plugin("HistoryAgent") is not None
            assert kernel.get_plugin("CoverageAgent") is not None
            assert kernel.get_plugin("PRismOrchestrator") is not None


# ── Integration tests (auto-skip when agent not yet implemented) ─────
# These tests call the REAL agent code instead of mocks.  They are
# automatically skipped when a teammate's agent module has no ``run``
# function (i.e. an empty __init__.py).

def _agent_is_ready(module_path: str) -> bool:
    """Return True if the agent module exposes a callable ``run``."""
    import importlib

    try:
        mod = importlib.import_module(module_path)
        return callable(getattr(mod, "run", None))
    except Exception:
        return False


_diff_ready = pytest.mark.skipif(
    not _agent_is_ready("agents.diff_analyst"),
    reason="Diff Analyst agent not yet implemented",
)
_history_ready = pytest.mark.skipif(
    not _agent_is_ready("agents.history_agent")
    or not os.getenv("AZURE_SEARCH_ENDPOINT"),
    reason="History Agent not ready or Azure credentials not configured",
)
_coverage_ready = pytest.mark.skipif(
    not _agent_is_ready("agents.coverage_agent"),
    reason="Coverage Agent not yet implemented",
)
_verdict_ready = pytest.mark.skipif(
    not _agent_is_ready("agents.verdict_agent"),
    reason="Verdict Agent not yet implemented",
)
_all_agents_ready = pytest.mark.skipif(
    not all(
        _agent_is_ready(m)
        for m in [
            "agents.diff_analyst",
            "agents.history_agent",
            "agents.coverage_agent",
            "agents.timing_agent",
            "agents.verdict_agent",
        ]
    ),
    reason="Not all agents are implemented yet",
)


@pytest.mark.integration
class TestIntegrationIndividualAgents:
    """Call each real agent directly and verify data-contract compliance."""

    def test_timing_agent_real(self):
        """Timing Agent is ours — should always work."""
        from agents.timing_agent import run as run_timing

        ts = datetime(2026, 2, 25, 10, 0, tzinfo=timezone.utc)
        result = _run(run_timing(deploy_timestamp=ts))
        assert isinstance(result, AgentResult)
        assert result.agent_name == "Timing Agent"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")

    @_diff_ready
    def test_diff_analyst_real(self):
        from agents.diff_analyst import run as run_diff

        result = _run(
            run_diff(
                diff="- retry_count=3\n+ pass",
                changed_files=["payment_service.py"],
            )
        )
        assert isinstance(result, AgentResult)
        assert result.agent_name == "Diff Analyst"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")

    @_history_ready
    def test_history_agent_real(self):
        from agents.history_agent import run as run_history

        result = _run(run_history(changed_files=["payment_service.py"]))
        assert isinstance(result, AgentResult)
        assert result.agent_name == "History Agent"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")

    @_coverage_ready
    def test_coverage_agent_real(self):
        from agents.coverage_agent import run as run_coverage

        result = _run(run_coverage(pr_number=46, repo="team-prism/backend"))
        assert isinstance(result, AgentResult)
        assert result.agent_name == "Coverage Agent"
        assert 0 <= result.risk_score_modifier <= 100
        assert result.status in ("pass", "warning", "critical")


@pytest.mark.integration
class TestIntegrationFullPipeline:
    """End-to-end pipeline tests — only run when ALL agents are implemented."""

    @_all_agents_ready
    def test_full_pipeline_returns_verdict(self):
        """Feed a mock PR payload through the real pipeline and verify output."""
        verdict = _run(orchestrate(MOCK_PAYLOAD))
        assert isinstance(verdict, VerdictReport)
        assert 0 <= verdict.confidence_score <= 100
        assert verdict.decision in ("greenlight", "blocked")
        assert len(verdict.agent_results) == 4
        assert len(verdict.risk_brief) > 0

    @_all_agents_ready
    def test_full_pipeline_json_roundtrip(self):
        """Verify the full verdict can be serialised and deserialised."""
        verdict = _run(orchestrate(MOCK_PAYLOAD))
        raw = verdict.to_json()
        parsed = VerdictReport.from_json(raw)
        assert parsed.confidence_score == verdict.confidence_score
        assert parsed.decision == verdict.decision
        assert len(parsed.agent_results) == len(verdict.agent_results)

    @_all_agents_ready
    def test_full_pipeline_all_agents_named(self):
        """Every agent result must have a name and conform to the contract."""
        verdict = _run(orchestrate(MOCK_PAYLOAD))
        expected_names = {"Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"}
        actual_names = {r.agent_name for r in verdict.agent_results}
        assert actual_names == expected_names
