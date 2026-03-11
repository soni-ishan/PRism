"""
PRism Foundry Governance Module
===============================
Integrates the PRism pipeline with **Azure AI Foundry** for production-grade
observability, content safety, policy guardrails, and evaluation tracking.

All features degrade gracefully when environment variables or packages are
missing, so the pipeline continues to function in local / CI environments
without Azure credentials.

Public API:
    ``get_foundry_client()``              — Singleton AIProjectClient factory
    ``get_instrumented_openai_client()``  — Traced AsyncAzureOpenAI client
    ``setup_tracing()``                   — Wire OpenTelemetry → Application Insights
    ``trace_agent_call(agent_name)``      — Async context-manager for per-agent spans
    ``check_content_safety(text)``        — Azure Content Safety on LLM output
    ``apply_policy_guardrails(verdict, pr_payload)``
                                          — Score auto-escalation + audit trail
    ``evaluate_quality(brief, agent_results)``
                                          — Groundedness / relevance tracking
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger("prism.foundry")


# ── Configuration ─────────────────────────────────────────────────────

FOUNDRY_CONFIG: dict[str, Any] = {
    "project_connection_string": os.getenv("AZURE_FOUNDRY_PROJECT_CONNECTION_STRING", ""),
    "openai_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    "openai_deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"),
    "openai_api_key": os.getenv("AZURE_OPENAI_API_KEY", ""),
    "content_safety_endpoint": os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT", ""),
    "content_safety_key": os.getenv("AZURE_CONTENT_SAFETY_KEY", ""),
    "appinsights_connection_string": os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", ""),
    "auto_escalation_threshold": 30,
    "blocked_requires_playbook": True,
    "tracing_enabled": True,
}


# ── Singleton Foundry Client ─────────────────────────────────────────

_foundry_client: Any | None = None
_foundry_client_initialised = False


def get_foundry_client() -> Any | None:
    """Return a singleton ``AIProjectClient`` connected to the Foundry project.

    Returns ``None`` if the connection string is missing or the SDK is not
    installed, allowing the rest of the pipeline to function without Azure.
    """
    global _foundry_client, _foundry_client_initialised

    if _foundry_client_initialised:
        return _foundry_client

    conn_str = FOUNDRY_CONFIG["project_connection_string"]
    if not conn_str:
        logger.info("AZURE_FOUNDRY_PROJECT_CONNECTION_STRING not set — Foundry disabled.")
        _foundry_client_initialised = True
        return None

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        _foundry_client = AIProjectClient(
            credential=DefaultAzureCredential(),
            conn_str=conn_str,
        )
        _foundry_client_initialised = True
        logger.info("AIProjectClient initialised for Foundry project.")
        return _foundry_client
    except ImportError:
        logger.warning(
            "azure-ai-projects or azure-identity not installed — Foundry disabled. "
            "Install with: pip install azure-ai-projects azure-identity"
        )
        _foundry_client_initialised = True
        return None
    except Exception as exc:
        logger.warning("Failed to initialise AIProjectClient: %s", exc)
        _foundry_client_initialised = True
        return None


def reset_foundry_client() -> None:
    """Reset the singleton so the next call to ``get_foundry_client()``
    re-initialises.  Primarily for testing."""
    global _foundry_client, _foundry_client_initialised
    _foundry_client = None
    _foundry_client_initialised = False


# ── Instrumented OpenAI Client ────────────────────────────────────────

def get_instrumented_openai_client() -> Any | None:
    """Return an ``AsyncAzureOpenAI`` client with OpenTelemetry tracing enabled.

    The client is configured using the ``AZURE_OPENAI_*`` environment
    variables.  Returns ``None`` if required variables are missing or the
    ``openai`` package is not installed.
    """
    endpoint = FOUNDRY_CONFIG["openai_endpoint"]
    api_key = FOUNDRY_CONFIG["openai_api_key"]
    deployment = FOUNDRY_CONFIG["openai_deployment"]

    if not endpoint or not api_key:
        logger.info("Azure OpenAI credentials not set — instrumented client unavailable.")
        return None

    try:
        from openai import AsyncAzureOpenAI
    except ModuleNotFoundError:
        logger.warning("openai package not installed — instrumented client unavailable.")
        return None

    try:
        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        )
        logger.debug(
            "Instrumented AsyncAzureOpenAI client created (deployment=%s).",
            deployment,
        )
        return client
    except Exception as exc:
        logger.warning("Failed to create instrumented OpenAI client: %s", exc)
        return None


# ── OpenTelemetry Tracing ─────────────────────────────────────────────

_tracing_initialised = False


def setup_tracing() -> bool:
    """Initialise OpenTelemetry tracing and export to Application Insights.

    Call this once at application startup (e.g. in the FastAPI lifespan).
    Returns ``True`` if tracing was successfully initialised.

    Preferred path (azure-monitor-opentelemetry installed):
        Uses ``configure_azure_monitor()`` which auto-instruments the OpenAI
        SDK so every LLM call emits ``gen_ai.*`` semantic-convention spans
        that appear in the Foundry Tracing dashboard.

    Fallback path (only exporter installed):
        Sets up a bare ``TracerProvider`` + ``BatchSpanProcessor``.  Custom
        ``prism.agent.*`` spans are exported but OpenAI calls are not traced.
    """
    global _tracing_initialised

    if _tracing_initialised:
        return True

    appinsights_conn = FOUNDRY_CONFIG["appinsights_connection_string"]
    if not appinsights_conn:
        logger.info(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set — tracing disabled."
        )
        return False

    # ── Preferred: configure_azure_monitor() + OpenAI auto-instrumentor ──────
    # This is the path that makes LLM calls visible in Foundry Tracing.
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        # OTEL_SERVICE_NAME is read by configure_azure_monitor for the
        # Resource service.name attribute shown in Foundry.
        os.environ.setdefault("OTEL_SERVICE_NAME", "prism-pipeline")
        configure_azure_monitor(connection_string=appinsights_conn)

        # Instrument the openai SDK so chat.completions.create() calls
        # emit gen_ai.* spans that Foundry Tracing can visualise.
        try:
            from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
            OpenAIInstrumentor().instrument()
            logger.info(
                "OpenTelemetry tracing initialised via configure_azure_monitor "
                "→ Application Insights (OpenAI gen_ai.* spans enabled)."
            )
        except ImportError:
            logger.info(
                "OpenTelemetry tracing initialised via configure_azure_monitor "
                "→ Application Insights (install opentelemetry-instrumentation-openai-v2 "
                "for LLM-level gen_ai.* spans)."
            )

        # Only mark as initialised once the full preferred path has completed
        # successfully (both configure_azure_monitor and instrumentation setup).
        _tracing_initialised = True
        return True

    except ImportError:
        logger.debug(
            "azure-monitor-opentelemetry not installed — falling back to manual "
            "TracerProvider setup (no OpenAI auto-instrumentation). "
            "Install with: pip install azure-monitor-opentelemetry "
            "opentelemetry-instrumentation-openai-v2"
        )
    except Exception as exc:
        logger.warning("configure_azure_monitor failed (%s) — falling back.", exc)

    # ── Fallback: bare TracerProvider + BatchSpanProcessor ───────────────────
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "opentelemetry-sdk not installed — tracing disabled. "
            "Install with: pip install opentelemetry-sdk opentelemetry-api"
        )
        return False

    try:
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry-exporter not installed — tracing disabled. "
            "Install with: pip install azure-monitor-opentelemetry-exporter"
        )
        return False

    try:
        resource = Resource.create({"service.name": "prism-pipeline"})
        provider = TracerProvider(resource=resource)
        exporter = AzureMonitorTraceExporter(connection_string=appinsights_conn)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracing_initialised = True
        logger.info("OpenTelemetry tracing initialised → Application Insights (fallback path).")
        return True
    except Exception as exc:
        logger.warning("Failed to initialise tracing: %s", exc)
        return False


def reset_tracing() -> None:
    """Reset tracing state.  For testing only."""
    global _tracing_initialised
    _tracing_initialised = False


def _get_tracer():
    """Return an OpenTelemetry tracer if available, else ``None``."""
    if not _tracing_initialised:
        return None
    try:
        from opentelemetry import trace
        return trace.get_tracer("prism.agents")
    except ImportError:
        return None


@asynccontextmanager
async def trace_agent_call(agent_name: str):
    """Async context manager that wraps a single agent invocation in an OTel span.

    Yields the span so callers can attach result attributes after the agent
    returns::

        async with trace_agent_call("Timing Agent") as span:
            result = await run_timing(...)
            if span is not None:
                span.set_attribute("prism.agent.risk_score_modifier", result.risk_score_modifier)

    If tracing is not initialised, yields ``None`` with no overhead.
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(
        f"prism.agent.{agent_name}",
        attributes={
            "prism.agent.name": agent_name,
            "prism.component": "agent",
        },
    ) as span:
        start = time.monotonic()
        try:
            yield span
        except Exception as exc:
            span.set_attribute("prism.agent.error", str(exc))
            span.set_status(
                _make_span_status("ERROR", str(exc)),
            )
            raise
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            span.set_attribute("prism.agent.latency_ms", elapsed_ms)


@asynccontextmanager
async def trace_orchestrate(pr_number: int, repo: str):
    """Root span for the entire PRism orchestration pipeline.

    Yields the span so the caller can attach ``confidence_score`` and
    ``decision`` after the verdict is produced::

        async with trace_orchestrate(payload.pr_number, payload.repo) as span:
            verdict = await ...
            if span is not None:
                span.set_attribute("prism.confidence_score", verdict.confidence_score)

    Yields ``None`` when tracing is not initialised.
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(
        "prism.orchestrate",
        attributes={
            "prism.pr_number": pr_number,
            "prism.repo": repo,
            "prism.component": "orchestrator",
        },
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_attribute("prism.orchestrate.error", str(exc))
            span.set_status(_make_span_status("ERROR", str(exc)))
            raise


def _make_span_status(status_code: str, description: str = ""):
    """Create a ``StatusCode`` for a span."""
    try:
        from opentelemetry.trace import StatusCode
        code = StatusCode.ERROR if status_code == "ERROR" else StatusCode.OK
        from opentelemetry.trace import Status
        return Status(status_code=code, description=description)
    except ImportError:
        return None


# ── Content Safety ────────────────────────────────────────────────────


async def check_content_safety(text: str) -> dict[str, Any]:
    """Run Azure Content Safety ``analyze_text`` on *text*.

    Returns a dict::

        {
            "safe": True/False,
            "categories": { "Hate": 0, "Violence": 0, ... },
            "blocked_categories": [],
        }

    Returns ``{"safe": True, ...}`` with empty categories if the Content
    Safety service is not configured.
    """
    cs_endpoint = FOUNDRY_CONFIG["content_safety_endpoint"]
    cs_key = FOUNDRY_CONFIG["content_safety_key"]

    if not cs_endpoint or not cs_key:
        logger.debug("Content Safety credentials not set — skipping.")
        return {"safe": True, "categories": {}, "blocked_categories": []}

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions
        from azure.core.credentials import AzureKeyCredential
    except ImportError:
        logger.warning(
            "azure-ai-contentsafety not installed — skipping content check. "
            "Install with: pip install azure-ai-contentsafety"
        )
        return {"safe": True, "categories": {}, "blocked_categories": []}

    try:
        import asyncio

        client = ContentSafetyClient(
            endpoint=cs_endpoint,
            credential=AzureKeyCredential(cs_key),
        )

        request = AnalyzeTextOptions(text=text)
        # Offload synchronous SDK call to a thread so we don't block
        # the event loop (this coroutine is called from FastAPI).
        response = await asyncio.to_thread(client.analyze_text, request)

        categories: dict[str, int] = {}
        blocked: list[str] = []

        if response.categories_analysis:
            for item in response.categories_analysis:
                cat_name = item.category if hasattr(item, "category") else str(item)
                severity = item.severity if hasattr(item, "severity") else 0
                categories[cat_name] = severity
                if severity >= 2:  # Threshold: severity 2+ is blocked
                    blocked.append(cat_name)

        return {
            "safe": len(blocked) == 0,
            "categories": categories,
            "blocked_categories": blocked,
        }

    except Exception as exc:
        logger.warning("Content Safety call failed — failing closed: %s", exc)
        return {"safe": False, "categories": {}, "blocked_categories": [], "error": str(exc)}


# ── Policy Guardrails ─────────────────────────────────────────────────


def apply_policy_guardrails(
    verdict: Any,
    pr_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply enterprise policy guardrails to a ``VerdictReport``.

    Rules:
    1. If ``confidence_score < auto_escalation_threshold`` (default 30) →
       flag for mandatory senior review.
    2. If ``decision == "blocked"`` and no ``rollback_playbook`` →
       flag policy violation.
    3. Log every decision to the audit trail.

    Returns a guardrails result dict::

        {
            "escalation_required": bool,
            "escalation_reason": str | None,
            "policy_violations": list[str],
            "audit_entry": dict,
        }
    """
    if pr_payload is None:
        pr_payload = {}

    score = getattr(verdict, "confidence_score", 0)
    decision = getattr(verdict, "decision", "blocked")
    playbook = getattr(verdict, "rollback_playbook", None)

    threshold = FOUNDRY_CONFIG["auto_escalation_threshold"]

    escalation_required = score < threshold
    escalation_reason = None
    if escalation_required:
        escalation_reason = (
            f"Confidence score {score} is below auto-escalation threshold {threshold}. "
            "Senior engineer review required before re-submission."
        )

    policy_violations: list[str] = []
    if (
        FOUNDRY_CONFIG["blocked_requires_playbook"]
        and decision == "blocked"
        and not playbook
    ):
        policy_violations.append(
            "Policy violation: blocked decision must include a rollback playbook."
        )

    audit_entry = {
        "pr_number": pr_payload.get("pr_number", "N/A"),
        "repo": pr_payload.get("repo", "unknown/repo"),
        "confidence_score": score,
        "decision": decision,
        "escalation_required": escalation_required,
        "policy_violations": policy_violations,
        "timestamp": _utc_now_iso(),
    }

    logger.info(
        "Guardrails applied for %s PR #%s: score=%d decision=%s escalation=%s violations=%d",
        audit_entry["repo"],
        audit_entry["pr_number"],
        score,
        decision,
        escalation_required,
        len(policy_violations),
    )

    # Persist audit entry to Foundry if available
    _log_audit_entry(audit_entry)

    return {
        "escalation_required": escalation_required,
        "escalation_reason": escalation_reason,
        "policy_violations": policy_violations,
        "audit_entry": audit_entry,
    }


def _log_audit_entry(entry: dict[str, Any]) -> None:
    """Persist an audit entry.  Currently logs; could write to Foundry."""
    logger.info("AUDIT: %s", entry)

    tracer = _get_tracer()
    if tracer is not None:
        with tracer.start_as_current_span(
            "prism.audit",
            attributes={
                "prism.audit.pr_number": str(entry.get("pr_number", "")),
                "prism.audit.repo": str(entry.get("repo", "")),
                "prism.audit.score": entry.get("confidence_score", 0),
                "prism.audit.decision": str(entry.get("decision", "")),
            },
        ):
            pass  # Span is auto-exported


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Evaluation (Groundedness / Relevance) ─────────────────────────────


async def evaluate_quality(
    brief: str,
    agent_results: list[Any],
) -> dict[str, Any]:
    """Evaluate the quality of an LLM-generated risk brief.

    Tracks two metrics:
    - **Groundedness** — are claims in the brief supported by agent findings?
    - **Relevance** — does the brief address all flagged issues?

    Uses a lightweight heuristic evaluation.  Can be extended with
    Azure AI Foundry evaluation APIs when available.

    Returns::

        {
            "groundedness_score": float (0-1),
            "relevance_score": float (0-1),
            "missing_agents": list[str],
            "evaluation_method": "heuristic" | "foundry",
        }
    """
    from agents.shared.data_contract import AgentResult

    # Collect all agent names and findings
    agent_names: list[str] = []
    all_findings: list[str] = []
    for r in agent_results:
        if isinstance(r, AgentResult):
            agent_names.append(r.agent_name)
            all_findings.extend(r.findings)
        elif isinstance(r, dict):
            agent_names.append(r.get("agent_name", "Unknown"))
            all_findings.extend(r.get("findings", []))

    brief_lower = brief.lower()

    # Groundedness: what fraction of findings are mentioned in the brief?
    grounded_count = 0
    for finding in all_findings:
        # Check if key words from the finding appear in the brief
        words = [w for w in finding.lower().split() if len(w) > 4]
        if words and any(w in brief_lower for w in words):
            grounded_count += 1

    groundedness = grounded_count / max(len(all_findings), 1)

    # Relevance: what fraction of agents are represented in the brief?
    mentioned_agents = [name for name in agent_names if name.lower() in brief_lower]
    relevance = len(mentioned_agents) / max(len(agent_names), 1)

    missing = [name for name in agent_names if name.lower() not in brief_lower]

    # Attempt Foundry evaluation API if client available.
    # Currently, only heuristic evaluation is implemented; we keep
    # evaluation_method="heuristic" until a real Foundry API call succeeds.
    evaluation_method = "heuristic"
    client = get_foundry_client()
    if client is not None:
        logger.debug(
            "Foundry client available, but heuristic evaluation is still in use; "
            "Foundry evaluation API not yet integrated."
        )

    result = {
        "groundedness_score": round(groundedness, 3),
        "relevance_score": round(relevance, 3),
        "missing_agents": missing,
        "evaluation_method": evaluation_method,
    }

    logger.info(
        "Quality evaluation: groundedness=%.2f relevance=%.2f method=%s",
        groundedness,
        relevance,
        evaluation_method,
    )

    return result
