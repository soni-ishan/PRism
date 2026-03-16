<p align="center">
  <img src="frontend/logo.png" alt="PRism" width="100" />
</p>

# PRism Setup Platform

A **self-service onboarding wizard** that guides users through connecting their GitHub repository and Azure Log Analytics workspace to PRism — with zero manual YAML editing or shell scripts.

## What It Does

The platform is a **standalone web application** (FastAPI backend + vanilla HTML/CSS/JS frontend) that:

1. **GitHub Connection** — GitHub OAuth login. The platform commits `.github/workflows/prism-gate.yml` directly to the target repository via the GitHub Contents API, and stores an encrypted PAT for the orchestrator's webhook handler. No manual copy-paste.

2. **Azure Connection** — Azure AD OAuth login with a visual dropdown of the user's subscriptions and Log Analytics workspaces. One click links the selected workspace to PRism's ingestion pipeline (persisted to the registrations database).

3. **Verify** — Shows a setup summary and pings the orchestrator health endpoint (`GET /health`) so users can confirm everything is working before opening their first PR.

4. **Dashboard** — `app.html` shows all registered repos, per-repo workflow status, and lets users manage their Azure workspace connections after onboarding.

## Architecture — Completely Independent of the Orchestrator

```
┌────────────────────────────────────────────────────────────┐
│                     platform/                              │
│                                                            │
│  ┌──────────────┐      ┌─────────────────────────────┐    │
│  │  Frontend     │      │  Backend  (FastAPI :8080)   │    │
│  │  index.html   │◄────►│  server/app.py              │    │
│  │  app.html     │      │                             │    │
│  │  docs.html    │      │  routers/                   │    │
│  └──────────────┘      │    auth.py                  │    │
│                         │    github_setup.py          │    │
│                         │    azure_setup.py           │    │
│                         │    registrations.py         │    │
│                         │  services/                  │    │
│                         │    auth_service.py          │    │
│                         │    github_service.py        │    │
│                         │    azure_service.py         │    │
│                         │    db.py (SQLAlchemy async) │    │
│                         └──────────────┬──────────────┘    │
│  ZERO imports from                     │ Knows orchestrator │
│  agents/, mcp_servers/, foundry/       │ only as a URL      │
└────────────────────────────────────────┼───────────────────┘
                                         │
                             ┌───────────▼──────────────┐
                             │  Orchestrator (external)  │
                             │  Hosted on Azure or       │
                             │  user's own deployment    │
                             └──────────────────────────┘
```

**Key principle**: `platform/` has **zero imports** from `agents/`, `mcp_servers/`, or `foundry/`. The orchestrator is known only via the `PRISM_ORCHESTRATOR_URL` environment variable.

## Running Locally

```bash
cd platform
pip install -r requirements.txt
cp .env.example .env
# Fill in GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET
# and AZURE_AD_CLIENT_ID + AZURE_AD_CLIENT_SECRET

uvicorn server.app:app --port 8080 --reload
```

Open **http://localhost:8080** in your browser.

Interactive API docs: **http://localhost:8080/api/docs**

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PRISM_ORCHESTRATOR_URL` | No | URL of the PRism orchestrator (defaults to shared hosted instance) |
| `GITHUB_CLIENT_ID` | Yes | Client ID of your GitHub OAuth App |
| `GITHUB_CLIENT_SECRET` | Yes | Client secret of your GitHub OAuth App |
| `GITHUB_OAUTH_REDIRECT_URI` | Yes | Callback URL (must match GitHub App settings) |
| `AZURE_AD_CLIENT_ID` | Yes | Client ID of your Azure AD App registration |
| `AZURE_AD_CLIENT_SECRET` | Yes | Client secret of your Azure AD App |
| `AZURE_AD_TENANT_ID` | No | Tenant ID or `common` (default) for multi-tenant |
| `AZURE_AD_REDIRECT_URI` | Yes | Callback URL (must match Azure AD App redirect URI) |
| `JWT_SECRET` | Yes | Secret key for signing session JWTs (24-hour expiry) |
| `ENCRYPTION_KEY` | Yes | Fernet key for encrypting GitHub PATs at rest |
| `DATABASE_URL` | No | Async DB URL — defaults to SQLite (`prism_platform.db`) in dev, PostgreSQL in prod |
| `PLATFORM_CONFIG_PATH` | No | Path for persisting Azure workspace config JSON |

## Authentication Flow

- **Users** sign in via GitHub OAuth2. Session issued as an httpOnly `prism_session` JWT cookie.
- **GitHub PATs** are encrypted with Fernet (AES-128-CBC) at rest using `ENCRYPTION_KEY`.
- **Azure workspace linking** uses a short-lived Azure AD Bearer token (not stored) to list subscriptions and workspaces.
- **GitHub webhooks** sent to the orchestrator are verified via HMAC-SHA256.

## Registering a GitHub OAuth App

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**
2. Set **Homepage URL** to your platform URL
3. Set **Authorization callback URL** to `http://localhost:8080/api/auth/github/callback`
4. Copy the **Client ID** and generate a **Client Secret** into your `.env`

## Registering an Azure AD App

1. Go to **Azure Portal → Azure Active Directory → App registrations → New registration**
2. Name: `PRism Setup`; supported account types: **Any Azure AD directory + personal accounts**
3. Add a **Redirect URI**: `http://localhost:8080/api/setup/azure/callback` (type: Web)
4. Go to **API permissions → Add a permission → Azure Service Management → user_impersonation**
5. Go to **Certificates & secrets → New client secret**, copy the value
6. Copy the **Application (client) ID** and secret into your `.env`

## Docker Deployment

```bash
cd platform
docker build -t prism-platform .
docker run -p 8080:8080 --env-file .env prism-platform
```

## File Structure

```
platform/
├── server/
│   ├── app.py                     # Standalone FastAPI app (port 8080)
│   ├── models.py                  # Pydantic models for request/response
│   ├── routers/
│   │   ├── auth.py                # GitHub OAuth2 login/logout + /me
│   │   ├── github_setup.py        # PAT validation + workflow install + status
│   │   ├── azure_setup.py         # Azure OAuth + subscription/workspace picker
│   │   └── registrations.py       # CRUD endpoints for repo registrations
│   └── services/
│       ├── auth_service.py        # JWT session + Fernet PAT encryption
│       ├── github_service.py      # GitHub Contents API interactions
│       ├── azure_service.py       # Azure ARM API interactions
│       └── db.py                  # SQLAlchemy async engine + session factory
├── frontend/
│   ├── index.html                 # 3-step setup wizard
│   ├── app.html                   # Registration dashboard
│   ├── docs.html                  # Documentation page
│   ├── docs.md                    # Documentation source
│   ├── logo.png                   # PRism logo
│   ├── css/                       # PRism-branded styles
│   └── js/                        # Wizard state & API calls
├── requirements.txt               # Platform-only dependencies
├── Dockerfile                     # Independent container image
├── .env.example                   # Configuration template
└── README.md                      # This file
```
