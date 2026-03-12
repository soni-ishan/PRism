"""GitHub setup router for the PRism platform.

Endpoints:
  POST /api/setup/github/validate-token     — Validate a GitHub PAT
  POST /api/setup/github/install-workflow   — Commit prism-gate.yml to a repo
  GET  /api/setup/github/status/{owner}/{repo} — Check workflow & secrets status
"""

from __future__ import annotations

import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from ..models import WorkflowInstallRequest
from ..services import github_service

# GitHub owner/repo names: alphanumeric, hyphens, dots, underscores
_SAFE_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")


def _validate_name(value: str, label: str) -> str:
    """Reject owner/repo names that contain path-traversal or special chars."""
    if not value or not _SAFE_NAME.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {label}: must be alphanumeric, hyphens, dots, or underscores.",
        )
    return value

router = APIRouter(prefix="/api/setup/github", tags=["github-setup"])


class ValidateTokenRequest(BaseModel):
    token: str


@router.post("/validate-token")
async def validate_token(req: ValidateTokenRequest) -> dict:
    """Validate a GitHub Personal Access Token.

    Checks that the PAT is valid by calling /user and returns the
    authenticated GitHub username and token scopes.
    """
    if not req.token:
        raise HTTPException(status_code=400, detail="No token provided.")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": github_service._auth_header(req.token),
                "Accept": "application/vnd.github+json",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired GitHub token. Please check your PAT and try again.",
            )
        data = resp.json()
        scopes = resp.headers.get("x-oauth-scopes", "")
        return {
            "valid": True,
            "username": data.get("login", "unknown"),
            "scopes": scopes,
        }


@router.post("/install-workflow")
async def install_workflow(req: WorkflowInstallRequest) -> dict:
    """Commit prism-gate.yml to the target repository.

    Uses the GitHub Contents API to create or update
    `.github/workflows/prism-gate.yml` in the given repository.
    """
    if not req.token:
        raise HTTPException(
            status_code=400,
            detail="No GitHub PAT provided. Please enter your Personal Access Token.",
        )

    # Quick token validity check — hit /user to confirm the PAT works
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": github_service._auth_header(req.token),
                "Accept": "application/vnd.github+json",
            },
        )
        if user_resp.status_code != 200:
            raise HTTPException(
                status_code=401,
                detail=f"GitHub PAT is invalid or expired (HTTP {user_resp.status_code}). Please check your token.",
            )
        github_user = user_resp.json().get("login", "unknown")
        print(f"[DEBUG] PAT valid for GitHub user: {github_user}")
        print(f"[DEBUG] Installing workflow to: {req.owner}/{req.repo}")

    orchestrator_url = req.orchestrator_url or os.getenv(
        "PRISM_ORCHESTRATOR_URL",
        "https://nontransportable-monte-advocatory.ngrok-free.dev",
    )
    try:
        result = await github_service.commit_workflow_file(
            token=req.token,
            owner=req.owner,
            repo=req.repo,
            orchestrator_url=orchestrator_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "message": f"prism-gate.yml installed in {req.owner}/{req.repo}",
        "commit_url": result.get("commit", {}).get("html_url"),
    }


@router.get("/status/{owner}/{repo}")
async def get_status(
    owner: str,
    repo: str,
    authorization: str = Header(..., description="Bearer <GitHub token>"),
) -> dict:
    """Check whether the workflow file and required secrets are configured."""
    _validate_name(owner, "owner")
    _validate_name(repo, "repo")
    token = authorization.removeprefix("Bearer ").strip()
    workflow_exists = await github_service.check_workflow_exists(
        token=token, owner=owner, repo=repo
    )
    secret_configured = await github_service.check_secret_configured(
        token=token, owner=owner, repo=repo, secret_name="GH_PAT"
    )

    return {
        "owner": owner,
        "repo": repo,
        "workflow_exists": workflow_exists,
        "gh_pat_secret_configured": secret_configured,
        "ready": workflow_exists,
    }
