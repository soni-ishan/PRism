"""
PRism Webhook Server
====================
FastAPI application that receives GitHub PR webhooks, parses them,
optionally fetches additional data (diff, changed files), and triggers
the Orchestrator pipeline.

Run locally::

    uvicorn agents.orchestrator.server:app --reload --port 8000

Endpoints:
  POST /webhook/pr    — GitHub PR webhook receiver
  GET  /health        — Healthcheck
  POST /analyze       — Manual analysis trigger (accepts PRPayload JSON)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from agents.orchestrator import PRPayload, orchestrate
from agents.shared.data_contract import RepoContext, derive_index_name

# ── Load .env before anything reads os.getenv() ──────────────────────────
load_dotenv()  # repo-root .env

# Also load the platform .env so the orchestrator can reach the platform DB
# and decrypt PATs.  override=False keeps repo-root values when both define
# the same key.
_platform_env = os.path.join(os.path.dirname(__file__), "..", "..", "platform", ".env")
if os.path.isfile(_platform_env):
    load_dotenv(os.path.abspath(_platform_env), override=False)

# Ensure the platform package is importable when the orchestrator
# lives in the same monorepo.  The platform code uses relative imports
# like ``from ..services.db import ...`` so we add *platform/* to
# sys.path rather than the repo root.
_PLATFORM_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "platform")
if os.path.isdir(_PLATFORM_DIR) and _PLATFORM_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_PLATFORM_DIR))

# ── Logging setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
# Silence noisy Azure SDK / telemetry loggers so PRism logs are visible
for _noisy in ("azure", "urllib3", "opentelemetry", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("prism.server")


# ── Lifespan: Foundry tracing ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: initialise tracing on startup."""
    try:
        from foundry.deployment_config import setup_tracing
        setup_tracing()
    except ImportError:
        logger.debug("Foundry module not available — tracing disabled.")
    yield


app = FastAPI(
    title="PRism — Deployment Risk Intelligence",
    description="Agentic AI pre-deployment risk gate",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Freemium Usage Tracker ───────────────────────────────────────────
# In-memory tracker (For a real SaaS, this would be Redis or Postgres)
# Tracks: { "client_uuid": {"count": int, "first_seen": float} }
# Entries older than _USAGE_TTL_SECONDS are evicted to bound memory growth.
USAGE_TRACKER: dict = {}
FREE_TIER_LIMIT = 5  # 5 PR evaluations ~= $1 of Azure OpenAI credits
_USAGE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30-day rolling window

from typing import Optional


def _evict_expired_usage() -> None:
    """Remove tracker entries older than _USAGE_TTL_SECONDS to prevent unbounded growth."""
    cutoff = time.time() - _USAGE_TTL_SECONDS
    expired = [k for k, v in USAGE_TRACKER.items() if v.get("first_seen", 0) < cutoff]
    for k in expired:
        del USAGE_TRACKER[k]


def check_freemium_limit(x_client_id: Optional[str] = Header(None)):
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-ID header")
    _evict_expired_usage()
    entry = USAGE_TRACKER.get(x_client_id)
    if entry is None:
        entry = {"count": 0, "first_seen": time.time()}
    if entry["count"] >= FREE_TIER_LIMIT:
        raise HTTPException(
            status_code=402,
            detail="Free trial exhausted. Please configure your own Enterprise PRism URL in VS Code Settings."
        )
    entry["count"] += 1
    USAGE_TRACKER[x_client_id] = entry
    return x_client_id

# ── Registration lookup ──────────────────────────────────────────────


async def _lookup_repo_context(full_repo: str) -> RepoContext | None:
    """Look up a platform registration by *owner/repo* and return a
    ``RepoContext`` with decrypted credentials.

    Returns ``None`` when:
    - The platform DB is not reachable / not configured.
    - No active registration exists for *full_repo*.
    """
    try:
        from server.services.db import async_session, RegistrationRow
        from server.services import auth_service
    except Exception:
        logger.debug("Platform DB not available — falling back to env-var mode.")
        return None

    parts = full_repo.split("/", 1)
    if len(parts) != 2:
        return None
    owner, repo = parts

    try:
        async with async_session() as session:
            result = await session.execute(
                select(RegistrationRow)
                .where(RegistrationRow.owner == owner)
                .where(RegistrationRow.repo == repo)
                .where(RegistrationRow.status == "active")
                .order_by(RegistrationRow.created_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is None:
                logger.info("No active registration for %s", full_repo)
                return None

            gh_token: str | None = None
            try:
                gh_token = auth_service.decrypt_pat(row.gh_pat_encrypted)
            except Exception as exc:
                logger.warning("Failed to decrypt PAT for %s: %s", full_repo, exc)

            # Only populate azure_search_index if the workspace is actually
            # linked.  Without it the History Agent will report "no deployment
            # connection" rather than querying a potentially empty index.
            has_azure = bool(row.azure_workspace_id or row.azure_customer_id)

            return RepoContext(
                registration_id=row.id,
                owner=owner,
                repo=repo,
                gh_token=gh_token,
                azure_search_endpoint=os.getenv("AZURE_SEARCH_ENDPOINT") if has_azure else None,
                azure_search_key=os.getenv("AZURE_SEARCH_KEY") if has_azure else None,
                azure_search_index=derive_index_name(owner, repo) if has_azure else None,
                azure_tenant_id=row.azure_tenant_id or None,
                azure_workspace_id=row.azure_workspace_id or None,
                azure_customer_id=row.azure_customer_id or None,
            )
    except Exception as exc:
        logger.warning("Registration lookup failed for %s: %s", full_repo, exc)
        return None


# ── Healthcheck ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "prism"}


# ── Manual analysis trigger ──────────────────────────────────────────


@app.post("/analyze")
async def analyze(
    payload: PRPayload,
    client_id: str = Depends(check_freemium_limit)
):
    """Accept a ``PRPayload`` directly and run the full pipeline.

    The orchestrator looks up the registration for ``payload.repo`` in the
    platform database so it can use the correct PAT and Azure workspace
    config.  Returns 404 if no active registration is found.
    """
    repo_ctx = await _lookup_repo_context(payload.repo)
    if repo_ctx is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active registration found for '{payload.repo}'. Register via the PRism Setup Platform first.",
        )

    verdict = await orchestrate(payload, repo_ctx=repo_ctx)

    # Apply Foundry policy guardrails
    guardrails = None
    try:
        from foundry.deployment_config import apply_policy_guardrails
        guardrails = apply_policy_guardrails(verdict, payload.model_dump())
    except ImportError:
        pass

    response = verdict.model_dump()
    if guardrails is not None:
        response["guardrails"] = guardrails
    return response


# ── GitHub Webhook ───────────────────────────────────────────────────


_WEBHOOK_SECRET: str | None = os.getenv("GITHUB_WEBHOOK_SECRET")


def _verify_signature(body: bytes, signature: str | None) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if _WEBHOOK_SECRET is None:
        # No secret configured — skip verification (dev mode)
        return True
    if signature is None:
        return False
    expected = "sha256=" + hmac.new(
        _WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _fetch_pr_details(
    repo: str,
    pr_number: int,
    token: str | None = None,
) -> tuple[list[str], str]:
    """Fetch changed files and diff from the GitHub API.

    Args:
        repo:      Full repo name (owner/repo).
        pr_number: Pull request number.
        token:     GitHub PAT to use (from platform registration).

    Returns:
        (changed_files, diff)
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    changed_files: list[str] = []
    diff = ""

    async with httpx.AsyncClient(
        base_url="https://api.github.com",
        headers=headers,
        timeout=30.0,
    ) as client:
        # Fetch changed file list (handle pagination)
        try:
            page = 1
            per_page = 100
            while True:
                resp = await client.get(
                    f"/repos/{repo}/pulls/{pr_number}/files",
                    params={"per_page": per_page, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not isinstance(batch, list):
                    logger.warning("Unexpected response when fetching changed files: %r", batch)
                    break
                changed_files.extend(f["filename"] for f in batch if "filename" in f)
                if len(batch) < per_page or "next" not in resp.links:
                    break
                page += 1
        except Exception as exc:
            logger.warning("Failed to fetch changed files: %s", exc)

        # Fetch unified diff
        try:
            diff_headers = {**headers, "Accept": "application/vnd.github.v3.diff"}
            resp = await client.get(
                f"/repos/{repo}/pulls/{pr_number}",
                headers=diff_headers,
            )
            resp.raise_for_status()
            diff = resp.text
        except Exception as exc:
            logger.warning("Failed to fetch diff: %s", exc)

    return changed_files, diff


def _parse_github_webhook(body: dict) -> PRPayload | None:
    """Extract a ``PRPayload`` from a raw GitHub PR webhook body.

    Returns ``None`` if the event is not a PR open/synchronize action.
    """
    action = body.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return None

    pr = body.get("pull_request", {})
    repo_data = body.get("repository", {})

    return PRPayload(
        pr_number=pr.get("number", body.get("number", 0)),
        repo=repo_data.get("full_name", ""),
        changed_files=[],  # Populated later via API
        diff="",  # Populated later via API
        timestamp=datetime.now(timezone.utc),
    )


async def _post_pr_comment(
    repo: str,
    pr_number: int,
    body: str,
    token: str | None = None,
) -> None:
    """Post a comment to a GitHub pull request."""
    if not token:
        logger.warning("No GH token available — skipping PR comment post")
        return
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json={"body": body})
            if resp.is_error:
                logger.warning("Failed to post PR comment: %s %s", resp.status_code, resp.text)
            else:
                logger.info("Posted PRism comment to %s PR #%d", repo, pr_number)
    except Exception as exc:
        logger.warning("Exception posting PR comment: %s", exc)


def _build_pr_comment(verdict) -> str:
    """Render the VerdictReport as a GitHub PR comment in Markdown."""
    decision = verdict.decision
    score = verdict.confidence_score
    tag = "✅ GREENLIGHT" if decision == "greenlight" else "🚫 BLOCKED"

    lines = [
        f"## PRism Deployment Risk Analysis",
        f"",
        f"**Verdict:** {tag} &nbsp;|&nbsp; **Confidence Score:** `{score} / 100`",
        f"",
        "| Agent | Status | Risk Modifier | Key Finding |",
        "|-------|--------|---------------|-------------|",
    ]
    for r in verdict.agent_results:
        status_emoji = {"pass": "✅", "warning": "⚠️", "critical": "🚫"}.get(r.status, "❓")
        top_finding = r.findings[0] if r.findings else "—"
        # Truncate long findings so the table stays readable
        if len(top_finding) > 80:
            top_finding = top_finding[:77] + "..."
        lines.append(
            f"| {r.agent_name} | {status_emoji} {r.status} | {r.risk_score_modifier} | {top_finding} |"
        )

    lines.append("")
    if verdict.risk_brief:
        lines.append("<details><summary>📋 Full Risk Brief</summary>")
        lines.append("")
        lines.append(verdict.risk_brief)
        lines.append("")
        lines.append("</details>")

    if verdict.rollback_playbook:
        lines.append("")
        lines.append("<details><summary>🔄 Rollback Playbook</summary>")
        lines.append("")
        lines.append(verdict.rollback_playbook)
        lines.append("")
        lines.append("</details>")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by [PRism](https://github.com/soni-ishan/PRism) — AI-powered deployment risk intelligence*")
    return "\n".join(lines)


async def _run_orchestration(
    payload: PRPayload,
    repo_ctx: RepoContext | None = None,
) -> None:
    """Background task: fetch PR details and run the PRism pipeline."""
    try:
        logger.info("Starting orchestration for %s PR #%d", payload.repo, payload.pr_number)

        # Resolve repo context from DB if not already provided
        if repo_ctx is None:
            repo_ctx = await _lookup_repo_context(payload.repo)
        if repo_ctx is None:
            logger.warning("No registration for %s — skipping orchestration", payload.repo)
            return

        token = repo_ctx.gh_token

        if payload.repo and payload.pr_number:
            changed_files, diff = await _fetch_pr_details(
                payload.repo, payload.pr_number, token=token,
            )
            payload.changed_files = changed_files
            payload.diff = diff
            logger.info("Fetched %d changed files, diff length=%d", len(changed_files), len(diff))

        verdict = await orchestrate(payload, repo_ctx=repo_ctx)
        logger.info("Orchestration returned verdict: %s (score=%d)", verdict.decision, verdict.confidence_score)

        # Apply Foundry policy guardrails
        try:
            from foundry.deployment_config import apply_policy_guardrails
            apply_policy_guardrails(verdict, payload.model_dump())
        except ImportError:
            pass

        # Post the verdict as a PR comment so it's visible on GitHub
        comment_body = _build_pr_comment(verdict)
        await _post_pr_comment(payload.repo, payload.pr_number, comment_body, token=token)

        logger.info(
            "Background orchestration complete for PR #%d: %s",
            payload.pr_number,
            verdict.decision,
        )
    except Exception:
        logger.exception("Background orchestration FAILED for PR #%d", payload.pr_number)


@app.post("/webhook/pr")
async def handle_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    """Receive a GitHub PR webhook, fetch additional data, and run PRism."""
    body_bytes = await request.body()

    # Verify signature
    if not _verify_signature(body_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Only process pull_request events
    if x_github_event != "pull_request":
        return JSONResponse(
            {"status": "ignored", "reason": f"Event type '{x_github_event}' not handled"},
            status_code=200,
        )

    body = await request.json()
    payload = _parse_github_webhook(body)

    if payload is None:
        return JSONResponse(
            {"status": "ignored", "reason": f"PR action '{body.get('action')}' not handled"},
            status_code=200,
        )

    # Validate payload before orchestration
    if not payload.repo or payload.pr_number <= 0:
        return JSONResponse(
            {"status": "ignored", "reason": "Malformed webhook: missing repo or pr_number"},
            status_code=400,
        )

    # Resolve repo context so the background task uses the right PAT
    repo_ctx = await _lookup_repo_context(payload.repo)
    if repo_ctx is None:
        return JSONResponse(
            {"status": "rejected", "reason": f"No active registration for '{payload.repo}'"},
            status_code=404,
        )

    background_tasks.add_task(_run_orchestration, payload, repo_ctx)
    return JSONResponse({"status": "accepted", "pr_number": payload.pr_number}, status_code=202)
