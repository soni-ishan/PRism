"""Authentication router — GitHub OAuth login / logout / session.

Endpoints:
  GET  /api/auth/github/login     — Redirect to GitHub OAuth
  GET  /api/auth/github/callback  — Handle GitHub callback, set session cookie
  GET  /api/auth/me               — Return current logged-in user
  POST /api/auth/logout           — Clear session cookie
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..services import auth_service
from ..services.db import UserRow, get_session, _uuid, _now

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "prism_session"


# ── Dependency: current user from cookie ────────────────────────────────────

async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> UserRow:
    """Extract and verify the JWT cookie, return the UserRow or 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = auth_service.verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user_id = payload.get("sub")
    result = await session.execute(select(UserRow).where(UserRow.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/github/login")
async def github_login():
    """Redirect the browser to GitHub's OAuth authorization page."""
    if not auth_service.GITHUB_CLIENT_ID:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured.")
    url = auth_service.get_github_login_url()
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_callback(
    code: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Exchange the code for a token, upsert the user, issue a session cookie."""
    # 1. Exchange code → access_token
    try:
        gh_token = await auth_service.exchange_github_code(code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"GitHub OAuth failed: {exc}") from exc

    # 2. Fetch GitHub profile
    try:
        profile = await auth_service.fetch_github_user(gh_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to fetch GitHub profile: {exc}") from exc

    github_id = profile["id"]
    username = profile.get("login", "unknown")
    avatar_url = profile.get("avatar_url", "")
    email = profile.get("email", "") or ""

    # 3. Upsert user
    result = await session.execute(
        select(UserRow).where(UserRow.github_id == github_id)
    )
    user = result.scalar_one_or_none()
    if user:
        user.username = username
        user.avatar_url = avatar_url
        user.email = email
        user.updated_at = _now()
    else:
        user = UserRow(
            id=_uuid(),
            github_id=github_id,
            username=username,
            avatar_url=avatar_url,
            email=email,
        )
        session.add(user)
    await session.commit()
    await session.refresh(user)

    # 4. Issue JWT in httpOnly cookie and redirect to dashboard
    jwt_token = auth_service.create_jwt(user.id, user.username)
    response = RedirectResponse(url="/app.html#dashboard", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        httponly=True,
        samesite="lax",
        max_age=auth_service.JWT_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@router.get("/me")
async def get_me(user: UserRow = Depends(get_current_user)):
    """Return the currently logged-in user's profile."""
    return {
        "id": user.id,
        "github_id": user.github_id,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "email": user.email,
    }


@router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
