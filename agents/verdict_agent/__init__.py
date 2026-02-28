"""
PRism Verdict Agent
===================
Ingests the four specialist ``AgentResult`` payloads, computes the
**Deployment Confidence Score**, renders a markdown **Risk Brief**,
and — when the deploy is blocked — generates a **Rollback Playbook**.

Optional LLM enhancement:
    If ``AZURE_OPENAI_ENDPOINT`` and ``AZURE_OPENAI_DEPLOYMENT`` environment
    variables are set, the risk brief and rollback playbook are enriched by
    GPT-4o via Azure OpenAI.  If the variables are absent or the call fails,
    the agent falls back to deterministic template generation.

Public API:
    ``async def run(agent_results, pr_payload) -> VerdictReport``
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agents.shared.data_contract import AgentResult, VerdictReport

logger = logging.getLogger("prism.verdict")

# Re-use the canonical weights from the Orchestrator so there is a
# single source of truth for the scoring formula.
_DEFAULT_WEIGHT = 0.25  # fallback if an agent name is unknown


def _get_weights() -> dict[str, float]:
    """Import AGENT_WEIGHTS from the orchestrator (lazy to avoid cycles)."""
    try:
        from agents.orchestrator import AGENT_WEIGHTS
        return AGENT_WEIGHTS
    except ImportError:
        logger.warning("Could not import AGENT_WEIGHTS — using equal weights.")
        return {}


# ── Scoring ──────────────────────────────────────────────────────────

def _compute_score(results: list[AgentResult], weights: dict[str, float]) -> int:
    """Compute the deployment confidence score.

    Formula:  ``score = 100 - Σ(modifier × weight)``, clamped to [0, 100].
    Unknown agent names fall back to ``_DEFAULT_WEIGHT``.
    """
    weighted_sum = 0.0
    for r in results:
        w = weights.get(r.agent_name, _DEFAULT_WEIGHT)
        weighted_sum += r.risk_score_modifier * w

    score = 100.0 - weighted_sum
    return max(0, min(100, round(score)))


def _decide(score: int, results: list[AgentResult]) -> str:
    """Return ``"greenlight"`` or ``"blocked"`` per PRism rules."""
    has_critical = any(r.status == "critical" for r in results)
    if has_critical or score < 70:
        return "blocked"
    return "greenlight"


# ── Risk Brief (template) ───────────────────────────────────────────

def _build_risk_brief(
    results: list[AgentResult],
    score: int,
    decision: str,
) -> str:
    """Build a deterministic markdown risk brief grouped by agent."""
    lines: list[str] = ["## PRism Risk Brief", ""]

    for r in results:
        lines.append(f"### {r.agent_name} ({r.status} — modifier: {r.risk_score_modifier})")
        if r.findings:
            for f in r.findings:
                lines.append(f"- {f}")
        else:
            lines.append("- No findings.")
        lines.append(f"- **Recommendation:** {r.recommended_action}")
        lines.append("")

    tag = "GREENLIGHT ✅" if decision == "greenlight" else "BLOCKED 🚫"
    lines.append(f"**Deployment Confidence Score: {score} / 100 → {tag}**")
    return "\n".join(lines)


# ── Rollback Playbook (template) ─────────────────────────────────────

def _build_rollback_playbook(
    results: list[AgentResult],
    score: int,
    pr_payload: dict[str, Any],
) -> str:
    """Generate a markdown rollback playbook for blocked deploys."""
    pr_number = pr_payload.get("pr_number", "N/A")
    repo = pr_payload.get("repo", "unknown/repo")

    lines: list[str] = [
        "## Rollback Playbook",
        "",
        f"**PR:** {repo}#{pr_number}",
        f"**Confidence Score:** {score} / 100",
        "",
        "### Immediate Actions",
        "",
        f"1. **Revert the PR:**",
        f"   ```bash",
        f"   git revert --no-edit <merge-commit-sha>",
        f"   git push origin main",
        f"   ```",
        "2. **Verify rollback:** Confirm the revert deploys cleanly via CI.",
        "3. **Notify the team:** Post in the deploy channel that the PR was rolled back.",
        "",
        "### Flagged Issues",
        "",
    ]

    for r in results:
        if r.status in ("warning", "critical"):
            severity = "⚠️ WARNING" if r.status == "warning" else "🔴 CRITICAL"
            lines.append(f"#### {r.agent_name} ({severity})")
            for f in r.findings:
                lines.append(f"- {f}")
            lines.append(f"- **Action:** {r.recommended_action}")
            lines.append("")

    lines.extend([
        "### Before Re-submitting",
        "",
        "- Address every issue listed above.",
        "- Ensure all tests pass locally and in CI.",
        "- Request a fresh PRism analysis after pushing fixes.",
    ])

    return "\n".join(lines)


# ── Optional LLM Enhancement ────────────────────────────────────────

async def _llm_enhance_brief(
    results: list[AgentResult],
    template_brief: str,
) -> str | None:
    """Attempt to produce an LLM-enriched risk brief via Azure OpenAI.

    Returns the enhanced text, or ``None`` if LLM is unavailable / fails.
    """
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")

    if not endpoint or not deployment:
        return None

    try:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-12-01-preview",
        )

        agent_json = "\n\n".join(r.to_json() for r in results)

        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a deployment risk analyst for a CI/CD system called PRism. "
                        "Summarize the following agent analysis results into a concise, "
                        "executive-level risk brief in markdown. Group findings by agent, "
                        "highlight the most critical issues first, and end with a clear "
                        "recommendation (deploy or delay). Keep it under 400 words."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Agent analysis results:\n\n{agent_json}",
                },
            ],
            temperature=0.3,
            max_tokens=800,
        )

        content = response.choices[0].message.content
        return content if content else None

    except Exception as exc:
        logger.warning("LLM enhancement failed — falling back to template: %s", exc)
        return None


async def _llm_enhance_playbook(
    results: list[AgentResult],
    template_playbook: str,
    pr_payload: dict[str, Any],
) -> str | None:
    """Attempt to produce an LLM-enriched rollback playbook.

    Returns the enhanced text, or ``None`` if LLM is unavailable / fails.
    """
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")

    if not endpoint or not deployment:
        return None

    try:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-12-01-preview",
        )

        agent_json = "\n\n".join(r.to_json() for r in results)
        changed_files = pr_payload.get("changed_files", [])

        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a deployment risk analyst. Generate a detailed, "
                        "context-aware rollback playbook in markdown for a blocked PR. "
                        "Include specific git commands, verification steps, and "
                        "remediation guidance based on the flagged issues and changed files. "
                        "Keep it under 500 words."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"PR #{pr_payload.get('pr_number', 'N/A')} "
                        f"in {pr_payload.get('repo', 'unknown/repo')}\n"
                        f"Changed files: {', '.join(changed_files)}\n\n"
                        f"Agent results:\n{agent_json}"
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=1000,
        )

        content = response.choices[0].message.content
        return content if content else None

    except Exception as exc:
        logger.warning("LLM playbook enhancement failed — falling back to template: %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────

async def run(
    agent_results: list[AgentResult],
    pr_payload: dict[str, Any] | None = None,
) -> VerdictReport:
    """Produce the final deployment verdict.

    Args:
        agent_results: List of ``AgentResult`` payloads from the four
                       specialist agents.
        pr_payload:    Raw dict of the ``PRPayload`` (used for context in
                       the rollback playbook).

    Returns:
        A fully populated ``VerdictReport``.
    """
    if pr_payload is None:
        pr_payload = {}

    weights = _get_weights()

    # 1. Score + decision
    score = _compute_score(agent_results, weights)
    decision = _decide(score, agent_results)

    # 2. Risk brief (template → optionally enhance with LLM)
    risk_brief = _build_risk_brief(agent_results, score, decision)

    llm_brief = await _llm_enhance_brief(agent_results, risk_brief)
    if llm_brief:
        risk_brief = llm_brief

    # 3. Rollback playbook (only when blocked)
    rollback_playbook: str | None = None
    if decision == "blocked":
        rollback_playbook = _build_rollback_playbook(agent_results, score, pr_payload)

        llm_playbook = await _llm_enhance_playbook(
            agent_results, rollback_playbook, pr_payload,
        )
        if llm_playbook:
            rollback_playbook = llm_playbook

    # 4. Build and return the verdict
    return VerdictReport(
        confidence_score=score,
        decision=decision,
        risk_brief=risk_brief,
        rollback_playbook=rollback_playbook,
        agent_results=agent_results,
    )
