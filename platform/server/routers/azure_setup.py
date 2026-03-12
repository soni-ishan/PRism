"""Azure setup router for the PRism platform.

Endpoints:
  GET  /api/setup/azure/auth-url                     — Azure AD OAuth2 URL
  GET  /api/setup/azure/callback                     — OAuth callback handler
  GET  /api/setup/azure/subscriptions                — List user's subscriptions
  GET  /api/setup/azure/workspaces/{subscription_id} — List workspaces in sub
  POST /api/setup/azure/connect-workspace            — Store workspace connection
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import RedirectResponse

from ..models import WorkspaceConnectRequest
from ..services import azure_service

router = APIRouter(prefix="/api/setup/azure", tags=["azure-setup"])

# Where workspace connection config is persisted locally (default)
_CONFIG_PATH = Path(os.getenv("PLATFORM_CONFIG_PATH", "/tmp/prism_workspace_config.json"))


@router.get("/auth-url")
async def get_auth_url(state: Optional[str] = Query(None)) -> dict:
    """Return the Azure AD OAuth2 authorization URL.

    The frontend redirects the user to this URL so they can sign into their
    Azure account and consent to the `Azure Management` scope.
    """
    client_id = os.getenv("AZURE_AD_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Azure AD app is not configured (AZURE_AD_CLIENT_ID missing).",
        )
    try:
        url = azure_service.get_auth_url(state=state)
        return {"url": url}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/callback")
async def azure_callback(
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
):
    """Handle Azure AD OAuth2 callback.

    Exchanges the authorization code for an access token and redirects the
    browser back to the frontend with the token in the URL fragment so the
    wizard can store it in memory and advance to the workspace picker.
    """
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Azure OAuth error: {error_description or error}",
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' query parameter.")

    try:
        token_data = await azure_service.exchange_code_for_token(code=code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    access_token = token_data.get("access_token", "")
    # Token in URL fragment (after #) — never sent to server in referrer
    return RedirectResponse(
        url=f"/#azure_connected=true&azure_token={access_token}"
    )


@router.get("/subscriptions")
async def list_subscriptions(
    authorization: str = Header(..., description="Bearer <Azure ARM token>"),
) -> dict:
    """List all Azure subscriptions accessible with the provided token."""
    token = authorization.removeprefix("Bearer ").strip()
    try:
        subs = await azure_service.list_subscriptions(access_token=token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"subscriptions": subs}


@router.get("/workspaces/{subscription_id}")
async def list_workspaces(
    subscription_id: str,
    authorization: str = Header(..., description="Bearer <Azure ARM token>"),
) -> dict:
    """List Log Analytics workspaces in the given subscription."""
    token = authorization.removeprefix("Bearer ").strip()
    try:
        workspaces = await azure_service.list_workspaces(
            access_token=token, subscription_id=subscription_id
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"workspaces": workspaces}


@router.post("/connect-workspace")
async def connect_workspace(req: WorkspaceConnectRequest) -> dict:
    """Store the selected workspace configuration.

    Fetches full workspace details (including the GUID / customer_id required
    by the ingestion pipeline) and persists the connection to a local JSON
    config file. Returns the configuration values for the user to review.
    """
    # Derive resource group and name from the workspace_id (ARM resource path)
    parts = req.workspace_id.split("/")
    ws_name = parts[-1] if parts else req.workspace_name
    rg = ""
    try:
        rg_idx = [p.lower() for p in parts].index("resourcegroups")
        rg = parts[rg_idx + 1]
    except (ValueError, IndexError):
        pass

    # Fetch full details to get customer_id (workspace GUID)
    customer_id = req.customer_id
    if not customer_id and rg and ws_name:
        try:
            details = await azure_service.get_workspace_details(
                access_token=req.access_token,
                subscription_id=req.subscription_id,
                resource_group=rg,
                workspace_name=ws_name,
            )
            customer_id = details.get("customer_id")
        except Exception:
            pass  # Non-fatal — user may already have the GUID

    config = {
        "subscription_id": req.subscription_id,
        "workspace_id": req.workspace_id,
        "workspace_name": req.workspace_name,
        "customer_id": customer_id,
        "resource_group": rg,
    }

    # Persist to local file for the ingestion pipeline to consume
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(config, indent=2))
    except OSError:
        pass  # Best-effort; don't fail the response

    return {
        "success": True,
        "message": f"Workspace '{req.workspace_name}' connected successfully.",
        "config": config,
        "env_vars": {
            "AZURE_LOG_WORKSPACE_ID": customer_id or req.workspace_id,
            "AZURE_SUBSCRIPTION_ID": req.subscription_id,
        },
    }
