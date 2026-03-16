"""Registrations router — CRUD for PRism workspace registrations.

Each registration represents one repo + workspace connection owned by a user.

Endpoints:
  GET    /api/registrations           — List current user's registrations
  GET    /api/registrations/{id}      — Get a single registration
  POST   /api/registrations           — Create a new registration
  PATCH  /api/registrations/{id}      — Update Azure workspace fields
  DELETE /api/registrations/{id}      — Permanently delete a registration
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..services import auth_service
from ..services.db import RegistrationRow, UserRow, get_session, _uuid, _now
from .auth import get_current_user

import logging

logger = logging.getLogger("prism.registrations")

router = APIRouter(prefix="/api/registrations", tags=["registrations"])


# ── Request / Response models ───────────────────────────────────────────────

class CreateRegistrationRequest(BaseModel):
    gh_pat: str
    owner: str
    repo: str
    orchestrator_url: Optional[str] = ""


class UpdateRegistrationRequest(BaseModel):
    azure_tenant_id: Optional[str] = None
    azure_subscription_id: Optional[str] = None
    azure_workspace_id: Optional[str] = None
    azure_workspace_name: Optional[str] = None
    azure_customer_id: Optional[str] = None
    status: Optional[str] = None


class RegistrationResponse(BaseModel):
    id: str
    owner: str
    repo: str
    orchestrator_url: str
    azure_tenant_id: str
    azure_subscription_id: str
    azure_workspace_id: str
    azure_workspace_name: str
    azure_customer_id: str
    workflow_installed: bool
    status: str
    created_at: datetime
    updated_at: datetime


def _to_response(r: RegistrationRow) -> dict:
    """Serialize a RegistrationRow into the public API response shape."""
    return {
        "id": r.id,
        "owner": r.owner,
        "repo": r.repo,
        "orchestrator_url": r.orchestrator_url or "",
        "azure_tenant_id": r.azure_tenant_id or "",
        "azure_subscription_id": r.azure_subscription_id or "",
        "azure_workspace_id": r.azure_workspace_id or "",
        "azure_workspace_name": r.azure_workspace_name or "",
        "azure_customer_id": r.azure_customer_id or "",
        "workflow_installed": r.workflow_installed,
        "status": r.status or "active",
        "created_at": r.created_at.isoformat() if r.created_at else "",
        "updated_at": r.updated_at.isoformat() if r.updated_at else "",
    }


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("")
async def list_registrations(
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all registrations for the currently logged-in user."""
    result = await session.execute(
        select(RegistrationRow)
        .where(RegistrationRow.user_id == user.id)
        .where(RegistrationRow.status == "active")
        .order_by(RegistrationRow.created_at.desc())
    )
    rows = result.scalars().all()
    return {"registrations": [_to_response(r) for r in rows]}


@router.get("/{registration_id}")
async def get_registration(
    registration_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get a single registration by ID (must belong to current user)."""
    result = await session.execute(
        select(RegistrationRow)
        .where(RegistrationRow.id == registration_id)
        .where(RegistrationRow.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")
    return _to_response(row)


@router.post("", status_code=201)
async def create_registration(
    req: CreateRegistrationRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create a new registration (encrypt and store the PAT)."""
    try:
        encrypted_pat = auth_service.encrypt_pat(req.gh_pat)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    row = RegistrationRow(
        id=_uuid(),
        user_id=user.id,
        gh_pat_encrypted=encrypted_pat,
        owner=req.owner,
        repo=req.repo,
        orchestrator_url=req.orchestrator_url or "",
        status="active",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_response(row)


@router.patch("/{registration_id}")
async def update_registration(
    registration_id: str,
    req: UpdateRegistrationRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update Azure fields or status on an existing registration."""
    result = await session.execute(
        select(RegistrationRow)
        .where(RegistrationRow.id == registration_id)
        .where(RegistrationRow.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")

    if req.azure_tenant_id is not None:
        row.azure_tenant_id = req.azure_tenant_id
    if req.azure_subscription_id is not None:
        row.azure_subscription_id = req.azure_subscription_id
    if req.azure_workspace_id is not None:
        row.azure_workspace_id = req.azure_workspace_id
    if req.azure_workspace_name is not None:
        row.azure_workspace_name = req.azure_workspace_name
    if req.azure_customer_id is not None:
        row.azure_customer_id = req.azure_customer_id
    if req.status is not None:
        row.status = req.status
    row.updated_at = _now()

    # When Azure workspace fields are being linked, ensure the per-repo
    # AI Search index exists so the ingestion pipeline can write to it
    # and the History Agent can query it.
    azure_being_linked = (
        req.azure_subscription_id is not None
        or req.azure_workspace_id is not None
        or req.azure_customer_id is not None
    )
    if azure_being_linked and row.owner and row.repo:
        try:
            from agents.shared.data_contract import derive_index_name
            from mcp_servers.azure_mcp_server.setup import create_index

            idx = derive_index_name(row.owner, row.repo)
            create_index(index_name=idx)
            logger.info("Ensured AI Search index '%s' for %s/%s", idx, row.owner, row.repo)
        except Exception as exc:
            logger.warning(
                "Could not create AI Search index for %s/%s: %s",
                row.owner, row.repo, exc,
            )

    await session.commit()
    await session.refresh(row)
    return _to_response(row)


@router.delete("/{registration_id}")
async def delete_registration(
    registration_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Permanently delete a registration."""
    result = await session.execute(
        select(RegistrationRow)
        .where(RegistrationRow.id == registration_id)
        .where(RegistrationRow.user_id == user.id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Registration not found")

    await session.delete(row)
    await session.commit()
    return {"ok": True, "message": f"Registration {registration_id} deleted"}
