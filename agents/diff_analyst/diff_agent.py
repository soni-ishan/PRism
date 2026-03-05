# agents/diff_analyst/diff_agent.py

import json
import os
import re
from typing import Any, List, Tuple

from agents.shared.data_contract import AgentResult
from .llm_client import call_llm

# NOTE:
# - Orchestrator expects agent_name to match weight map key: "Diff Analyst"
# - Orchestrator expects: async def run(diff: str, changed_files: list[str]) -> AgentResult

AGENT_NAME = "Diff Analyst"

# -----------------------------
# Heuristic patterns (fast scan)
# -----------------------------

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"), "Private key material"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id pattern"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub token-like string"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI key-like string"),
    (
        re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]+['\"]"),
        "Hardcoded credential assignment",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-\._~\+\/]+=*"), "Bearer token in code"),
]

RETRY_REMOVAL_HINTS = [
    re.compile(r"(?i)\bretry\b"),
    re.compile(r"(?i)\bbackoff\b"),
    re.compile(r"(?i)\btimeout\b"),
    re.compile(r"(?i)\bcircuit\s*breaker\b"),
    re.compile(r"(?i)\bidempotency\b"),
]

SCHEMA_RISK_HINTS = [
    re.compile(r"(?i)\bDROP\s+(TABLE|COLUMN)\b"),
    re.compile(r"(?i)\bALTER\s+TABLE\b"),
    re.compile(r"(?i)\bALTER\s+COLUMN\b"),
    re.compile(r"(?i)\bRENAME\s+COLUMN\b"),
    re.compile(r"(?i)\bALTER\s+COLUMN\s+TYPE\b"),
    re.compile(r"(?i)\bCREATE\s+INDEX\b"),
    re.compile(r"(?i)\bFOREIGN\s+KEY\b"),
]


def heuristic_scan(diff_text: str) -> Tuple[int, str, List[str]]:
    """
    Returns: (risk_score_modifier, status, findings)
    status in {"pass", "warning", "critical"}
    """
    findings: List[str] = []

    # A) Secrets => critical, short-circuit
    for rx, label in SECRET_PATTERNS:
        if rx.search(diff_text):
            findings.append(
                f"Possible hardcoded secret detected ({label}). Do not commit secrets; rotate credentials if real."
            )
            return 90, "critical", findings

    # D) Schema / migration risk
    if any(rx.search(diff_text) for rx in SCHEMA_RISK_HINTS):
        findings.append(
            "Schema/migration risk detected (SQL/DDL keywords present). Review for breaking or destructive changes."
        )

    # C) Retry/timeout/backoff removal hints (only if removed lines mention them)
    if any(rx.search(diff_text) for rx in RETRY_REMOVAL_HINTS):
        if re.search(r"^-.*(retry|backoff|timeout|idempotency|circuit)", diff_text, flags=re.I | re.M):
            findings.append("Potential removal of retry/timeout/backoff/idempotency logic detected in removed lines.")

    # B) Error handling removal hints (only if removed lines mention them)
    if re.search(r"^-.*(try|except|catch|raise|throw)", diff_text, flags=re.I | re.M):
        findings.append("Potential removal of error handling detected in removed lines (try/catch/raise/throw).")

    if not findings:
        return 0, "pass", []

    return 55, "warning", findings


# -----------------------------
# LLM persona prompt
# -----------------------------

SYSTEM_PROMPT = f"""
You are {AGENT_NAME}. You are a pre-deployment security diff scanner.

You will be given a Pull Request diff text (unified diff hunks). Your job is to find dangerous anti-patterns and report them.

YOU MUST HUNT FOR THESE 4 CATEGORIES (prioritize in this order):
A) HARDCODED SECRETS
- API keys, tokens, passwords, private keys, connection strings
- Examples: "sk-", "AKIA", "-----BEGIN", "Bearer ", "password=", "apikey=", "connectionstring="
- If you suspect a secret, describe it but DO NOT print the actual secret value.

B) ERROR HANDLING REMOVAL / MISSING ERROR HANDLING
- removed try/except, removed catch, removed error checks
- replaced error handling with empty blocks or "pass"
- removed validation, removed null checks, removed guard clauses

C) RETRIES/TIMEOUTS/BACKOFF REMOVAL
- removed retry loops, removed exponential backoff, removed timeout config
- removed circuit breakers, removed rate limit handling
- removed idempotency keys in payment-like flows

D) SCHEMA / MIGRATION RISK
- SQL migrations: DROP COLUMN/TABLE, ALTER TYPE, constraints removed, indexes removed
- ORM model changes that break backward compatibility
- changes to serialized contracts (JSON fields renamed/removed)

OUTPUT FORMAT RULES:
Return ONLY valid JSON with EXACTLY these keys:
{{
  "risk_score_modifier": <int 0-100>,
  "status": "pass" | "warning" | "critical",
  "findings": ["string", "..."],
  "recommended_action": "string"
}}

SCORING RULES:
- If A (hardcoded secret) found => status="critical", risk_score_modifier >= 85
- If auth/error handling removed in sensitive areas OR destructive schema change => status="critical", risk_score_modifier >= 75
- If B/C/D present but not clearly catastrophic => status="warning", risk_score_modifier 35-74
- If no meaningful risk => status="pass", risk_score_modifier 0-20

FINDINGS RULES:
- findings must be specific and actionable.
- mention file names if visible (e.g., "payment_service.py: removed retry wrapper").
- keep findings concise (max 8 findings).
- no markdown, no extra text, JSON only.

IMPORTANT:
If you return status="pass", you MUST include at least 2 findings:
1) "No critical anti-patterns detected in the provided diff."
2) "Checked for: hardcoded secrets, removed error handling, removed retries/timeouts/backoff, and schema/migration risks."
""".strip()


# -----------------------------
# Helpers
# -----------------------------

def _safe_findings_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]


def _fallback(reason: str, h_risk: int = 60, h_status: str = "warning", h_findings: List[str] | None = None) -> AgentResult:
    findings = (h_findings or [])[:8]
    if not findings:
        findings = [reason]
    return AgentResult(
        agent_name=AGENT_NAME,
        risk_score_modifier=max(0, min(100, h_risk)),
        status=h_status if h_status in {"pass", "warning", "critical"} else "warning",
        findings=findings,
        recommended_action="Review the diff manually; Diff Analyst returned a safe fallback due to parsing/LLM issues.",
    )


def _run_core(diff_text: str, changed_files: List[str]) -> AgentResult:
    if not diff_text or not diff_text.strip():
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=30,
            status="warning",
            findings=["No diff content provided to Diff Analyst."],
            recommended_action="Ensure the orchestrator passes PR diff/patch text into Diff Analyst.",
        )

    # Heuristic scan (deterministic)
    h_risk, h_status, h_findings = heuristic_scan(diff_text)

    # Critical secret -> block immediately
    if h_status == "critical":
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=h_risk,
            status="critical",
            findings=h_findings[:8],
            recommended_action="Block merge. Remove secret from code, rotate credentials, and add secret scanning to CI.",
        )

    # Provide file context to LLM without leaking full diff structure
    context_prefix = ""
    if changed_files:
        context_prefix = "CHANGED FILES:\n" + "\n".join(f"- {p}" for p in changed_files[:200]) + "\n\n"

    raw = call_llm(SYSTEM_PROMPT, context_prefix + diff_text)

    try:
        parsed = json.loads(raw)

        risk = int(parsed.get("risk_score_modifier", 50))
        status = str(parsed.get("status", "warning")).strip()
        findings = _safe_findings_list(parsed.get("findings", []))
        rec = str(parsed.get("recommended_action", "Review findings before merge.")).strip()

        # Normalize
        risk = max(0, min(100, risk))
        if status not in {"pass", "warning", "critical"}:
            status = "warning"

        # Merge heuristic findings first
        merged_findings = (h_findings + findings)[:8]

        # Don't let LLM incorrectly downgrade to pass if heuristics found issues
        if h_findings and status == "pass":
            status = "warning"
            risk = max(risk, h_risk)

        # Enforce explainable pass
        if status == "pass" and not merged_findings:
            merged_findings = [
                "No critical anti-patterns detected in the provided diff.",
                "Checked for: hardcoded secrets, removed error handling, removed retries/timeouts/backoff, and schema/migration risks.",
            ]
            rec = "Proceed with normal review; no high-risk patterns detected."
            risk = min(risk, 20)

        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=max(risk, h_risk),
            status=status,
            findings=merged_findings,
            recommended_action=rec,
        )

    except Exception:
        return _fallback(
            reason="Model output was not valid JSON; returned heuristic-only fallback.",
            h_risk=max(60, h_risk),
            h_status="warning" if h_status != "critical" else "critical",
            h_findings=h_findings or ["Model output was not valid JSON; returned heuristic-only fallback."],
        )


# -----------------------------
# Orchestrator entrypoint
# -----------------------------

async def run(diff: str, changed_files: list[str]) -> AgentResult:
    """
    Orchestrator entrypoint.
    Expected signature: async def run(diff: str, changed_files: list[str]) -> AgentResult
    """
    return _run_core(diff, changed_files)


# -----------------------------
# Local-dev helper (optional)
# -----------------------------

def run_from_pr(pr_number: int) -> AgentResult:
    """
    Local development helper only. Not used by orchestrator.
    Fetches PR diff via MCP and then runs analysis.
    """
    try:
        from .mcp_client import fetch_pr_diff  # local import to avoid import-time issues
    except Exception:
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=60,
            status="warning",
            findings=["MCP client import failed; run_from_pr is unavailable in this environment."],
            recommended_action="Use orchestrator-provided diff, or fix MCP client dependencies for local testing.",
        )

    owner = os.environ.get("GITHUB_OWNER", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not owner or not repo:
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=40,
            status="warning",
            findings=["Missing GITHUB_OWNER/GITHUB_REPO env vars for run_from_pr()."],
            recommended_action="Set GITHUB_OWNER and GITHUB_REPO for local MCP testing, or rely on orchestrator input.",
        )

    diff_text = fetch_pr_diff(owner, repo, pr_number)

    if not diff_text or not diff_text.strip():
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=40,
            status="warning",
            findings=[f"Fetched empty diff for PR #{pr_number} (owner={owner}, repo={repo})."],
            recommended_action="Verify PR number/repo and ensure MCP server has access to fetch PR file patches.",
        )

    # For local testing, changed_files are unknown unless you also fetch them; pass empty list.
    return _run_core(diff_text, [])