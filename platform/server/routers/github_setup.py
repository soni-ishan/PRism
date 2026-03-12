"""GitHub setup router for the PRism platform.

Endpoints:
  GET  /api/setup/github/install-url        — GitHub App installation URL
  GET  /api/setup/github/callback           — OAuth/App callback handler
  POST /api/setup/github/install-workflow   — Commit prism-gate.yml to a repo
  GET  /api/setup/github/status/{owner}/{repo} — Check workflow & secrets status
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from ..models import WorkflowInstallRequest
from ..services import github_service

router = APIRouter(prefix="/api/setup/github", tags=["github-setup"])

GITHUB_APP_INSTALL_BASE = "https://github.com/apps"


@router.get("/install-url")
async def get_install_url() -> dict:
    """Return the GitHub App installation URL.

    Users are redirected here to install the PRism GitHub App on their
    repositories. If a GitHub App is configured (GITHUB_APP_ID), returns the
    App install URL. Otherwise falls back to the OAuth authorization flow using
    GITHUB_CLIENT_ID.
    """
    app_id = os.getenv("GITHUB_APP_ID")
    client_id = os.getenv("GITHUB_CLIENT_ID")

    if app_id:
        app_slug = os.getenv("GITHUB_APP_SLUG", "prism-gate")
        url = f"{GITHUB_APP_INSTALL_BASE}/{app_slug}/installations/new"
        return {"url": url, "method": "app"}

    if client_id:
        redirect_uri = os.getenv(
            "GITHUB_OAUTH_REDIRECT_URI",
            "http://localhost:8080/api/setup/github/callback",
        )
        url = (
            "https://github.com/login/oauth/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            "&scope=repo"
        )
        return {"url": url, "method": "oauth"}

    raise HTTPException(
        status_code=503,
        detail="GitHub App (GITHUB_APP_ID) or OAuth app (GITHUB_CLIENT_ID) is not configured.",
    )


@router.get("/callback")
async def github_callback(
    installation_id: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    setup_action: Optional[str] = Query(None),
):
    """Handle the GitHub App installation or OAuth callback.

    After the user installs the GitHub App or completes OAuth, GitHub redirects
    back here with either an `installation_id` (App flow) or an authorization
    `code` (OAuth flow). We redirect the browser to the frontend with the
    result in the query string so the wizard can advance to step 2.
    """
    if installation_id:
        # GitHub App installation completed
        return RedirectResponse(
            url=f"/?github_connected=true&installation_id={installation_id}"
        )

    if code:
        # OAuth flow — exchange code for access token
        client_id = os.getenv("GITHUB_CLIENT_ID")
        client_secret = os.getenv("GITHUB_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise HTTPException(status_code=503, detail="GitHub OAuth not configured.")

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()

        token = data.get("access_token")
        if not token:
            raise HTTPException(
                status_code=400,
                detail=f"GitHub OAuth token exchange failed: {data.get('error_description', data)}",
            )

        return RedirectResponse(
            url=f"/?github_connected=true&github_token={token}"
        )

    raise HTTPException(
        status_code=400,
        detail="Expected 'installation_id' or 'code' query parameter.",
    )


@router.post("/install-workflow")
async def install_workflow(req: WorkflowInstallRequest) -> dict:
    """Commit prism-gate.yml to the target repository.

    Uses the GitHub Contents API to create or update
    `.github/workflows/prism-gate.yml` in the given repository.
    """
    orchestrator_url = req.orchestrator_url or os.getenv(
        "PRISM_ORCHESTRATOR_URL",
        "https://prism-dev-orchestrator.politerock-2dda79e7.eastus2.azurecontainerapps.io",
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
    token: str = Query(..., description="GitHub user/installation token"),
) -> dict:
    """Check whether the workflow file and required secrets are configured."""
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
