"""PRism Setup Platform — standalone FastAPI application.

This server is COMPLETELY INDEPENDENT of the orchestrator logic.
It has zero imports from agents/, mcp_servers/, or foundry/.
The orchestrator is known only as an external URL string (PRISM_ORCHESTRATOR_URL).

Run with:
    uvicorn server.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Load .env from the platform/ directory (one level up from server/)
load_dotenv(Path(__file__).parent.parent / ".env")

from .routers import azure_setup, github_setup

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PRism Setup Platform",
    description=(
        "Self-service onboarding wizard for PRism — independent of the orchestrator. "
        "Guides users through connecting GitHub and Azure."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# Allow the frontend (served from same origin) and any localhost dev server
_origins = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
_extra_origin = os.getenv("PLATFORM_ORIGIN", "")
if _extra_origin:
    _origins.append(_extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(github_setup.router)
app.include_router(azure_setup.router)

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Health check — also reports the orchestrator URL this platform targets."""
    orchestrator_url = os.getenv(
        "PRISM_ORCHESTRATOR_URL",
        "https://nontransportable-monte-advocatory.ngrok-free.dev",
    )
    return {
        "status": "ok",
        "platform": "PRism Setup Wizard",
        "orchestrator_url": orchestrator_url,
    }


# ---------------------------------------------------------------------------
# Serve frontend static files (must be last — catches all unmatched routes)
# ---------------------------------------------------------------------------

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
