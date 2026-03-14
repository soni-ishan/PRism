"""Authentication service — GitHub OAuth + JWT + PAT encryption.

Handles:
  - GitHub OAuth code→token exchange
  - Fetching the authenticated GitHub user profile
  - Issuing / verifying JWT session tokens (httpOnly cookies)
  - Fernet (AES) encryption of GitHub PATs at rest
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from cryptography.fernet import Fernet

# ── Configuration ───────────────────────────────────────────────────────────

GITHUB_CLIENT_ID = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

# Fernet key for encrypting PATs.  Generate once with Fernet.generate_key().
# Lazily initialized to ensure env vars are loaded first.
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Return a Fernet instance, initializing lazily from env."""
    global _fernet
    if _fernet is None:
        key = os.getenv("ENCRYPTION_KEY", "")
        if not key:
            raise RuntimeError("ENCRYPTION_KEY is not configured — cannot encrypt PAT.")
        _fernet = Fernet(key.encode())
    return _fernet


# ── GitHub OAuth ────────────────────────────────────────────────────────────

GITHUB_REDIRECT_URI = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "")


def get_github_login_url(state: str | None = None) -> str:
    """Return the GitHub OAuth authorization URL."""
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope=read:user user:email"
    )
    if GITHUB_REDIRECT_URI:
        url += f"&redirect_uri={GITHUB_REDIRECT_URI}"
    if state:
        url += f"&state={state}"
    return url


async def exchange_github_code(code: str) -> str:
    """Exchange a GitHub OAuth authorization code for an access_token."""
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
        }
        if GITHUB_REDIRECT_URI:
            payload["redirect_uri"] = GITHUB_REDIRECT_URI
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json=payload,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(data.get("error_description", "GitHub OAuth token exchange failed"))
        return token


async def fetch_github_user(access_token: str) -> dict:
    """Fetch the authenticated user's GitHub profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        return resp.json()


# ── JWT helpers ─────────────────────────────────────────────────────────────

def create_jwt(user_id: str, github_username: str) -> str:
    """Issue a signed JWT for the given user."""
    payload = {
        "sub": user_id,
        "username": github_username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict | None:
    """Verify and decode a JWT. Returns the payload dict or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── PAT encryption ──────────────────────────────────────────────────────────

def encrypt_pat(pat: str) -> str:
    """Encrypt a GitHub PAT with Fernet (AES-128-CBC). Returns base64 token."""
    return _get_fernet().encrypt(pat.encode()).decode()


def decrypt_pat(encrypted: str) -> str:
    """Decrypt a Fernet-encrypted PAT back to plaintext."""
    return _get_fernet().decrypt(encrypted.encode()).decode()
