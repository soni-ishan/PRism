# PRism Setup Platform

A **self-service onboarding wizard** that guides users through connecting their GitHub repository and Azure Log Analytics workspace to PRism — with zero manual YAML editing or shell scripts.

## What It Does

The platform is a **standalone web application** (FastAPI backend + vanilla HTML/CSS/JS frontend) that:

1. **GitHub Connection** — Redirects users to install the PRism GitHub App (or complete an OAuth flow). After installation the platform automatically commits `.github/workflows/prism-gate.yml` to the target repository via the GitHub Contents API. No more manual copy-paste.

2. **Azure Connection** — Walks users through an Azure AD OAuth login, then presents a visual dropdown of their Azure subscriptions and Log Analytics workspaces. One click connects the selected workspace to the PRism ingestion pipeline.

3. **Verify** — Shows a setup summary and pings the orchestrator health endpoint so users can confirm everything is working before opening their first PR.

## Architecture — Completely Independent of the Orchestrator

```
┌────────────────────────────────────────────────────────────┐
│                     platform/                              │
│                                                            │
│  ┌──────────────┐      ┌─────────────────────────────┐    │
│  │  Frontend     │      │  Backend  (FastAPI :8080)   │    │
│  │  index.html   │◄────►│  server/app.py              │    │
│  │  app.js       │      │                             │    │
│  │  styles.css   │      │  routers/                   │    │
│  └──────────────┘      │    github_setup.py          │    │
│                         │    azure_setup.py           │    │
│                         │  services/                  │    │
│                         │    github_service.py        │    │
│                         │    azure_service.py         │    │
│                         └──────────────┬──────────────┘    │
│  ZERO imports from                     │ Only knows        │
│  agents/, mcp_servers/, foundry/       │ orchestrator      │
│                                        │ as a URL string   │
└────────────────────────────────────────┼───────────────────┘
                                         │
                             ┌───────────▼──────────────┐
                             │  Orchestrator (external)  │
                             │  Could be PRism-hosted    │
                             │  OR user's own Azure      │
                             │  deployment in the future │
                             └──────────────────────────┘
```

**Key principle**: `platform/` has **zero imports** from `agents/`, `mcp_servers/`, or `foundry/`. The orchestrator is known only via the `PRISM_ORCHESTRATOR_URL` environment variable. This means that when users can self-host the orchestrator, this platform works unchanged — it just points to their URL.

## Running Locally

```bash
cd platform
pip install -r requirements.txt
cp .env.example .env
# Fill in at least GITHUB_CLIENT_ID + GITHUB_CLIENT_SECRET
# or GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY_PATH
# and AZURE_AD_CLIENT_ID + AZURE_AD_CLIENT_SECRET

uvicorn server.app:app --port 8080 --reload
```

Open **http://localhost:8080** in your browser.

The interactive API documentation is available at **http://localhost:8080/api/docs**.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PRISM_ORCHESTRATOR_URL` | No | URL of the PRism orchestrator (defaults to the shared dev instance) |
| `GITHUB_APP_ID` | App flow | Numeric ID of your registered GitHub App |
| `GITHUB_APP_SLUG` | App flow | Slug of your GitHub App (used for install URL) |
| `GITHUB_APP_PRIVATE_KEY_PATH` | App flow | Path to the `.pem` private key for your GitHub App |
| `GITHUB_CLIENT_ID` | OAuth flow | Client ID of your GitHub OAuth App |
| `GITHUB_CLIENT_SECRET` | OAuth flow | Client secret of your GitHub OAuth App |
| `GITHUB_OAUTH_REDIRECT_URI` | OAuth flow | Callback URL (must match GitHub App settings) |
| `AZURE_AD_CLIENT_ID` | Azure | Client ID of your Azure AD App registration |
| `AZURE_AD_CLIENT_SECRET` | Azure | Client secret of your Azure AD App |
| `AZURE_AD_TENANT_ID` | No | Tenant ID or `common` (default) for multi-tenant |
| `AZURE_AD_REDIRECT_URI` | Azure | Callback URL (must match Azure AD App redirect URI) |
| `PLATFORM_CONFIG_PATH` | No | Path for persisting workspace config JSON |

## Registering a GitHub App

1. Go to **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
2. Set the app name to `PRism Gate` (or similar)
3. Set the **Homepage URL** to your platform URL
4. Set the **Callback URL** to `http://localhost:8080/api/setup/github/callback`
5. **Webhook**: Uncheck "Active" (not needed for the setup wizard)
6. **Repository permissions**:
   - Contents: **Read and write** (to commit the workflow file)
   - Secrets: **Read** (to check if `GH_PAT` is configured)
   - Pull requests: **Write** (for PRism to post comments)
7. Click **Create GitHub App**, note the **App ID**, and download the **private key `.pem`**
8. Copy the App ID and `.pem` path into your `.env`

## Registering an Azure AD App

1. Go to **Azure Portal → Azure Active Directory → App registrations → New registration**
2. Name: `PRism Setup`; Supported account types: **Any Azure AD directory + personal accounts**
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
│   ├── __init__.py
│   ├── app.py                 # Standalone FastAPI app (port 8080)
│   ├── models.py              # Pydantic models for setup state
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── github_setup.py    # GitHub App/OAuth endpoints
│   │   └── azure_setup.py    # Azure OAuth + workspace picker
│   └── services/
│       ├── __init__.py
│       ├── github_service.py  # GitHub Contents API interactions
│       └── azure_service.py  # Azure ARM API interactions
├── frontend/
│   ├── index.html             # 3-step setup wizard
│   ├── styles.css             # PRism-branded styling
│   └── app.js                 # Wizard state & API calls
├── requirements.txt           # Platform-only dependencies
├── Dockerfile                 # Independent container
├── .env.example               # Configuration template
└── README.md                  # This file
```
