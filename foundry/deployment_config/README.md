# Deployment Configuration — PRism

Separated into three folders by concern. Deploy in order:

## Prerequisites

- Azure CLI (`az`) logged in
- Docker Desktop running (for container builds)
- Fill in the `parameters.json` files (copy from `parameters.example.json` in each folder)

## Folder Structure

```
deployment_config/
  infra/              ← shared Azure resources (ACR, OpenAI, PG, etc.)
  orchestrator/       ← orchestrator container app + Dockerfile
  platform/           ← platform container app + Dockerfile
  generate-env.ps1    ← generate .env files from Azure resources
  cleanup.ps1         ← delete resource group + purge soft-deleted resources
```

## Step 1 — Shared Infrastructure

```powershell
cd infra
.\deploy.ps1                                 # uses parameters.json
.\deploy.ps1 -Location eastus               # override region
.\deploy.ps1 -ResourceGroupName rg-prism-staging
```

Creates: ACR, Container Apps Environment, PostgreSQL, OpenAI, AI Search,
Content Safety, Key Vault, Log Analytics, Application Insights, Managed Identities.

## Step 2 — Orchestrator Container App

```powershell
cd orchestrator
.\deploy.ps1                                 # builds Docker image + deploys
.\deploy.ps1 -SkipDocker                     # redeploy Bicep only (image already in ACR)
```

## Step 3 — Platform Container App

```powershell
cd platform
.\deploy.ps1                                 # builds Docker image + deploys
.\deploy.ps1 -SkipDocker                     # redeploy Bicep only
```

## Utilities

```powershell
# Generate .env files from deployed Azure resources
.\generate-env.ps1

# Delete everything (prompts for confirmation)
.\cleanup.ps1
.\cleanup.ps1 -Force                         # skip confirmation
```

## Parameter Files

Each folder has its own `parameters.json` (secrets, git-ignored) and `parameters.example.json` (template, committed):

| Folder | Contains |
|--------|----------|
| `infra/parameters.json` | Region, GitHub PAT, OpenAI config, PostgreSQL creds |
| `orchestrator/parameters.json` | GitHub PAT, repo info, model deployment name |
| `platform/parameters.json` | PostgreSQL creds, OAuth secrets, JWT, encryption key |

> **Security**: The `parameters.json` files contain secrets and are git-ignored.
> Only the `parameters.example.json` files are committed.

## Known Issues

- **PostgreSQL in eastus2**: Your subscription may have `LocationIsOfferRestricted`
  for PostgreSQL in `eastus2`. Try `eastus` or `centralus` as the location instead.
