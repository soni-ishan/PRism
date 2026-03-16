<p align="center">
  <img src="../../vscode_extension/media/prism-icon.png" alt="PRism" width="80" />
</p>

# Deployment Configuration — PRism

Azure infrastructure-as-code for deploying the full PRism stack. Deploy in order: **infra → orchestrator → platform**.

## Prerequisites

- Azure CLI (`az`) logged in (`az login`)
- Docker Desktop running (for container image builds)
- Copy `parameters.example.json` to `parameters.json` in each subfolder and fill in values

## Folder Structure

```
deployment_config/
  infra/              ← shared Azure resources (one-time setup)
  orchestrator/       ← orchestrator container app + Dockerfile
  platform/           ← platform container app + Dockerfile
  generate-env.ps1    ← generate .env files from deployed Azure resources
  cleanup.ps1         ← delete resource group + purge soft-deleted resources
```

## Step 1 — Shared Infrastructure

```powershell
cd infra
.\deploy.ps1                                  # uses parameters.json
.\deploy.ps1 -Location eastus                 # override region
.\deploy.ps1 -ResourceGroupName rg-prism-prod
```

Creates:
- Azure Container Registry (ACR)
- Azure Container Apps Environment
- Azure PostgreSQL (for platform registrations DB)
- Azure OpenAI (GPT-4o-mini, Sweden Central)
- Azure AI Search (semantic search for incident history)
- Azure Content Safety
- Key Vault
- Log Analytics workspace + Application Insights
- Managed Identities

## Step 2 — Orchestrator Container App

```powershell
cd orchestrator
.\deploy.ps1                  # builds Docker image, pushes to ACR, deploys Container App
.\deploy.ps1 -SkipDocker      # redeploy Bicep only (image already in ACR)
```

Deploys the FastAPI orchestrator (`agents/`) on port 8000. Exposes `/analyze`, `/webhook/pr`, and `/health` publicly on Azure Container Apps.

## Step 3 — Platform Container App

```powershell
cd platform
.\deploy.ps1                  # builds Docker image, pushes to ACR, deploys Container App
.\deploy.ps1 -SkipDocker      # redeploy Bicep only
```

Deploys the PRism Setup Platform (`platform/`) on port 8080. Exposes the setup wizard and dashboard publicly.

## Utilities

```powershell
# Generate .env files from already-deployed Azure resources
.\generate-env.ps1

# Delete all resources (prompts for confirmation)
.\cleanup.ps1
.\cleanup.ps1 -Force          # skip confirmation prompt
```

## Parameter Files

Each folder has its own `parameters.json` (secrets, git-ignored) and `parameters.example.json` (template, committed):

| Folder | Key Parameters |
|---|---|
| `infra/parameters.json` | Region, OpenAI model config, PostgreSQL admin creds |
| `orchestrator/parameters.json` | GitHub PAT, repo info, OpenAI deployment name |
| `platform/parameters.json` | PostgreSQL creds, GitHub OAuth secrets, JWT secret, encryption key |

> **Security note:** `parameters.json` files contain secrets and are git-ignored. Only `parameters.example.json` files are committed to the repository.

## CI/CD via GitHub Actions

PRism ships two deployment workflows in `.github/workflows/`:

- **`deploy-azure.yml`** — Deploys the orchestrator on push to `main`/`develop` (when `agents/`, `foundry/`, or `mcp_servers/` change) or on manual `workflow_dispatch`
- **`deploy-platform.yml`** — Deploys the platform on matching push or manual trigger

Both workflows use `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` repository secrets.

## Known Issues

- **PostgreSQL in eastus2**: Some subscriptions have `LocationIsOfferRestricted` for PostgreSQL in `eastus2`. Use `eastus` or `centralus` instead.
