"""Database layer — SQLAlchemy async with SQLite (dev) or PostgreSQL (prod).

Switch engines via the DATABASE_URL environment variable:
  - sqlite+aiosqlite:///./prism_platform.db   (local dev, default)
  - postgresql+asyncpg://user:pass@host/db     (Azure PostgreSQL)
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    BigInteger,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./prism_platform.db",
)

# Create the async engine with production-ready settings
_connect_args: dict = {}
_engine_kwargs: dict = {"echo": False}

if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
else:
    # PostgreSQL (asyncpg) — add connection timeout and SSL config
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    _connect_args = {
        "timeout": 30,           # asyncpg connection timeout (seconds)
        "command_timeout": 30,   # per-statement timeout
        "ssl": _ssl_ctx,         # explicit SSL context for Azure PG
    }
    _engine_kwargs.update(
        pool_pre_ping=True,      # validate connections before use
        pool_recycle=300,        # recycle connections every 5 min
        pool_size=5,
        max_overflow=5,
    )

engine = create_async_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    **_engine_kwargs,
)

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── ORM Base ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Users table ─────────────────────────────────────────────────────────────

class UserRow(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    github_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(Text, nullable=False)
    avatar_url = Column(Text, default="")
    email = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    registrations = relationship("RegistrationRow", back_populates="user", lazy="selectin")


# ── Registrations table ────────────────────────────────────────────────────

class RegistrationRow(Base):
    __tablename__ = "registrations"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)

    # GitHub
    gh_pat_encrypted = Column(Text, nullable=False)  # Fernet-encrypted PAT
    owner = Column(Text, nullable=False)
    repo = Column(Text, nullable=False)
    orchestrator_url = Column(Text, default="")

    # Azure (optional — filled in step 2)
    azure_tenant_id = Column(Text, default="")  # customer's AAD tenant for cross-tenant access
    azure_subscription_id = Column(Text, default="")
    azure_workspace_id = Column(Text, default="")
    azure_workspace_name = Column(Text, default="")
    azure_customer_id = Column(Text, default="")

    workflow_installed = Column(Boolean, default=False)
    status = Column(String(20), default="active")  # active | inactive

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship("UserRow", back_populates="registrations")


# ── Lifecycle helpers ───────────────────────────────────────────────────────

async def init_db(*, retries: int = 5, base_delay: float = 2.0) -> None:
    """Create all tables (idempotent). Retries on transient connection errors."""
    for attempt in range(1, retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database initialised (attempt %d/%d)", attempt, retries)
            return
        except Exception as exc:
            if attempt == retries:
                logger.error("Database connection failed after %d attempts: %s", retries, exc)
                raise
            delay = base_delay * (2 ** (attempt - 1))   # exponential backoff
            logger.warning(
                "Database connection attempt %d/%d failed (%s), retrying in %.1fs …",
                attempt, retries, exc, delay,
            )
            await asyncio.sleep(delay)


async def get_session() -> AsyncSession:  # noqa: D401 — FastAPI Depends
    """Yield an async session for use as a FastAPI dependency."""
    async with async_session() as session:
        yield session
