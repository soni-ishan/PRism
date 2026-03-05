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
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from agents.orchestrator import PRPayload, orchestrate

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


# ── Healthcheck ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "prism"}


# ── Manual analysis trigger ──────────────────────────────────────────

@app.post("/analyze")
async def analyze(payload: PRPayload):
    """Accept a ``PRPayload`` directly and run the full pipeline."""
    verdict = await orchestrate(payload)

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

_GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")
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
    repo: str, pr_number: int
) -> tuple[list[str], str]:
    """Fetch changed files and diff from the GitHub API.

    Returns:
        (changed_files, diff)
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    if _GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {_GITHUB_TOKEN}"

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


async def _run_orchestration(payload: PRPayload) -> None:
    """Background task: fetch PR details and run the PRism pipeline."""
    if payload.repo and payload.pr_number:
        changed_files, diff = await _fetch_pr_details(payload.repo, payload.pr_number)
        payload.changed_files = changed_files
        payload.diff = diff
    verdict = await orchestrate(payload)

    # Apply Foundry policy guardrails
    try:
        from foundry.deployment_config import apply_policy_guardrails
        apply_policy_guardrails(verdict, payload.model_dump())
    except ImportError:
        pass

    logger.info(
        "Background orchestration complete for PR #%d: %s",
        payload.pr_number,
        verdict.decision,
    )


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

    background_tasks.add_task(_run_orchestration, payload)
    return JSONResponse({"status": "accepted", "pr_number": payload.pr_number}, status_code=202)
