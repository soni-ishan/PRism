# agents/diff_analyst/diff_agent.py

import json
import os
from typing import Any, List

from agents.shared.data_contract import AgentResult
from .llm_client import call_llm
from .mcp_client import fetch_pr_diff
import re
from typing import Tuple

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----"), "Private key material"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id pattern"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "GitHub token-like string"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI key-like string"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]+['\"]"), "Hardcoded credential assignment"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-\._~\+\/]+=*"), "Bearer token in code"),
]

RETRY_REMOVAL_HINTS = [
    re.compile(r"(?i)\bretry\b"),
    re.compile(r"(?i)\bbackoff\b"),
    re.compile(r"(?i)\btimeout\b"),
    re.compile(r"(?i)\bcircuit\s*breaker\b"),
    re.compile(r"(?i)\bidempotency\b"),
]

ERROR_HANDLING_REMOVAL_HINTS = [
    re.compile(r"(?i)\btry\b"),
    re.compile(r"(?i)\bexcept\b"),
    re.compile(r"(?i)\bcatch\b"),
    re.compile(r"(?i)\bthrow\b"),
    re.compile(r"(?i)\braise\b"),
    re.compile(r"(?i)\bif\s*\(\s*err(or)?\b"),
]

SCHEMA_RISK_HINTS = [
    re.compile(r"(?i)\bDROP\s+(TABLE|COLUMN)\b"),
    re.compile(r"(?i)\bALTER\s+TABLE\b"),
    re.compile(r"(?i)\bALTER\s+COLUMN\b"),
    re.compile(r"(?i)\bRENAME\s+COLUMN\b"),
    re.compile(r"(?i)\bCREATE\s+INDEX\b"),
    re.compile(r"(?i)\bFOREIGN\s+KEY\b"),
]

def heuristic_scan(diff_text: str) -> Tuple[int, str, List[str]]:
    findings = []

    # Secrets
    for rx, label in SECRET_PATTERNS:
        if rx.search(diff_text):
            findings.append(f"Possible hardcoded secret detected ({label}). Do not commit secrets; rotate if real.")
            # secrets are critical
            return 90, "critical", findings

    # Schema risk
    if any(rx.search(diff_text) for rx in SCHEMA_RISK_HINTS):
        findings.append("Schema/migration risk detected (SQL/DDL keywords present). Review for breaking/destructive changes.")

    # Retry/backoff hints (not perfect, but useful)
    if any(rx.search(diff_text) for rx in RETRY_REMOVAL_HINTS):
        # only flag if also see '-' removed lines containing these words
        if re.search(r"^-.*(retry|backoff|timeout|idempotency|circuit)", diff_text, flags=re.I | re.M):
            findings.append("Potential removal of retry/timeout/backoff/idempotency logic detected in removed lines.")

    # Error handling hints
    if re.search(r"^-.*(try|except|catch|raise|throw)", diff_text, flags=re.I | re.M):
        findings.append("Potential removal of error handling detected in removed lines (try/catch/raise/throw).")

    # If we found nothing, pass through
    if not findings:
        return 0, "pass", []

    # Otherwise, warning severity
    return 55, "warning", findings

AGENT_NAME = "Diff Analyst Agent"

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
2) "Checked for: <list the 4 categories>."
"""

def _safe_findings_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]

def _fallback(reason: str) -> AgentResult:
    return AgentResult(
        agent_name=AGENT_NAME,
        risk_score_modifier=60,
        status="warning",
        findings=[reason],
        recommended_action="Review the diff manually and re-run the Diff Analyst Agent after tightening JSON-only output.",
    )

def run(diff_text: str) -> AgentResult:
    if not diff_text or not diff_text.strip():
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=30,
            status="warning",
            findings=["No diff content provided to Diff Analyst Agent."],
            recommended_action="Ensure the orchestrator passes PR diff/patch text into Diff Analyst Agent.",
        )

    # 1) Heuristic scan (fast + deterministic)
    h_risk, h_status, h_findings = heuristic_scan(diff_text)

    # If heuristics found a critical secret, we can short-circuit (optional but recommended)
    if h_status == "critical":
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=h_risk,
            status=h_status,
            findings=h_findings[:8],
            recommended_action="Block merge. Remove secret from code, rotate credentials, and add secret scanning to CI.",
        )

    # 2) LLM scan (deep analysis)
    raw = call_llm(SYSTEM_PROMPT, diff_text)

    try:
        parsed = json.loads(raw)

        risk = int(parsed.get("risk_score_modifier", 50))
        status = str(parsed.get("status", "warning")).strip()
        findings = _safe_findings_list(parsed.get("findings", []))
        rec = str(parsed.get("recommended_action", "Review findings before merge.")).strip()

        # 3) Merge heuristic findings in (prepend so deterministic signals aren't lost)
        merged_findings = (h_findings + findings)[:8]

        # 4) If heuristics had warnings, ensure we don't downgrade to pass incorrectly
        if h_findings and status == "pass":
            status = "warning"
            risk = max(risk, h_risk)
        # If model returns empty feedback but diff exists, force useful output
        if diff_text.strip() and status == "pass" and not merged_findings:
         merged_findings = [
        "No critical anti-patterns detected in the provided diff.",
        "Checked for: hardcoded secrets, removed error handling, removed retries/timeouts/backoff, and schema/migration risks."
        ]
        rec = "Proceed with normal review; no high-risk patterns detected."
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=max(0, min(100, max(risk, h_risk))),
            status=status,
            findings=merged_findings,
            recommended_action=rec,
        )
            
    except Exception:
        
        # Fallback keeps Verdict safe
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=max(60, h_risk),
            status="warning" if h_status != "critical" else "critical",
            findings=(h_findings or ["Model output was not valid JSON; returned heuristic-only fallback."])[:8],
            recommended_action="Review the diff manually; re-run Diff Analyst after enforcing JSON-only output.",
        )


def run_from_pr(pr_number: int) -> AgentResult:
    """
    Convenience wrapper: fetch PR diff via GitHub MCP Server, then analyze it.
    Requires env vars: GITHUB_OWNER, GITHUB_REPO.
    """
    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]

    diff_text = fetch_pr_diff(owner, repo, pr_number)
    print("DIFF_CHARS:", len(diff_text))
    print("DIFF_HEAD:\n", diff_text[:400])

    if not diff_text or not diff_text.strip():
        return AgentResult(
            agent_name=AGENT_NAME,
            risk_score_modifier=40,
            status="warning",
            findings=[f"Fetched empty diff for PR #{pr_number} (owner={owner}, repo={repo})."],
            recommended_action="Verify PR number/repo and ensure MCP server has access to fetch PR file patches.",
        )

    return run(diff_text)