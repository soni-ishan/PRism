"""
PRism Orchestrator
==================
Central dispatcher that receives a PR event, fires all four specialist agents
concurrently, validates their results, and passes them to the Verdict Agent.

Includes:
  - ``PRPayload`` model for structured PR input.
  - ``orchestrate()`` — the main parallel dispatch pipeline.
  - ``create_kernel()`` — optional Semantic Kernel integration for
    Microsoft Agent Framework branding.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agents.shared.data_contract import AgentResult, VerdictReport

logger = logging.getLogger("prism.orchestrator")


def _parse_iso_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, normalizing trailing 'Z' to '+00:00'."""
    if ts.endswith(("Z", "z")):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


# ── Agent weight configuration (used by Verdict Agent, exposed here
#    so the Orchestrator can attach metadata) ─────────────────────────

AGENT_WEIGHTS: dict[str, float] = {
    "Diff Analyst": 0.30,
    "History Agent": 0.25,
    "Coverage Agent": 0.25,
    "Timing Agent": 0.20,
}

# Names used for error-fallback identification
_AGENT_NAMES = ["Diff Analyst", "History Agent", "Coverage Agent", "Timing Agent"]


# ── PR Payload Model ─────────────────────────────────────────────────

class PRPayload(BaseModel):
    """Structured representation of a Pull Request event.

    The webhook handler (``server.py``) parses the raw GitHub webhook
    and fetches additional data (diff, changed files) before constructing
    this object.
    """

    pr_number: int = Field(..., description="Pull request number")
    repo: str = Field(..., description="Full repository name (owner/repo)")
    changed_files: list[str] = Field(
        default_factory=list,
        description="List of file paths changed in the PR",
    )
    diff: str = Field(default="", description="Unified diff of the PR")
    timestamp: datetime | None = Field(
        default=None,
        description="Deployment / PR timestamp (ISO-8601). Defaults to now.",
    )


# ── Fallback payload for crashed agents ──────────────────────────────

def _make_fallback(agent_name: str, error: Exception) -> AgentResult:
    """Create a conservative fallback payload when an agent crashes."""
    logger.warning("Agent '%s' failed: %s", agent_name, error)
    return AgentResult(
        agent_name=agent_name,
        risk_score_modifier=50,
        status="warning",
        findings=[f"Agent failed with error: {error!s}"],
        recommended_action="Agent encountered an error; manual review recommended.",
    )


# ── Core Orchestration ───────────────────────────────────────────────

async def _import_and_run_agents(payload: PRPayload) -> list[AgentResult]:
    """Fire all four specialist agents concurrently.

    Returns exactly four ``AgentResult`` objects — one per agent.
    If an agent raises an exception, a fallback payload is substituted.
    """
    # Lazy imports so the Orchestrator doesn't crash if a teammate's
    # module has a transient import-time error.
    async def _run_timing() -> AgentResult:
        from agents.timing_agent import run as run_timing
        return await run_timing(deploy_timestamp=payload.timestamp)

    async def _run_diff() -> AgentResult:
        from agents.diff_analyst import run as run_diff
        return await run_diff(diff=payload.diff, changed_files=payload.changed_files)

    async def _run_history() -> AgentResult:
        from agents.history_agent import run as run_history
        return await run_history(changed_files=payload.changed_files)

    async def _run_coverage() -> AgentResult:
        from agents.coverage_agent import run as run_coverage
        return await run_coverage(pr_number=payload.pr_number, repo=payload.repo)

    agent_coros = [
        _run_diff(),
        _run_history(),
        _run_coverage(),
        _run_timing(),
    ]

    raw_results = await asyncio.gather(*agent_coros, return_exceptions=True)

    validated: list[AgentResult] = []
    for idx, result in enumerate(raw_results):
        name = _AGENT_NAMES[idx]
        if isinstance(result, BaseException):
            validated.append(_make_fallback(name, result))
        elif isinstance(result, AgentResult):
            validated.append(result)
        else:
            # Unexpected return type — treat as error
            validated.append(
                _make_fallback(name, TypeError(f"Expected AgentResult, got {type(result).__name__}"))
            )

    return validated


async def orchestrate(pr_payload: dict[str, Any] | PRPayload) -> VerdictReport:
    """Full PRism pipeline: dispatch agents → collect → verdict.

    Args:
        pr_payload: Either a raw dict (from the webhook) or a ``PRPayload`` instance.

    Returns:
        A ``VerdictReport`` with the deployment decision.
    """
    # Normalise input
    if isinstance(pr_payload, dict):
        payload = PRPayload.model_validate(pr_payload)
    else:
        payload = pr_payload

    logger.info(
        "Orchestrating PRism analysis for %s PR #%d (%d files changed)",
        payload.repo,
        payload.pr_number,
        len(payload.changed_files),
    )

    # 1. Fire all agents concurrently
    agent_results = await _import_and_run_agents(payload)

    # 2. Pass to Verdict Agent
    try:
        from agents.verdict_agent import run as run_verdict
    except (ImportError, AttributeError):
        logger.warning("Verdict Agent unavailable — returning blocked fallback.")
        return VerdictReport(
            confidence_score=0,
            decision="blocked",
            risk_brief="Verdict Agent is not yet implemented.",
            agent_results=agent_results,
        )

    try:
        verdict = await run_verdict(
            agent_results=agent_results,
            pr_payload=payload.model_dump(),
        )
    except Exception as exc:
        logger.exception("Verdict Agent failed: %s", exc)
        return VerdictReport(
            confidence_score=0,
            decision="blocked",
            risk_brief=f"Verdict Agent crashed: {exc}",
            agent_results=agent_results,
        )

    logger.info(
        "PRism verdict for %s PR #%d: score=%d decision=%s",
        payload.repo,
        payload.pr_number,
        verdict.confidence_score,
        verdict.decision,
    )

    return verdict


# ── Semantic Kernel Integration ──────────────────────────────────────
# Wraps each agent as a Semantic Kernel native plugin so the system
# can be demoed under the "Microsoft Agent Framework" branding.

def create_kernel():
    """Create a Semantic Kernel ``Kernel`` with all PRism agents
    registered as native plugins.

    Returns:
        A configured ``semantic_kernel.Kernel`` instance.

    Usage::

        kernel = create_kernel()
        # The kernel can now be used with SK planners, chat completions, etc.
    """
    try:
        from semantic_kernel import Kernel
        from semantic_kernel.functions import kernel_function
    except ImportError:
        logger.warning(
            "semantic-kernel not installed — Semantic Kernel integration disabled. "
            "Install with: pip install semantic-kernel"
        )
        return None

    kernel = Kernel()

    # ── Timing Plugin ────────────────────────────────────────────────

    class TimingPlugin:
        """Semantic Kernel plugin wrapping the PRism Timing Agent."""

        @kernel_function(
            name="analyze_deploy_timing",
            description=(
                "Evaluate deployment timing risk based on day-of-week, "
                "time-of-day, holiday proximity, and release proximity."
            ),
        )
        async def analyze(self, timestamp: str = "") -> str:
            from agents.timing_agent import run as run_timing

            ts = _parse_iso_timestamp(timestamp) if timestamp else None
            result = await run_timing(deploy_timestamp=ts)
            return result.to_json()

    # ── Diff Analyst Plugin ──────────────────────────────────────────

    class DiffAnalystPlugin:
        """Semantic Kernel plugin wrapping the PRism Diff Analyst Agent."""

        @kernel_function(
            name="analyze_pr_diff",
            description=(
                "Scan a PR diff for dangerous patterns — removed retries, "
                "missing error handling, schema changes, hardcoded secrets."
            ),
        )
        async def analyze(self, diff: str = "", changed_files: str = "") -> str:
            from agents.diff_analyst import run as run_diff

            files = [f.strip() for f in changed_files.split(",") if f.strip()]
            result = await run_diff(diff=diff, changed_files=files)
            return result.to_json()

    # ── History Plugin ───────────────────────────────────────────────

    class HistoryPlugin:
        """Semantic Kernel plugin wrapping the PRism History Agent."""

        @kernel_function(
            name="correlate_incident_history",
            description=(
                "Correlate changed files with past production incidents "
                "using Azure AI Search."
            ),
        )
        async def analyze(self, changed_files: str = "") -> str:
            from agents.history_agent import run as run_history

            files = [f.strip() for f in changed_files.split(",") if f.strip()]
            result = await run_history(changed_files=files)
            return result.to_json()

    # ── Coverage Plugin ──────────────────────────────────────────────

    class CoveragePlugin:
        """Semantic Kernel plugin wrapping the PRism Coverage Agent."""

        @kernel_function(
            name="check_test_coverage",
            description=(
                "Detect test coverage regression and trigger Copilot "
                "to auto-generate missing tests."
            ),
        )
        async def analyze(self, pr_number: str = "0", repo: str = "") -> str:
            from agents.coverage_agent import run as run_coverage

            result = await run_coverage(pr_number=int(pr_number), repo=repo)
            return result.to_json()

    # ── Orchestrator Plugin ──────────────────────────────────────────

    class OrchestratorPlugin:
        """Semantic Kernel plugin for the full PRism pipeline."""

        @kernel_function(
            name="run_prism_analysis",
            description=(
                "Run the full PRism deployment risk analysis pipeline. "
                "Fires all four specialist agents in parallel and returns "
                "a VerdictReport with the Deployment Confidence Score."
            ),
        )
        async def analyze(
            self,
            pr_number: str = "0",
            repo: str = "",
            diff: str = "",
            changed_files: str = "",
            timestamp: str = "",
        ) -> str:
            files = [f.strip() for f in changed_files.split(",") if f.strip()]
            payload = PRPayload(
                pr_number=int(pr_number),
                repo=repo,
                diff=diff,
                changed_files=files,
                timestamp=_parse_iso_timestamp(timestamp) if timestamp else None,
            )
            verdict = await orchestrate(payload)
            return verdict.to_json()

    # Register all plugins
    kernel.add_plugin(TimingPlugin(), plugin_name="TimingAgent")
    kernel.add_plugin(DiffAnalystPlugin(), plugin_name="DiffAnalyst")
    kernel.add_plugin(HistoryPlugin(), plugin_name="HistoryAgent")
    kernel.add_plugin(CoveragePlugin(), plugin_name="CoverageAgent")
    kernel.add_plugin(OrchestratorPlugin(), plugin_name="PRismOrchestrator")

    logger.info("Semantic Kernel initialised with 5 PRism plugins")
    return kernel
