"""Pydantic models for PRism setup state tracking."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class GitHubConnection(BaseModel):
    """Stores GitHub connection info after App installation or OAuth."""

    installation_id: Optional[str] = None
    repos: List[str] = []
    token_scopes: List[str] = []
    connected: bool = False


class AzureConnection(BaseModel):
    """Stores Azure connection info after OAuth login."""

    tenant_id: Optional[str] = None
    subscription_id: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    connected: bool = False


class WorkspaceInfo(BaseModel):
    """Azure Log Analytics workspace details."""

    id: str
    name: str
    resource_group: str
    customer_id: Optional[str] = None  # The workspace GUID used by the ingestion pipeline
    location: Optional[str] = None
    subscription_id: Optional[str] = None


class SubscriptionInfo(BaseModel):
    """Azure subscription details."""

    id: str
    display_name: str
    state: Optional[str] = None


class SetupState(BaseModel):
    """Tracks overall setup wizard progress."""

    github_connected: bool = False
    azure_connected: bool = False
    workspace_selected: bool = False
    verified: bool = False
    github: GitHubConnection = GitHubConnection()
    azure: AzureConnection = AzureConnection()


class WorkflowInstallRequest(BaseModel):
    """Request body for installing the PRism workflow into a repo."""

    owner: str
    repo: str
    token: str
    orchestrator_url: Optional[str] = None


class WorkspaceConnectRequest(BaseModel):
    """Request body for connecting a Log Analytics workspace."""

    subscription_id: str
    workspace_id: str
    workspace_name: str
    customer_id: Optional[str] = None
    tenant_id: Optional[str] = None  # customer's AAD tenant (for cross-tenant)
    access_token: str
