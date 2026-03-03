"""
Tests for the PRism Verdict Agent.

Covers scoring, decision logic, risk brief generation, rollback playbook,
contract compliance, and LLM fallback behaviour.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agents.orchestrator import AGENT_WEIGHTS
from agents.shared.data_contract import AgentResult, VerdictReport
from agents.verdict_agent import (
    _build_risk_brief,
    _build_rollback_playbook,
    _compute_score,
    _decide,
    run,
)


# ── Helpers ──────────────────────────────────────────────────────────

WEIGHTS = AGENT_WEIGHTS


def _make(
    name: str,
    modifier: int = 10,
    status: str = "pass",
    findings: list[str] | None = None,
) -> AgentResult:
    return AgentResult(
        agent_name=name,
        risk_score_modifier=modifier,
        status=status,
        findings=findings or [f"{name} finding"],
        recommended_action=f"{name} recommendation",
    )


def _four_results(
    diff: int = 10,
    hist: int = 10,
    cov: int = 10,
    timing: int = 10,
    *,
    statuses: dict[str, str] | None = None,
) -> list[AgentResult]:
    """Create a standard set of four agent results with given modifiers."""
    s = statuses or {}
    return [
        _make("Diff Analyst",   diff,   s.get("Diff Analyst", "pass")),
        _make("History Agent",  hist,   s.get("History Agent", "pass")),
        _make("Coverage Agent", cov,    s.get("Coverage Agent", "pass")),
        _make("Timing Agent",   timing, s.get("Timing Agent", "pass")),
    ]


PR_PAYLOAD = {
    "pr_number": 99,
    "repo": "acme/backend",
    "changed_files": ["api/handler.py", "db/schema.py"],
    "diff": "- old\n+ new",
}


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.run(coro)


# ── Scoring tests ────────────────────────────────────────────────────


class TestScoring:
    def test_all_zero_modifiers(self):
        results = _four_results(0, 0, 0, 0)
        assert _compute_score(results, WEIGHTS) == 100

    def test_all_max_modifiers(self):
        results = _four_results(100, 100, 100, 100)
        # 100 - (100*0.30 + 100*0.25 + 100*0.25 + 100*0.20) = 100 - 100 = 0
        assert _compute_score(results, WEIGHTS) == 0

    def test_mixed_modifiers(self):
        results = _four_results(diff=20, hist=10, cov=30, timing=5)
        # 100 - (20*0.30 + 10*0.25 + 30*0.25 + 5*0.20)
        # 100 - (6 + 2.5 + 7.5 + 1) = 100 - 17 = 83
        assert _compute_score(results, WEIGHTS) == 83

    def test_score_clamps_to_zero(self):
        # Impossible with real weights summing to 1.0 and modifiers capped
        # at 100, but test the clamp logic by using exaggerated weights.
        big_weights = {"Agent": 2.0}
        results = [_make("Agent", modifier=100)]
        assert _compute_score(results, big_weights) == 0

    def test_unknown_agent_uses_default_weight(self):
        results = [_make("Unknown Agent", modifier=40)]
        # Default weight = 0.25 → 100 - (40 * 0.25) = 90
        score = _compute_score(results, WEIGHTS)
        assert score == 90


# ── Decision tests ───────────────────────────────────────────────────


class TestDecision:
    def test_greenlight_when_score_high(self):
        results = _four_results(10, 10, 10, 10)
        assert _decide(90, results) == "greenlight"

    def test_blocked_when_score_below_70(self):
        results = _four_results(50, 50, 50, 50)
        assert _decide(50, results) == "blocked"

    def test_blocked_when_any_critical(self):
        results = _four_results(
            0, 0, 0, 0,
            statuses={"Timing Agent": "critical"},
        )
        # Score is 100 but critical override kicks in
        assert _decide(100, results) == "blocked"

    def test_edge_case_score_exactly_70(self):
        results = _four_results(10, 10, 10, 10)
        assert _decide(70, results) == "greenlight"

    def test_edge_case_score_69(self):
        results = _four_results(10, 10, 10, 10)
        assert _decide(69, results) == "blocked"


# ── Risk Brief tests ────────────────────────────────────────────────


class TestRiskBrief:
    def test_contains_all_agent_names(self):
        results = _four_results()
        brief = _build_risk_brief(results, 90, "greenlight")
        for r in results:
            assert r.agent_name in brief

    def test_contains_all_findings(self):
        results = _four_results()
        brief = _build_risk_brief(results, 90, "greenlight")
        for r in results:
            for f in r.findings:
                assert f in brief

    def test_contains_score_and_decision(self):
        brief = _build_risk_brief(_four_results(), 85, "greenlight")
        assert "85 / 100" in brief
        assert "GREENLIGHT" in brief

    def test_blocked_brief_shows_blocked_tag(self):
        brief = _build_risk_brief(_four_results(), 40, "blocked")
        assert "BLOCKED" in brief
        assert "40 / 100" in brief


# ── Rollback Playbook tests ─────────────────────────────────────────


class TestRollbackPlaybook:
    def test_contains_pr_info(self):
        playbook = _build_rollback_playbook(_four_results(), 40, PR_PAYLOAD)
        assert "acme/backend" in playbook
        assert "#99" in playbook or "99" in playbook

    def test_contains_revert_instructions(self):
        playbook = _build_rollback_playbook(_four_results(), 40, PR_PAYLOAD)
        assert "git revert" in playbook

    def test_lists_flagged_agents(self):
        results = _four_results(
            diff=60, hist=10, cov=10, timing=10,
            statuses={"Diff Analyst": "critical"},
        )
        playbook = _build_rollback_playbook(results, 30, PR_PAYLOAD)
        assert "Diff Analyst" in playbook
        assert "CRITICAL" in playbook


# ── Full run() tests ────────────────────────────────────────────────


class TestRunGreenlight:
    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    def test_greenlight_with_low_risk(self, _mock_llm):
        results = _four_results(5, 5, 5, 5)
        # 100 - (5*0.30 + 5*0.25 + 5*0.25 + 5*0.20) = 100 - 5 = 95
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))

        assert isinstance(verdict, VerdictReport)
        assert verdict.confidence_score == 95
        assert verdict.decision == "greenlight"
        assert verdict.rollback_playbook is None
        assert len(verdict.agent_results) == 4

    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    def test_greenlight_edge_score_70(self, _mock_llm):
        # 100 - (30*0.30 + 30*0.25 + 30*0.25 + 30*0.20) = 100 - 30 = 70
        results = _four_results(30, 30, 30, 30)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))

        assert verdict.confidence_score == 70
        assert verdict.decision == "greenlight"
        assert verdict.rollback_playbook is None


class TestRunBlocked:
    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    @patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None)
    def test_blocked_low_score(self, _mock_pb, _mock_brief):
        # 100 - (80*0.30 + 80*0.25 + 80*0.25 + 80*0.20) = 100 - 80 = 20
        results = _four_results(80, 80, 80, 80)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))

        assert verdict.confidence_score == 20
        assert verdict.decision == "blocked"
        assert verdict.rollback_playbook is not None
        assert "git revert" in verdict.rollback_playbook

    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    @patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None)
    def test_blocked_critical_override(self, _mock_pb, _mock_brief):
        results = _four_results(
            0, 0, 0, 0,
            statuses={"Coverage Agent": "critical"},
        )
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))

        # Score is 100 but critical forces blocked
        assert verdict.confidence_score == 100
        assert verdict.decision == "blocked"
        assert verdict.rollback_playbook is not None


# ── LLM Fallback tests ──────────────────────────────────────────────


class TestLLMFallback:
    @patch.dict("os.environ", {}, clear=True)
    def test_no_env_vars_uses_template(self):
        results = _four_results(5, 5, 5, 5)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))
        # Should succeed with template-based brief
        assert "PRism Risk Brief" in verdict.risk_brief
        assert verdict.decision == "greenlight"

    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    @patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None)
    def test_llm_failure_falls_back_to_template(self, _mock_pb, _mock_brief):
        results = _four_results(80, 80, 80, 80)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))
        assert "PRism Risk Brief" in verdict.risk_brief
        assert "Rollback Playbook" in verdict.rollback_playbook

    def test_llm_enhance_brief_passes_template_in_prompt(self):
        """_llm_enhance_brief must include the template brief in the user message."""
        from agents.verdict_agent import _llm_enhance_brief, _build_risk_brief

        results = _four_results(10, 10, 10, 10)
        template = _build_risk_brief(results, 90, "greenlight")

        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "LLM-enhanced brief"
        )

        with patch(
            "foundry.deployment_config.get_instrumented_openai_client",
            return_value=mock_client,
        ), patch.dict(
            "os.environ",
            {"AZURE_OPENAI_DEPLOYMENT": "gpt-4o"},
        ):
            result = _run(_llm_enhance_brief(results, template))

        assert result == "LLM-enhanced brief"
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_content = next(m["content"] for m in messages if m["role"] == "user")
        assert "Template brief:" in user_content
        assert template in user_content


# ── Contract Compliance tests ────────────────────────────────────────


class TestContractCompliance:
    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    def test_json_roundtrip(self, _mock_llm):
        results = _four_results(15, 10, 20, 5)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))
        raw = verdict.to_json()
        parsed = VerdictReport.from_json(raw)
        assert parsed.confidence_score == verdict.confidence_score
        assert parsed.decision == verdict.decision
        assert len(parsed.agent_results) == len(verdict.agent_results)

    @patch("agents.verdict_agent._llm_enhance_brief", new_callable=AsyncMock, return_value=None)
    @patch("agents.verdict_agent._llm_enhance_playbook", new_callable=AsyncMock, return_value=None)
    def test_blocked_roundtrip_with_playbook(self, _mock_pb, _mock_brief):
        results = _four_results(80, 80, 80, 80)
        verdict = _run(run(agent_results=results, pr_payload=PR_PAYLOAD))
        raw = verdict.to_json()
        parsed = VerdictReport.from_json(raw)
        assert parsed.decision == "blocked"
        assert parsed.rollback_playbook is not None
        assert len(parsed.rollback_playbook) > 0

    def test_no_payload_defaults_gracefully(self):
        results = _four_results(5, 5, 5, 5)
        verdict = _run(run(agent_results=results))
        assert isinstance(verdict, VerdictReport)
        assert verdict.confidence_score == 95
