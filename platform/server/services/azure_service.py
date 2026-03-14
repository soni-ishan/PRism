"""Azure ARM API service layer for PRism platform setup.

Handles MSAL OAuth token exchange and Azure Resource Manager API calls
for discovering Log Analytics workspaces.
Has zero imports from agents/, mcp_servers/, or foundry/.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx

ARM_BASE = "https://management.azure.com"
ARM_SCOPE = "https://management.azure.com/.default"


def _get_msal_app(tenant_id: Optional[str] = None):
    """Build a ConfidentialClientApplication for the platform's Azure AD app.

    Requires AZURE_AD_TENANT_ID to be set to the specific tenant where the
    Azure subscription lives.  Using 'common' or 'organizations' will block
    personal Microsoft accounts from obtaining ARM tokens.
    """
    try:
        import msal
    except ImportError as exc:
        raise ImportError("msal package is required for Azure authentication.") from exc

    client_id = os.environ["AZURE_AD_CLIENT_ID"]
    client_secret = os.environ["AZURE_AD_CLIENT_SECRET"]
    tid = tenant_id or os.environ.get("AZURE_AD_TENANT_ID", "")
    if not tid:
        raise RuntimeError(
            "AZURE_AD_TENANT_ID must be set to your Azure AD tenant ID "
            "(not 'common' or 'organizations') for personal account support."
        )
    authority = f"https://login.microsoftonline.com/{tid}"

    return msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )


def get_auth_url(redirect_uri: Optional[str] = None, state: Optional[str] = None) -> str:
    """Return the Azure AD OAuth2 authorization URL.

    Requests ARM scope so that both org and personal accounts with Azure
    subscriptions can authenticate and access Azure resources.
    """
    app = _get_msal_app()
    redirect = redirect_uri or os.getenv(
        "AZURE_AD_REDIRECT_URI", "http://localhost:8080/api/setup/azure/callback"
    )
    params: Dict[str, Any] = {
        "scopes": [ARM_SCOPE],
        "redirect_uri": redirect,
        "prompt": "select_account",
    }
    if state:
        params["state"] = state
    url = app.get_authorization_request_url(**params)
    return url


async def exchange_code_for_token(
    code: str,
    redirect_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """Exchange an authorization code for an ARM access token."""
    app = _get_msal_app()
    redirect = redirect_uri or os.getenv(
        "AZURE_AD_REDIRECT_URI", "http://localhost:8080/api/setup/azure/callback"
    )
    result = await asyncio.to_thread(
        app.acquire_token_by_authorization_code,
        code=code,
        scopes=[ARM_SCOPE],
        redirect_uri=redirect,
    )
    if "error" in result:
        raise ValueError(f"MSAL token exchange failed: {result.get('error_description', result['error'])}")

    claims = result.get("id_token_claims", {})
    result["tenant_id"] = claims.get("tid", "")

    return result


async def list_subscriptions(access_token: str) -> List[Dict[str, Any]]:
    """List Azure subscriptions accessible with the given token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{ARM_BASE}/subscriptions?api-version=2022-12-01"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return [
        {
            "id": sub["subscriptionId"],
            "display_name": sub.get("displayName", sub["subscriptionId"]),
            "state": sub.get("state"),
        }
        for sub in data.get("value", [])
    ]


async def list_workspaces(
    access_token: str, subscription_id: str
) -> List[Dict[str, Any]]:
    """List Log Analytics workspaces in the given subscription."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = (
        f"{ARM_BASE}/subscriptions/{subscription_id}"
        "/providers/Microsoft.OperationalInsights/workspaces"
        "?api-version=2023-09-01"
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    workspaces = []
    for ws in data.get("value", []):
        # Extract resource group from the resource ID
        parts = ws.get("id", "").split("/")
        rg = ""
        try:
            rg_idx = [p.lower() for p in parts].index("resourcegroups")
            rg = parts[rg_idx + 1]
        except (ValueError, IndexError):
            pass

        workspaces.append(
            {
                "id": ws.get("id", ""),
                "name": ws.get("name", ""),
                "resource_group": rg,
                "customer_id": ws.get("properties", {}).get("customerId"),
                "location": ws.get("location"),
                "subscription_id": subscription_id,
            }
        )
    return workspaces


async def get_workspace_details(
    access_token: str,
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
) -> Dict[str, Any]:
    """Get detailed information about a specific Log Analytics workspace."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = (
        f"{ARM_BASE}/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        "/providers/Microsoft.OperationalInsights"
        f"/workspaces/{workspace_name}"
        "?api-version=2023-09-01"
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        ws = resp.json()

    parts = ws.get("id", "").split("/")
    rg = ""
    try:
        rg_idx = [p.lower() for p in parts].index("resourcegroups")
        rg = parts[rg_idx + 1]
    except (ValueError, IndexError):
        pass

    return {
        "id": ws.get("id", ""),
        "name": ws.get("name", ""),
        "resource_group": rg,
        "customer_id": ws.get("properties", {}).get("customerId"),
        "location": ws.get("location"),
        "subscription_id": subscription_id,
    }
