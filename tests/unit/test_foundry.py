"""
Tests for PRism Foundry Governance Module
==========================================
All Azure SDK calls are mocked so the tests run in CI without credentials.

Test classes:
  TestFoundryClient       — singleton AIProjectClient factory
  TestInstrumentedOpenAI  — instrumented AsyncAzureOpenAI client
  TestTracing             — OpenTelemetry → Application Insights setup
  TestTraceAgentCall      — async context-manager per-agent spans
  TestContentSafety       — Azure Content Safety integration
  TestPolicyGuardrails    — auto-escalation, playbook enforcement, audit
  TestEvaluateQuality     — groundedness / relevance heuristic
  TestConfig              — FOUNDRY_CONFIG structure
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents.shared.data_contract import AgentResult, VerdictReport
from foundry.deployment_config import (
    FOUNDRY_CONFIG,
    apply_policy_guardrails,
    check_content_safety,
    evaluate_quality,
    get_foundry_client,
    get_instrumented_openai_client,
    reset_foundry_client,
    reset_tracing,
    setup_tracing,
    trace_agent_call,
    trace_orchestrate,
)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_result(
    name: str = "Test Agent",
    modifier: int = 30,
    status: str = "warning",
    findings: list[str] | None = None,
    action: str = "Review manually",
) -> AgentResult:
    return AgentResult(
        agent_name=name,
        risk_score_modifier=modifier,
        status=status,
        findings=findings or ["test finding"],
        recommended_action=action,
    )


def _make_verdict(
    score: int = 80,
    decision: str = "greenlight",
    brief: str = "All clear",
    playbook: str | None = None,
    results: list[AgentResult] | None = None,
) -> VerdictReport:
    return VerdictReport(
        confidence_score=score,
        decision=decision,
        risk_brief=brief,
        rollback_playbook=playbook,
        agent_results=results or [],
    )


# ── TestFoundryClient ─────────────────────────────────────────────────

class TestFoundryClient:
    """Tests for the singleton AIProjectClient factory."""

    def setup_method(self):
        reset_foundry_client()

    def teardown_method(self):
        reset_foundry_client()

    def test_returns_none_without_connection_string(self):
        """Should return None when AZURE_FOUNDRY_PROJECT_CONNECTION_STRING is empty."""
        with patch.dict(FOUNDRY_CONFIG, {"project_connection_string": ""}):
            assert get_foundry_client() is None

    def test_returns_none_when_sdk_missing(self):
        """Should return None when azure-ai-projects is not installed."""
        with patch.dict(
            FOUNDRY_CONFIG,
            {"project_connection_string": "some-connection-string"},
        ):
            with patch.dict("sys.modules", {"azure.ai.projects": None}):
                assert get_foundry_client() is None

    def test_singleton_returns_same_instance(self):
        """Second call should return the cached value, not re-initialise."""
        with patch.dict(FOUNDRY_CONFIG, {"project_connection_string": ""}):
            first = get_foundry_client()
            second = get_foundry_client()
            assert first is second  # Both None, but from cache

    def test_reset_allows_reinitialisation(self):
        """reset_foundry_client() should clear the cache."""
        with patch.dict(FOUNDRY_CONFIG, {"project_connection_string": ""}):
            get_foundry_client()
            reset_foundry_client()
            # After reset, the internal flag is cleared
            result = get_foundry_client()
            assert result is None  # Still None, but re-evaluated


# ── TestInstrumentedOpenAI ────────────────────────────────────────────

class TestInstrumentedOpenAI:
    """Tests for the instrumented AsyncAzureOpenAI client factory."""

    def test_returns_none_without_endpoint(self):
        """Should return None when AZURE_OPENAI_ENDPOINT is empty."""
        with patch.dict(FOUNDRY_CONFIG, {"openai_endpoint": "", "openai_api_key": "key"}):
            assert get_instrumented_openai_client() is None

    def test_returns_none_without_api_key(self):
        """Should return None when AZURE_OPENAI_API_KEY is empty."""
        with patch.dict(FOUNDRY_CONFIG, {"openai_endpoint": "https://example.com", "openai_api_key": ""}):
            assert get_instrumented_openai_client() is None

    def test_returns_none_when_openai_missing(self):
        """Should return None when the openai package is not installed."""
        with patch.dict(
            FOUNDRY_CONFIG,
            {"openai_endpoint": "https://example.com", "openai_api_key": "key"},
        ):
            with patch.dict("sys.modules", {"openai": None}):
                assert get_instrumented_openai_client() is None

    def test_returns_client_when_configured(self):
        """Should return a client object when credentials are set."""
        mock_client_cls = MagicMock()
        mock_module = MagicMock()
        mock_module.AsyncAzureOpenAI = mock_client_cls

        with patch.dict(
            FOUNDRY_CONFIG,
            {"openai_endpoint": "https://example.com", "openai_api_key": "key"},
        ):
            with patch.dict("sys.modules", {"openai": mock_module}):
                result = get_instrumented_openai_client()
                assert result is not None


# ── TestTracing ───────────────────────────────────────────────────────

class TestTracing:
    """Tests for OpenTelemetry tracing setup."""

    def setup_method(self):
        reset_tracing()

    def teardown_method(self):
        reset_tracing()

    def test_returns_false_without_connection_string(self):
        """Should return False when APPLICATIONINSIGHTS_CONNECTION_STRING is empty."""
        with patch.dict(FOUNDRY_CONFIG, {"appinsights_connection_string": ""}):
            assert setup_tracing() is False

    def test_returns_false_when_otel_missing(self):
        """Should return False when opentelemetry-sdk is not installed."""
        with patch.dict(
            FOUNDRY_CONFIG,
            {"appinsights_connection_string": "InstrumentationKey=test"},
        ):
            with patch.dict("sys.modules", {"opentelemetry": None}):
                assert setup_tracing() is False

    def test_idempotent_after_success(self):
        """Second call should return True immediately if already initialised."""
        # Simulate a successful init by setting the flag directly
        import foundry.deployment_config as fdc
        fdc._tracing_initialised = True
        assert setup_tracing() is True
        fdc._tracing_initialised = False  # cleanup


# ── TestTraceAgentCall ────────────────────────────────────────────────

class TestTraceAgentCall:
    """Tests for the trace_agent_call async context manager."""

    def setup_method(self):
        reset_tracing()

    def teardown_method(self):
        reset_tracing()

    def test_no_op_when_tracing_disabled(self):
        """Should execute the block with no overhead when tracing is off."""
        executed = False

        async def _inner():
            nonlocal executed
            async with trace_agent_call("Timing Agent"):
                executed = True

        asyncio.run(_inner())
        assert executed is True

    def test_exception_propagates(self):
        """Exceptions inside the context manager should propagate."""
        async def _inner():
            async with trace_agent_call("Timing Agent"):
                raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            asyncio.run(_inner())

    def test_yields_none_when_tracing_disabled(self):
        """Should yield None (not the span) when tracing is not initialised."""
        received: list = []

        async def _inner():
            async with trace_agent_call("Timing Agent") as span:
                received.append(span)

        asyncio.run(_inner())
        assert received == [None]


# ── TestTraceOrchestrate ──────────────────────────────────────────────

class TestTraceOrchestrate:
    """Tests for the trace_orchestrate root-span context manager."""

    def setup_method(self):
        reset_tracing()

    def teardown_method(self):
        reset_tracing()

    def test_yields_none_when_tracing_disabled(self):
        """Should yield None when tracing is not initialised."""
        received: list = []

        async def _inner():
            async with trace_orchestrate(42, "owner/repo") as span:
                received.append(span)

        asyncio.run(_inner())
        assert received == [None]

    def test_block_executes_when_tracing_disabled(self):
        """The wrapped block should always execute."""
        executed = False

        async def _inner():
            nonlocal executed
            async with trace_orchestrate(1, "owner/repo"):
                executed = True

        asyncio.run(_inner())
        assert executed is True

    def test_exception_propagates(self):
        """Exceptions inside the context manager should propagate."""
        async def _inner():
            async with trace_orchestrate(1, "owner/repo"):
                raise RuntimeError("pipeline error")

        with pytest.raises(RuntimeError, match="pipeline error"):
            asyncio.run(_inner())


# ── TestContentSafety ─────────────────────────────────────────────────

class TestContentSafety:
    """Tests for Azure Content Safety integration."""

    def test_returns_safe_without_credentials(self):
        """Should return safe=True when Content Safety is not configured."""
        with patch.dict(
            FOUNDRY_CONFIG,
            {"content_safety_endpoint": "", "content_safety_key": ""},
        ):
            result = asyncio.run(check_content_safety("test text"))
            assert result["safe"] is True
            assert result["blocked_categories"] == []

    def test_returns_safe_when_sdk_missing(self):
        """Should return safe=True when azure-ai-contentsafety is not installed."""
        with patch.dict(
            FOUNDRY_CONFIG,
            {
                "content_safety_endpoint": "https://example.com",
                "content_safety_key": "key",
            },
        ):
            with patch.dict("sys.modules", {"azure.ai.contentsafety": None}):
                result = asyncio.run(check_content_safety("test text"))
                assert result["safe"] is True

    def test_detects_unsafe_content(self):
        """Should flag categories with severity >= 2 as blocked."""
        mock_item = SimpleNamespace(category="Violence", severity=4)
        mock_response = SimpleNamespace(categories_analysis=[mock_item])

        mock_client_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.analyze_text.return_value = mock_response
        mock_client_cls.return_value = mock_client_instance

        mock_options_cls = MagicMock()
        mock_cred_cls = MagicMock()

        mock_cs_module = MagicMock()
        mock_cs_module.ContentSafetyClient = mock_client_cls
        mock_cs_module.models = MagicMock()
        mock_cs_module.models.AnalyzeTextOptions = mock_options_cls

        mock_cs_models_module = MagicMock()
        mock_cs_models_module.AnalyzeTextOptions = mock_options_cls

        mock_core_cred = MagicMock()
        mock_core_cred.AzureKeyCredential = mock_cred_cls

        with patch.dict(
            FOUNDRY_CONFIG,
            {
                "content_safety_endpoint": "https://example.com",
                "content_safety_key": "key",
            },
        ):
            with patch.dict(
                "sys.modules",
                {
                    "azure.ai.contentsafety": mock_cs_module,
                    "azure.ai.contentsafety.models": mock_cs_models_module,
                    "azure.core.credentials": mock_core_cred,
                },
            ):
                result = asyncio.run(check_content_safety("violent content"))
                assert result["safe"] is False
                assert "Violence" in result["blocked_categories"]

    def test_safe_content_passes(self):
        """Should return safe=True when all categories have low severity."""
        mock_item = SimpleNamespace(category="Hate", severity=0)
        mock_response = SimpleNamespace(categories_analysis=[mock_item])

        mock_client_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.analyze_text.return_value = mock_response
        mock_client_cls.return_value = mock_client_instance

        mock_options_cls = MagicMock()
        mock_cred_cls = MagicMock()

        mock_cs_module = MagicMock()
        mock_cs_module.ContentSafetyClient = mock_client_cls

        mock_cs_models_module = MagicMock()
        mock_cs_models_module.AnalyzeTextOptions = mock_options_cls

        mock_core_cred = MagicMock()
        mock_core_cred.AzureKeyCredential = mock_cred_cls

        with patch.dict(
            FOUNDRY_CONFIG,
            {
                "content_safety_endpoint": "https://example.com",
                "content_safety_key": "key",
            },
        ):
            with patch.dict(
                "sys.modules",
                {
                    "azure.ai.contentsafety": mock_cs_module,
                    "azure.ai.contentsafety.models": mock_cs_models_module,
                    "azure.core.credentials": mock_core_cred,
                },
            ):
                result = asyncio.run(check_content_safety("normal text"))
                assert result["safe"] is True
                assert result["blocked_categories"] == []


# ── TestPolicyGuardrails ──────────────────────────────────────────────

class TestPolicyGuardrails:
    """Tests for enterprise policy guardrails."""

    def test_no_escalation_above_threshold(self):
        """Score above threshold should not trigger escalation."""
        verdict = _make_verdict(score=80, decision="greenlight")
        result = apply_policy_guardrails(verdict, {"pr_number": 1, "repo": "org/repo"})
        assert result["escalation_required"] is False
        assert result["escalation_reason"] is None

    def test_escalation_below_threshold(self):
        """Score below 30 should trigger auto-escalation."""
        verdict = _make_verdict(
            score=20,
            decision="blocked",
            playbook="revert steps",
        )
        result = apply_policy_guardrails(verdict, {"pr_number": 2, "repo": "org/repo"})
        assert result["escalation_required"] is True
        assert "20" in result["escalation_reason"]
        assert "30" in result["escalation_reason"]

    def test_escalation_at_boundary(self):
        """Score exactly at threshold should not trigger escalation."""
        verdict = _make_verdict(
            score=30,
            decision="blocked",
            playbook="revert steps",
        )
        result = apply_policy_guardrails(verdict, {"pr_number": 3, "repo": "org/repo"})
        assert result["escalation_required"] is False

    def test_blocked_without_playbook_violates_policy(self):
        """Blocked decision without playbook should flag a policy violation."""
        verdict = _make_verdict(score=50, decision="blocked", playbook=None)
        result = apply_policy_guardrails(verdict)
        assert len(result["policy_violations"]) == 1
        assert "rollback playbook" in result["policy_violations"][0].lower()

    def test_blocked_with_playbook_no_violations(self):
        """Blocked decision with playbook should have no policy violations."""
        verdict = _make_verdict(
            score=50,
            decision="blocked",
            playbook="## Rollback\n1. Revert commit",
        )
        result = apply_policy_guardrails(verdict)
        assert result["policy_violations"] == []

    def test_audit_entry_contains_pr_info(self):
        """Audit entry should contain PR number, repo, score, and decision."""
        verdict = _make_verdict(score=85, decision="greenlight")
        payload = {"pr_number": 42, "repo": "org/prism"}
        result = apply_policy_guardrails(verdict, payload)

        audit = result["audit_entry"]
        assert audit["pr_number"] == 42
        assert audit["repo"] == "org/prism"
        assert audit["confidence_score"] == 85
        assert audit["decision"] == "greenlight"
        assert "timestamp" in audit

    def test_default_pr_payload(self):
        """Should handle None pr_payload gracefully."""
        verdict = _make_verdict(score=90, decision="greenlight")
        result = apply_policy_guardrails(verdict)
        assert result["audit_entry"]["pr_number"] == "N/A"


# ── TestEvaluateQuality ───────────────────────────────────────────────

class TestEvaluateQuality:
    """Tests for the quality evaluation heuristic."""

    def test_perfect_groundedness(self):
        """All findings mentioned in brief → groundedness = 1.0."""
        results = [
            _make_result(name="Timing Agent", findings=["Friday deployment is risky"]),
        ]
        brief = "The Timing Agent flagged that Friday deployment is risky."
        scores = asyncio.run(evaluate_quality(brief, results))
        assert scores["groundedness_score"] == 1.0

    def test_zero_groundedness(self):
        """No findings mentioned → groundedness = 0.0."""
        results = [
            _make_result(name="Timing Agent", findings=["Friday deployment is risky"]),
        ]
        brief = "Everything looks perfect, no issues found."
        scores = asyncio.run(evaluate_quality(brief, results))
        assert scores["groundedness_score"] == 0.0

    def test_perfect_relevance(self):
        """All agent names appear in brief → relevance = 1.0."""
        results = [
            _make_result(name="Timing Agent"),
            _make_result(name="Diff Analyst"),
        ]
        brief = "The Timing Agent and Diff Analyst both flagged issues."
        scores = asyncio.run(evaluate_quality(brief, results))
        assert scores["relevance_score"] == 1.0

    def test_partial_relevance(self):
        """Only some agents mentioned → relevance < 1.0."""
        results = [
            _make_result(name="Timing Agent"),
            _make_result(name="Diff Analyst"),
        ]
        brief = "The Timing Agent flagged Friday deployment risk."
        scores = asyncio.run(evaluate_quality(brief, results))
        assert scores["relevance_score"] == 0.5
        assert "Diff Analyst" in scores["missing_agents"]

    def test_empty_results(self):
        """No agent results → scores should be 0 without errors."""
        scores = asyncio.run(evaluate_quality("Some brief", []))
        assert scores["groundedness_score"] == 0.0
        assert scores["relevance_score"] == 0.0

    def test_evaluation_method_heuristic_by_default(self):
        """Without Foundry client, method should be 'heuristic'."""
        results = [_make_result()]
        scores = asyncio.run(evaluate_quality("brief", results))
        assert scores["evaluation_method"] == "heuristic"


# ── TestConfig ────────────────────────────────────────────────────────

class TestConfig:
    """Tests for the FOUNDRY_CONFIG structure."""

    def test_config_has_required_keys(self):
        """Config dict should contain all expected keys."""
        expected_keys = {
            "project_connection_string",
            "openai_endpoint",
            "openai_deployment",
            "openai_api_key",
            "content_safety_endpoint",
            "content_safety_key",
            "appinsights_connection_string",
            "auto_escalation_threshold",
            "blocked_requires_playbook",
            "tracing_enabled",
        }
        assert expected_keys.issubset(set(FOUNDRY_CONFIG.keys()))

    def test_auto_escalation_threshold_default(self):
        """Default auto-escalation threshold should be 30."""
        assert FOUNDRY_CONFIG["auto_escalation_threshold"] == 30

    def test_blocked_requires_playbook_default(self):
        """blocked_requires_playbook should default to True."""
        assert FOUNDRY_CONFIG["blocked_requires_playbook"] is True
