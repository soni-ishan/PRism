"""
PRism – Live Foundry Observable E2E Tests
==========================================
These tests make REAL Azure OpenAI calls and emit actual OpenTelemetry spans
to Application Insights so they appear on the Foundry Tracing dashboard.

Why the regular integration tests produce no traces
----------------------------------------------------
The standard suite patches ``_llm_enhance_brief`` and ``_llm_enhance_playbook``
and clears the OpenAI env vars so everything resolves in < 1 s via deterministic
heuristics.  That means the real OpenAI SDK is never invoked, no spans are
emitted, and Foundry sees nothing.

What these tests do differently
--------------------------------
1. ``setup_tracing()`` is called in a session-scoped fixture so the
   OpenTelemetry → Application Insights exporter is active for every test.
2. LLM patches are intentionally absent — the Verdict Agent uses the real
   ``AsyncAzureOpenAI`` client backed by your deployment.
3. A ``flush_telemetry()`` helper force-flushes the BatchSpanProcessor after
   each test so spans are exported before the process exits.
4. Every agent call is wrapped in ``trace_agent_call()`` inside the regular
   orchestrator code, so per-agent latency spans appear under each trace.

Required environment variables (all must be set)
-------------------------------------------------
  APPLICATIONINSIGHTS_CONNECTION_STRING — App Insights / Foundry connection string
  AZURE_OPENAI_ENDPOINT                 — e.g. https://<name>.openai.azure.com/
  AZURE_OPENAI_API_KEY                  — your API key
  AZURE_OPENAI_DEPLOYMENT               — e.g. gpt-4o-mini

Optional (for History Agent live calls)
-----------------------------------------
  AZURE_SEARCH_ENDPOINT + AZURE_SEARCH_KEY + AZURE_TENANT_ID +
  AZURE_CLIENT_ID + AZURE_CLIENT_SECRET

Run these tests with
----------------------
  pytest -m foundry_required -v
  # or include them in a full live run:
  pytest -m "foundry_required or azure_required" -v
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import PRPayload, orchestrate
from agents.shared.data_contract import AgentResult, VerdictReport


# ── Env-var guards ────────────────────────────────────────────────────

_FOUNDRY_VARS = (
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
)


def _foundry_creds_available() -> bool:
    return all(os.getenv(v) for v in _FOUNDRY_VARS)


def _history_creds_available() -> bool:
    return bool(os.getenv("AZURE_SEARCH_ENDPOINT") and os.getenv("AZURE_SEARCH_KEY"))


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=False)
def live_tracing():
    """
    Initialize real OpenTelemetry → Application Insights tracing for the
    entire test session.  Must be requested by tests that want live traces.
    """
    if not _foundry_creds_available():
        pytest.skip(
            "foundry_required: set APPLICATIONINSIGHTS_CONNECTION_STRING, "
            "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT"
        )

    # Re-read env vars into FOUNDRY_CONFIG in case they were set after import
    from foundry.deployment_config import FOUNDRY_CONFIG, reset_tracing
    FOUNDRY_CONFIG["appinsights_connection_string"] = os.environ[
        "APPLICATIONINSIGHTS_CONNECTION_STRING"
    ]
    FOUNDRY_CONFIG["openai_endpoint"] = os.environ["AZURE_OPENAI_ENDPOINT"]
    FOUNDRY_CONFIG["openai_api_key"] = os.environ["AZURE_OPENAI_API_KEY"]
    FOUNDRY_CONFIG["openai_deployment"] = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    reset_tracing()
    from foundry.deployment_config import setup_tracing
    ok = setup_tracing()
    assert ok, (
        "setup_tracing() returned False even though credentials are present. "
        "Check that opentelemetry-sdk and azure-monitor-opentelemetry-exporter "
        "are installed."
    )
    yield
    flush_telemetry()


@pytest.fixture()
def azure_search_stub():
    """Stub History Agent's Azure Search so it returns zero incidents.
    Use this when AZURE_SEARCH_* credentials are not configured.
    """
    mock_mcp = MagicMock()
    mock_mcp.query_incidents_by_files_search.return_value = []
    with patch("agents.history_agent.agent.AzureMCPServer", return_value=mock_mcp):
        yield mock_mcp


@pytest.fixture()
def azure_search_live():
    """Use the real AzureMCPServer.  Requires AZURE_SEARCH_* env vars."""
    if not _history_creds_available():
        pytest.skip(
            "azure_required: set AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY "
            "to run live History Agent tests."
        )
    yield  # no patch — real AzureMCPServer is used


# ── Helpers ───────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def flush_telemetry() -> None:
    """Force-flush the BatchSpanProcessor so spans are exported before the
    process exits (important in short-lived test runs)."""
    try:
        from opentelemetry import trace as otel_trace
        provider = otel_trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=10_000)
    except Exception:
        pass  # best-effort


# ── Live Foundry-observable tests ─────────────────────────────────────

@pytest.mark.foundry_required
class TestLiveFoundryTracing:
    """
    Full pipeline runs that emit real traces to Foundry.

    Each test method calls orchestrate() without any LLM mocks.
    The Verdict Agent will call Azure OpenAI using the instrumented client,
    and a ``prism.agent.*`` span is emitted for every agent via trace_agent_call().

    After these tests run you should see spans in:
        Azure AI Foundry → prism-project → Tracing
    """

    def test_safe_pr_emits_greenlight_trace(self, live_tracing, azure_search_stub):
        """
        Safe diff on a Tuesday morning.
        Expected trace:  4 agent spans + 1 verdict LLM call → greenlight.
        """
        payload = PRPayload(
            pr_number=1001,
            repo="acme/backend",
            changed_files=["utils/logger.py"],
            diff="+ logger.setLevel(logging.DEBUG)\n+ version = '3.1.0'",
            timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),  # Tuesday
        )

        t0 = time.monotonic()
        verdict = _run(orchestrate(payload))
        elapsed = time.monotonic() - t0

        # If LLM was actually called this will be > 1s
        print(f"\n  ⏱  orchestrate() elapsed: {elapsed:.2f}s  (>1s confirms real LLM call)")

        assert isinstance(verdict, VerdictReport)
        assert verdict.decision == "greenlight"
        assert len(verdict.agent_results) == 4

        flush_telemetry()

    def test_risky_pr_emits_blocked_trace(self, live_tracing, azure_search_stub):
        """
        PR with a hardcoded secret.
        Expected trace: Diff Analyst critical span → blocked verdict with playbook.
        """
        payload = PRPayload(
            pr_number=1002,
            repo="acme/backend",
            changed_files=["config.py"],
            diff='+ STRIPE_SECRET = "sk-1234567890abcdef1234567890abcdef"',
            timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        )

        t0 = time.monotonic()
        verdict = _run(orchestrate(payload))
        elapsed = time.monotonic() - t0

        print(f"\n  ⏱  orchestrate() elapsed: {elapsed:.2f}s")

        assert verdict.decision == "blocked"
        assert verdict.rollback_playbook is not None
        assert "git revert" in verdict.rollback_playbook

        flush_telemetry()

    def test_friday_deploy_emits_timing_critical_trace(self, live_tracing, azure_search_stub):
        """
        Friday 17:15 deployment.
        Expected trace: Timing Agent critical span → blocked verdict.
        """
        payload = PRPayload(
            pr_number=1003,
            repo="acme/backend",
            changed_files=["api/handler.py"],
            diff="+ minor_fix = True",
            timestamp=datetime(2026, 3, 13, 17, 15, tzinfo=timezone.utc),  # Friday
        )

        t0 = time.monotonic()
        verdict = _run(orchestrate(payload))
        elapsed = time.monotonic() - t0

        print(f"\n  ⏱  orchestrate() elapsed: {elapsed:.2f}s")

        timing_result = next(
            (r for r in verdict.agent_results if r.agent_name == "Timing Agent"), None
        )
        assert timing_result is not None
        assert timing_result.status == "critical"
        assert verdict.decision == "blocked"

        flush_telemetry()

    def test_multi_agent_span_attributes(self, live_tracing, azure_search_stub):
        """
        Confirms all four agent spans are created and the verdict is valid.
        Inspect span attributes in Foundry under ``prism.agent.*``.
        """
        payload = PRPayload(
            pr_number=1004,
            repo="acme/backend",
            changed_files=["services/payment.py", "db/models.py"],
            diff=(
                "- retry_count = 3\n"
                "+ pass\n"
                "- try:\n"
                "-     process()\n"
                "- except Exception:\n"
                "-     log_error()\n"
                "+ process()\n"
            ),
            timestamp=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
        )

        verdict = _run(orchestrate(payload))

        names = {r.agent_name for r in verdict.agent_results}
        assert names == {"Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"}
        assert verdict.confidence_score is not None
        assert isinstance(verdict.risk_brief, str)
        assert len(verdict.risk_brief) > 0

        flush_telemetry()


@pytest.mark.foundry_required
class TestLiveFoundryWithRealHistory:
    """
    Full pipeline tests that use the live Azure AI Search index.
    Requires both AZURE_SEARCH_* and AZURE_OPENAI_* credentials.
    """

    def test_payment_file_gets_history_risk(self, live_tracing, azure_search_live):
        """
        payment_service.py should have historical incidents in the Azure index.
        Expect:  History Agent warning/critical + real LLM brief generated.
        """
        payload = PRPayload(
            pr_number=2001,
            repo="acme/backend",
            changed_files=["payment_service.py"],
            diff="+ amount = amount * 100  # convert to cents",
            timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        )

        t0 = time.monotonic()
        verdict = _run(orchestrate(payload))
        elapsed = time.monotonic() - t0

        print(f"\n  ⏱  elapsed: {elapsed:.2f}s")

        history_result = next(
            (r for r in verdict.agent_results if r.agent_name == "History Agent"), None
        )
        assert history_result is not None
        assert history_result.risk_score_modifier > 0

        flush_telemetry()

    def test_unknown_file_passes_all_agents(self, live_tracing, azure_search_live):
        """New file with no history + safe diff + weekday morning → greenlight."""
        payload = PRPayload(
            pr_number=2002,
            repo="acme/backend",
            changed_files=["brand_new_module_xyz.py"],
            diff="+ def hello(): return 'world'",
            timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        )

        verdict = _run(orchestrate(payload))

        assert verdict.decision == "greenlight"
        history_result = next(
            (r for r in verdict.agent_results if r.agent_name == "History Agent"), None
        )
        assert history_result is not None
        assert history_result.status == "pass"

        flush_telemetry()


@pytest.mark.foundry_required
class TestLiveFoundryPolicy:
    """
    Verifies that Foundry policy guardrails produce audit entries that are
    observable alongside the traces in the Foundry dashboard.
    """

    def test_greenlight_audit_entry_emitted(self, live_tracing, azure_search_stub):
        from foundry.deployment_config import apply_policy_guardrails

        payload = PRPayload(
            pr_number=3001,
            repo="acme/backend",
            changed_files=["utils.py"],
            diff="+ x = 1",
            timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        )

        verdict = _run(orchestrate(payload))
        guardrails = apply_policy_guardrails(verdict, payload.model_dump())

        assert "audit_entry" in guardrails
        assert guardrails["audit_entry"]["pr_number"] == 3001
        assert guardrails["audit_entry"]["repo"] == "acme/backend"
        assert guardrails["audit_entry"]["decision"] in ("greenlight", "blocked")
        assert "timestamp" in guardrails["audit_entry"]

        flush_telemetry()

    def test_blocked_low_confidence_triggers_escalation(self, live_tracing, azure_search_stub):
        from foundry.deployment_config import apply_policy_guardrails

        # Force a blocked verdict against a very risky diff
        payload = PRPayload(
            pr_number=3002,
            repo="acme/backend",
            changed_files=["config.py"],
            diff='+ SECRET = "sk-1234567890abcdef1234567890abcdef"',
            timestamp=datetime(2026, 3, 13, 17, 0, tzinfo=timezone.utc),  # Friday 17:00
        )

        verdict = _run(orchestrate(payload))

        # If score is very low, apply_policy_guardrails should flag escalation
        very_low_verdict = VerdictReport(
            confidence_score=10,
            decision="blocked",
            risk_brief="Simulated very-low-confidence for escalation test.",
            rollback_playbook="## Rollback\n1. Revert the PR",
            agent_results=verdict.agent_results,
        )
        guardrails = apply_policy_guardrails(very_low_verdict, payload.model_dump())

        assert guardrails["escalation_required"] is True
        assert guardrails["audit_entry"]["confidence_score"] == 10

        flush_telemetry()
