# PRism — Client / Vendor Setup Guide

**What this document covers:** A complete walkthrough for engineering teams who want to deploy PRism as their pre-deployment risk gate. Covers local dev, Azure resource provisioning, CI/CD integration, VS Code extension, and production deployment.

---

## Table of Contents

1. [What Is PRism?](#1-what-is-prism)  
2. [Architecture Overview](#2-architecture-overview)  
3. [Prerequisites](#3-prerequisites)  
4. [Quick Start (5 Minutes — No Azure)](#4-quick-start-5-minutes--no-azure)  
5. [Azure Resource Provisioning](#5-azure-resource-provisioning)  
6. [Environment Variables Reference](#6-environment-variables-reference)  
7. [GitHub Actions Integration (PR Comments)](#7-github-actions-integration-pr-comments)  
8. [VS Code Extension](#8-vs-code-extension)  
9. [Production Deployment (Azure Container Apps)](#9-production-deployment-azure-container-apps)  
10. [Production: Managed Identity (Zero API Keys)](#10-production-managed-identity-zero-api-keys)  
11. [Verifying the Setup](#11-verifying-the-setup)  
12. [FAQ / Troubleshooting](#12-faq--troubleshooting)

---

## 1\. What Is PRism?

PRism is an **AI-powered pre-deployment risk gate** that analyzes every pull request before it ships. It runs 4 specialist agents in parallel:

| Agent | What It Checks | Example Finding |
| :---- | :---- | :---- |
| **Diff Analyst** | Code changes — removed safety nets, risky patterns | "Retry logic removed from payment\_service.py" |
| **History Agent** | Past incidents on the same files | "payment\_service.py caused 3 P1 incidents in the last 90 days" |
| **Coverage Agent** | Test coverage impact of the PR | "Net coverage delta: −12% — tests were deleted" |
| **Timing Agent** | Deploy window risk (Friday 5 PM, holidays, etc.) | "Deployment scheduled for Friday 4:50 PM — high incident window" |

These agent scores are weighted and combined into a **Deployment Confidence Score (0–100)**:

- **≥ 70** → ✅ Greenlight — safe to deploy  
- **\< 70** or any agent returns `critical` → 🚫 Blocked — risk brief \+ rollback playbook generated

PRism surfaces results in **three places**:

1. **GitHub PR comment** — automated via GitHub Actions  
2. **VS Code sidebar** — live risk assessment in the IDE  
3. **API endpoint** — programmatic access for custom integrations

---

## 2\. Architecture Overview

┌──────────────────────────────────────────────────┐

│                 GitHub Repository                │

│   PR opened / updated ──► GitHub Actions fires   │

└────────────────────────┬─────────────────────────┘

                         │ POST /analyze

                         ▼

┌──────────────────────────────────────────────────┐

│              PRism FastAPI Backend                │

│         (Azure Container Apps or local)           │

│                                                  │

│  ┌────────────┐ ┌──────────────┐ ┌────────────┐ │

│  │Diff Analyst│ │History Agent │ │Coverage Agt│ │

│  └─────┬──────┘ └──────┬───────┘ └─────┬──────┘ │

│        │    ┌──────────┐│               │        │

│        │    │Timing Agt││               │        │

│        │    └─────┬────┘│               │        │

│        └──────┬───┴─────┴───────────────┘        │

│               ▼                                  │

│        ┌─────────────┐                           │

│        │Verdict Agent│ ── Azure OpenAI (optional)│

│        └─────┬───────┘                           │

│              │                                   │

│  ┌───────────┴────────────────────────────────┐  │

│  │ Foundry Governance (optional)              │  │

│  │  ∘ Content Safety  ∘ Tracing  ∘ Guardrails │  │

│  └────────────────────────────────────────────┘  │

└────────────────────────┬─────────────────────────┘

                         │ VerdictReport JSON

              ┌──────────┼──────────┐

              ▼          ▼          ▼

        PR Comment   VS Code    API Consumer

                     Sidebar

---

## 3\. Prerequisites

| Requirement | Minimum Version | Purpose |
| :---- | :---- | :---- |
| Python | 3.12+ | Backend server |
| pip | Latest | Package management |
| Git | 2.x | Clone repo, detect branches |
| Node.js | 18+ | VS Code extension build (optional) |
| Azure CLI | 2.x | Azure resource provisioning (optional) |
| Docker | 20+ | Container deployment (optional) |

**Optional (for full Azure integration):**

- An Azure subscription with permissions to create resources  
- A GitHub account with repo admin access (for Actions workflows)

---

## 4\. Quick Start (5 Minutes — No Azure)

PRism is designed to work **without any Azure resources**. All Azure features degrade gracefully — the pipeline never crashes due to missing credentials.

### 4.1 — Clone and Install

git clone https://github.com/soni-ishan/PRism.git

cd PRism

pip install \-r requirements.txt

### 4.2 — Create a Minimal `.env`

\# Only needed if you want live GitHub data fetching

GITHUB\_TOKEN=ghp\_your\_token\_here

That's it. No Azure credentials required for local dev.

### 4.3 — Start the Server

uvicorn agents.orchestrator.server:app \--reload \--port 8000

### 4.4 — Test It

\# Health check

curl http://localhost:8000/health

\# → {"status": "ok", "service": "prism"}

\# Run an analysis

curl \-X POST http://localhost:8000/analyze \\

  \-H "Content-Type: application/json" \\

  \-d '{

    "pr\_number": 46,

    "repo": "your-org/your-repo",

    "changed\_files": \["payment\_service.py", "utils/retry.py"\],

    "diff": "- retry\_count=3\\n+ pass",

    "timestamp": "2026-03-07T16:50:00Z"

  }'

You'll get a full `VerdictReport` with a confidence score, decision, risk brief, and per-agent results. The LLM-enhanced risk brief will use a deterministic template (no Azure OpenAI), but the scoring is fully functional.

### What Works Without Azure

| Feature | Without Azure | With Azure |
| :---- | :---- | :---- |
| 4-agent parallel analysis | ✅ Full | ✅ Full |
| Confidence scoring | ✅ Full | ✅ Full |
| Greenlight/Blocked decision | ✅ Full | ✅ Full |
| Risk brief | ✅ Deterministic template | ✅ LLM-enhanced (GPT-4o) |
| Rollback playbook | ✅ Deterministic template | ✅ LLM-enhanced |
| Content safety filtering | ⬜ Skipped | ✅ Azure Content Safety |
| Observability / tracing | ⬜ Skipped | ✅ Application Insights |
| Audit trail | ⬜ Skipped | ✅ AI Foundry evaluation API |
| History Agent (past incidents) | ✅ Mock data | ✅ Azure AI Search |

---

## 5\. Azure Resource Provisioning

**Time estimate:** \~20 minutes for manual setup. Under 5 minutes with Bicep/IaC.

### 5.1 — Resource Summary

| \# | Resource | SKU / Pricing | What It Enables |
| :---- | :---- | :---- | :---- |
| 1 | **Azure OpenAI** | Pay-as-you-go (\~$0.15/1M tokens for gpt-4o-mini) | LLM-enhanced risk briefs and rollback playbooks |
| 2 | **Azure AI Foundry Project** | Free (hub/container) | Central project client, audit trail, evaluation API |
| 3 | **Azure Content Safety** | Free tier: 5K txns/month | Filters harmful/hallucinated LLM output |
| 4 | **Application Insights** | Free tier: 5 GB/month | Per-agent latency tracing, live Foundry dashboard |
| 5 | **Azure AI Search** | Free tier: 50 MB | History Agent incident lookup |
| 6 | **Azure Container Apps** | Pay-per-use, scale-to-zero | Production hosting for the FastAPI backend |
| 7 | **Azure Container Registry** | Basic \~$5/month | Store Docker images for ACA |

**Estimated monthly cost for a small team:** $5–15/month (mostly Container Registry \+ minimal OpenAI token usage). Scale-to-zero ACA means you pay nothing when idle.

### 5.2 — Manual Provisioning (Azure Portal)

#### Step A: Azure CLI Login

az login

\# This enables DefaultAzureCredential for all local development

#### Step B: Create a Resource Group

az group create \--name rg-prism \--location eastus

#### Step C: Azure OpenAI

1. Go to [portal.azure.com](https://portal.azure.com) → Create → "Azure OpenAI"  
2. Choose a region with GPT-4o availability (East US, Sweden Central)  
3. Once created: Keys and Endpoint → copy **Endpoint** and **Key 1**  
4. Deploy a model: Azure AI Foundry Studio → Deployments → deploy `gpt-4o-mini`  
5. Note the **deployment name** you chose

#### Step D: AI Foundry Project

1. Go to [ai.azure.com](https://ai.azure.com) → Create a project  
2. Once created: Project settings → copy **Project connection string**  
3. Connect your Azure OpenAI resource under Settings → Connected resources

#### Step E: Content Safety

1. Azure Portal → Create → "Content Safety"  
2. Choose same region as OpenAI  
3. Copy the **Endpoint** and **Key**

#### Step F: Application Insights

1. Azure Portal → Create → "Application Insights" (Workspace-based)  
2. Copy the **Connection String** from Overview

#### Step G: Azure AI Search (for History Agent)

1. Azure Portal → Create → "Azure AI Search"  
2. Free tier is sufficient for development  
3. Copy the **Endpoint** and **Admin Key**

### 5.3 — Automated Provisioning (Bicep / IaC)

For teams managing infrastructure as code, a Bicep template can provision all resources in one command. Below is the conceptual command — the actual template would live in your IaC repo.

az deployment group create \\

  \--resource-group rg-prism \\

  \--template-file infra/main.bicep \\

  \--parameters environmentName=prod location=eastus

A production Bicep template would create:

- Azure OpenAI with a `gpt-4o-mini` deployment  
- AI Foundry project connected to the OpenAI resource  
- Content Safety resource  
- Application Insights \+ Log Analytics workspace  
- Azure AI Search (free tier)  
- Container Apps Environment \+ Container App  
- Container Registry  
- Managed Identity with role assignments (see Section 10\)

---

## 6\. Environment Variables Reference

### 6.1 — Complete `.env` File

\# ─── Required for live GitHub data ───

GITHUB\_TOKEN=ghp\_xxxxxxxxxxxx

\# ─── Azure OpenAI (LLM-enhanced risk briefs) ───

AZURE\_OPENAI\_ENDPOINT=https://your-resource.openai.azure.com/

AZURE\_OPENAI\_API\_KEY=your-key-here

AZURE\_OPENAI\_DEPLOYMENT=gpt-4o-mini

\# ─── Azure AI Foundry (audit trail, evaluation) ───

AZURE\_FOUNDRY\_PROJECT\_CONNECTION\_STRING=your-connection-string

\# ─── Azure Content Safety (LLM output filtering) ───

AZURE\_CONTENT\_SAFETY\_ENDPOINT=https://your-cs.cognitiveservices.azure.com/

AZURE\_CONTENT\_SAFETY\_KEY=your-key-here

\# ─── Application Insights (tracing) ───

APPLICATIONINSIGHTS\_CONNECTION\_STRING=InstrumentationKey=xxx;IngestionEndpoint=xxx

\# ─── Azure AI Search (History Agent) ───

AZURE\_AI\_SEARCH\_ENDPOINT=https://your-search.search.windows.net

AZURE\_AI\_SEARCH\_KEY=your-admin-key

\# ─── Webhook Security (optional) ───

GITHUB\_WEBHOOK\_SECRET=your-webhook-secret

\# ─── Service Principal Auth (if not using az login) ───

AZURE\_TENANT\_ID=

AZURE\_CLIENT\_ID=

AZURE\_CLIENT\_SECRET=

### 6.2 — Which Vars Are Actually Required?

**None of them are strictly required.** The system works with zero env vars — every feature degrades gracefully:

| Variable | If Missing | Impact |
| :---- | :---- | :---- |
| `GITHUB_TOKEN` | Can't fetch live PR diffs | Must provide diff in request body |
| `AZURE_OPENAI_*` | LLM enhancement skipped | Deterministic risk brief template used |
| `AZURE_FOUNDRY_*` | Foundry client returns `None` | Audit trail and evaluation skipped |
| `AZURE_CONTENT_SAFETY_*` | Content safety returns safe-by-default | No LLM output filtering |
| `APPLICATIONINSIGHTS_*` | Tracing is a no-op | No latency dashboards |
| `AZURE_AI_SEARCH_*` | History Agent uses mock data | No real incident lookups |
| `GITHUB_WEBHOOK_SECRET` | Signature check skipped | Webhook accepts all requests (dev mode) |

### 6.3 — "14 Env Vars Seems Like a Lot"

This is a common concern. Here's the reality:

- **For local dev:** You need **1** env var (`GITHUB_TOKEN`). Everything else falls back gracefully.  
- **For a basic deployment:** You need **4** env vars (`GITHUB_TOKEN` \+ the 3 `AZURE_OPENAI_*` vars) to get LLM-enhanced risk briefs.  
- **For full production:** You use **Managed Identities** (see Section 10\) — which means **zero API keys** in your environment. Azure RBAC handles authentication automatically.

The 14 vars exist purely for maximum flexibility and local development convenience.

---

##  7\. GitHub Actions Integration (PR Comments)

This is how PRism automatically comments on every PR with a risk assessment.

### 7.1 — Add the Workflow File

Create `.github/workflows/prism-gate.yml` in your repository:

name: PRism Risk Gate

on:

  pull\_request:

    types: \[opened, synchronize, reopened\]

jobs:

  prism-analysis:

    runs-on: ubuntu-latest

    permissions:

      pull-requests: write

      statuses: write

    steps:

      \- uses: actions/checkout@v4

        with:

          fetch-depth: 0

      \- name: Get PR diff

        id: diff

        run: |

          DIFF=$(gh pr diff ${{ github.event.pull\_request.number }} \--repo ${{ github.repository }})

          echo "diff\<\<EOF" \>\> $GITHUB\_OUTPUT

          echo "$DIFF" \>\> $GITHUB\_OUTPUT

          echo "EOF" \>\> $GITHUB\_OUTPUT

        env:

          GH\_TOKEN: ${{ secrets.GITHUB\_TOKEN }}

      \- name: Get changed files

        id: files

        run: |

          FILES=$(gh pr view ${{ github.event.pull\_request.number }} \--json files \-q '.files\[\].path' \--repo ${{ github.repository }})

          echo "files=$FILES" \>\> $GITHUB\_OUTPUT

        env:

          GH\_TOKEN: ${{ secrets.GITHUB\_TOKEN }}

      \- name: Call PRism API

        id: prism

        run: |

          RESPONSE=$(curl \-s \-X POST "${{ vars.PRISM\_API\_URL }}/analyze" \\

            \-H "Content-Type: application/json" \\

            \-d '{

              "pr\_number": ${{ github.event.pull\_request.number }},

              "repo": "${{ github.repository }}",

              "changed\_files": ${{ steps.files.outputs.files }},

              "diff": ${{ toJSON(steps.diff.outputs.diff) }},

              "timestamp": "'$(date \-u \+%Y-%m-%dT%H:%M:%SZ)'"

            }')

          echo "response=$RESPONSE" \>\> $GITHUB\_OUTPUT

      \- name: Post PR Comment

        run: |

          SCORE=$(echo '${{ steps.prism.outputs.response }}' | jq \-r '.confidence\_score')

          DECISION=$(echo '${{ steps.prism.outputs.response }}' | jq \-r '.decision')

          BRIEF=$(echo '${{ steps.prism.outputs.response }}' | jq \-r '.risk\_brief')

          gh pr comment ${{ github.event.pull\_request.number }} \\

            \--repo ${{ github.repository }} \\

            \--body "\#\# 🔬 PRism Deployment Risk Assessment

          \*\*Confidence Score:\*\* $SCORE / 100

          \*\*Decision:\*\* $DECISION

          $BRIEF"

        env:

          GH\_TOKEN: ${{ secrets.GITHUB\_TOKEN }}

      \- name: Set commit status

        run: |

          DECISION=$(echo '${{ steps.prism.outputs.response }}' | jq \-r '.decision')

          if \[ "$DECISION" \= "greenlight" \]; then

            STATE="success"

            DESC="PRism: Deploy approved"

          else

            STATE="failure"

            DESC="PRism: Deploy blocked"

          fi

          gh api repos/${{ github.repository }}/statuses/${{ github.event.pull\_request.head.sha }} \\

            \-f state="$STATE" \-f description="$DESC" \-f context="PRism Risk Gate"

        env:

          GH\_TOKEN: ${{ secrets.GITHUB\_TOKEN }}

### 7.2 — Configure Repository Variables

Go to your repo → Settings → Secrets and variables → Actions → **Variables** tab:

| Variable | Value | Example |
| :---- | :---- | :---- |
| `PRISM_API_URL` | Your PRism backend URL | `https://prism-api.blueocean-abc123.eastus.azurecontainerapps.io` |

`GITHUB_TOKEN` is automatically provided by GitHub Actions — no configuration needed.

### 7.3 — What the PR Comment Looks Like

\#\# 🔬 PRism Deployment Risk Assessment

\*\*Confidence Score:\*\* 21 / 100

\*\*Decision:\*\* blocked

\*\*Risk Brief:\*\*

\- Diff Analyst: Retry logic removed from payment\_service.py — critical safety net degradation

\- History Agent: payment\_service.py caused 3 P1 incidents in the last 90 days

\- Coverage Agent: Net coverage delta: −12% — test file deleted

\- Timing Agent: Friday 4:50 PM deployment — historically high incident window

\*\*Recommendation:\*\* Delay deployment. Add retry logic back, restore test coverage.

A commit status check is also set, which can be made **required** in branch protection rules to physically prevent merging blocked PRs.

---

## 8\. VS Code Extension

The PRism VS Code extension provides a **sidebar panel** showing the Deployment Confidence Score, per-agent findings, and status badges — directly in the IDE.

### 8.1 — Install from Source (Development)

cd vscode\_extension

npm install

npm run compile

Then press **F5** in VS Code to launch the Extension Development Host.

### 8.2 — Install from VSIX (Distribution)

cd vscode\_extension

npx \--no-install vsce package

\# This creates prism-risk-gate-0.1.0.vsix

code \--install-extension prism-risk-gate-0.1.0.vsix

### 8.3 — Configuration

Open VS Code Settings (Ctrl+,) and search for "PRism":

| Setting | Default | Description |
| :---- | :---- | :---- |
| `prism.serverUrl` | `http://localhost:8000` | URL of the PRism FastAPI backend. Change to your ACA endpoint for production. |
| `prism.autoRefresh` | `true` | Automatically re-analyze when the active branch changes. |
| `prism.refreshIntervalSeconds` | `30` | How often to poll the backend (in seconds). |

### 8.4 — Features

- **Activity bar icon** — click the PRism prism icon in the left sidebar  
- **Score gauge** — color-coded circle (green ≥70, yellow 40-69, red \<40)  
- **Agent cards** — each agent shows status badge, score modifier, findings  
- **Re-run button** — manually trigger a fresh analysis  
- **Full Report panel** — opens a detailed report in a new editor tab  
- **Mock mode** — if the backend is unreachable, the extension falls back to mock data so you can still see the UX

---

## 9\. Production Deployment (Azure Container Apps)

### 9.1 — Why Azure Container Apps?

- **Scale-to-zero** — no cost when idle (perfect for PR-driven workloads)  
- **Serverless containers** — no VM management, no Kubernetes complexity  
- **Built-in HTTPS** — automatic TLS certificates  
- **Azure RBAC \+ Managed Identity** — zero API keys in production  
- **Qualifies for Azure Integration Prize** — deep Azure service integration

### 9.2 — Build the Docker Image

\# From the repo root

docker build \-t prism-api .

docker run \-p 8000:8000 \--env-file .env prism-api

curl http://localhost:8000/health

### 9.3 — Deploy to Azure

\# 1\. Create a Container Registry

az acr create \\

  \--name prismacr \\

  \--resource-group rg-prism \\

  \--sku Basic \\

  \--admin-enabled true

\# 2\. Build and push the image (ACR Tasks — no local Docker needed)

az acr build \--registry prismacr \--image prism-api:latest .

\# 3\. Create a Container Apps environment

az containerapp env create \\

  \--name prism-env \\

  \--resource-group rg-prism \\

  \--location eastus

\# 4\. Deploy the container app

az containerapp create \\

  \--name prism-api \\

  \--resource-group rg-prism \\

  \--environment prism-env \\

  \--image prismacr.azurecr.io/prism-api:latest \\

  \--registry-server prismacr.azurecr.io \\

  \--target-port 8000 \\

  \--ingress external \\

  \--min-replicas 0 \\

  \--max-replicas 3 \\

  \--env-vars \\

    AZURE\_OPENAI\_ENDPOINT=secretref:openai-endpoint \\

    AZURE\_OPENAI\_API\_KEY=secretref:openai-key \\

    AZURE\_OPENAI\_DEPLOYMENT=gpt-4o-mini \\

    GITHUB\_TOKEN=secretref:github-token \\

    APPLICATIONINSIGHTS\_CONNECTION\_STRING=secretref:appinsights

### 9.4 — Get Your Public URL

az containerapp show \\

  \--name prism-api \\

  \--resource-group rg-prism \\

  \--query "properties.configuration.ingress.fqdn" \\

  \--output tsv

\# → prism-api.blueocean-abc123.eastus.azurecontainerapps.io

Use this URL as:

- `PRISM_API_URL` in GitHub Actions variables  
- `prism.serverUrl` in the VS Code extension settings

---

## 10\. Production: Managed Identity (Zero API Keys)

**This is the key answer to "how do you handle 14 env vars in production?"**

In production on Azure, you use **Managed Identities** and **RBAC role assignments** instead of API keys. The Container App gets an identity that Azure trusts — no secrets to manage, rotate, or leak.

### 10.1 — Enable System-Assigned Managed Identity

az containerapp identity assign \\

  \--name prism-api \\

  \--resource-group rg-prism \\

  \--system-assigned

### 10.2 — Assign RBAC Roles

\# Get the identity's principal ID

IDENTITY=$(az containerapp show \\

  \--name prism-api \\

  \--resource-group rg-prism \\

  \--query "identity.principalId" \\

  \--output tsv)

\# Azure OpenAI — allow the app to call the model

az role assignment create \\

  \--assignee $IDENTITY \\

  \--role "Cognitive Services OpenAI User" \\

  \--scope /subscriptions/\<sub\>/resourceGroups/rg-prism/providers/Microsoft.CognitiveServices/accounts/\<openai-resource\>

\# Content Safety — allow the app to run safety checks

az role assignment create \\

  \--assignee $IDENTITY \\

  \--role "Cognitive Services User" \\

  \--scope /subscriptions/\<sub\>/resourceGroups/rg-prism/providers/Microsoft.CognitiveServices/accounts/\<content-safety-resource\>

\# AI Search — allow the app to query the index

az role assignment create \\

  \--assignee $IDENTITY \\

  \--role "Search Index Data Reader" \\

  \--scope /subscriptions/\<sub\>/resourceGroups/rg-prism/providers/Microsoft.Search/searchServices/\<search-resource\>

\# Application Insights — allow the app to send telemetry

az role assignment create \\

  \--assignee $IDENTITY \\

  \--role "Monitoring Metrics Publisher" \\

  \--scope /subscriptions/\<sub\>/resourceGroups/rg-prism/providers/Microsoft.Insights/components/\<appinsights-resource\>

### 10.3 — What This Means for Env Vars

With Managed Identity, your production environment needs **only these non-secret env vars**:

AZURE\_OPENAI\_ENDPOINT=https://your-resource.openai.azure.com/

AZURE\_OPENAI\_DEPLOYMENT=gpt-4o-mini

AZURE\_FOUNDRY\_PROJECT\_CONNECTION\_STRING=your-connection-string

AZURE\_CONTENT\_SAFETY\_ENDPOINT=https://your-cs.cognitiveservices.azure.com/

APPLICATIONINSIGHTS\_CONNECTION\_STRING=InstrumentationKey=xxx;IngestionEndpoint=xxx

AZURE\_AI\_SEARCH\_ENDPOINT=https://your-search.search.windows.net

**Notice: zero API keys.** `DefaultAzureCredential` automatically detects the Managed Identity and authenticates with Azure RBAC. The `AZURE_OPENAI_API_KEY`, `AZURE_CONTENT_SAFETY_KEY`, `AZURE_AI_SEARCH_KEY`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` env vars are all **unnecessary** in production.

### 10.4 — Summary: Dev vs Production Auth

| Environment | Auth Method | API Keys Needed | Env Vars |
| :---- | :---- | :---- | :---- |
| **Local dev** | `az login` → `DefaultAzureCredential` | Optional (for convenience) | 1 required (`GITHUB_TOKEN`), rest optional |
| **CI/CD** | GitHub Actions secrets | 2–4 secrets | Stored in GitHub, not in code |
| **Production (ACA)** | Managed Identity → RBAC | **Zero** | Only endpoints \+ deployment names (non-secret) |

---

## 11\. Verifying the Setup

### 11.1 — Health Check

curl https://your-prism-url/health

\# Expected: {"status": "ok", "service": "prism"}

### 11.2 — Run an Analysis

curl \-X POST https://your-prism-url/analyze \\

  \-H "Content-Type: application/json" \\

  \-d '{

    "pr\_number": 1,

    "repo": "your-org/your-repo",

    "changed\_files": \["src/main.py"\],

    "diff": "- old\_code\\n+ new\_code",

    "timestamp": "2026-03-10T10:00:00Z"

  }'

Expected response:

{

  "confidence\_score": 67,

  "decision": "blocked",

  "risk\_brief": "...",

  "rollback\_playbook": "...",

  "agent\_results": \[

    {"agent\_name": "Diff Analyst", "risk\_score\_modifier": 20, "status": "warning", ...},

    {"agent\_name": "History Agent", "risk\_score\_modifier": 15, "status": "warning", ...},

    {"agent\_name": "Coverage Agent", "risk\_score\_modifier": 10, "status": "pass", ...},

    {"agent\_name": "Timing Agent", "risk\_score\_modifier": 0, "status": "pass", ...}

  \]

}

### 11.3 — VS Code Extension

1. Set `prism.serverUrl` to your ACA endpoint  
2. Open a Git repository in VS Code  
3. Click the PRism icon in the activity bar  
4. You should see the score gauge, agent cards, and findings

### 11.4 — GitHub Actions

1. Add `PRISM_API_URL` as a repository variable  
2. Open a new PR  
3. The `prism-gate.yml` workflow should fire  
4. A PR comment should appear with the risk assessment  
5. A commit status check should appear (success or failure)

---

## 12\. FAQ / Troubleshooting

### Q: Do I need all the Azure resources to use PRism?

**No.** PRism works with zero Azure resources — all features degrade gracefully. Start with the Quick Start (Section 4\) and add Azure resources incrementally as needed.

### Q: What if I only want the GitHub PR comment, not the VS Code extension?

That's fine — each delivery channel is independent. Just set up the GitHub Actions workflow (Section 7\) and skip the VS Code extension.

### Q: Can I use a different LLM provider instead of Azure OpenAI?

The core scoring engine (confidence score, greenlight/blocked decision) does **not** use any LLM. LLMs are only used for enhancing the human-readable risk brief and rollback playbook. To swap providers, modify `foundry/deployment_config/__init__.py` — specifically the `get_instrumented_openai_client()` function.

### Q: How do I add my own incident history for the History Agent?

The History Agent queries Azure AI Search for past incidents. To populate it:

1. Create an index in your Azure AI Search resource  
2. Upload your incident documents (JSON format with fields: `file_path`, `severity`, `description`, `date`)  
3. Set `AZURE_AI_SEARCH_ENDPOINT` and `AZURE_AI_SEARCH_KEY` in your environment

If you don't have an AI Search resource, the History Agent will use mock data or return a fallback result.

### Q: Can I customize the agent weights?

Yes. Edit `agents/orchestrator/__init__.py` — the `AGENT_WEIGHTS` dictionary:

AGENT\_WEIGHTS \= {

    "Diff Analyst": 0.30,   \# Code change risk

    "History Agent": 0.25,  \# Past incident risk

    "Coverage Agent": 0.25, \# Test coverage impact

    "Timing Agent": 0.20,   \# Deploy window risk

}

Weights must sum to 1.0.

### Q: The VS Code extension shows "Server unreachable — showing mock data"

This means the extension can't reach the backend at the configured `prism.serverUrl`. Check:

1. Is the backend running? (`curl http://localhost:8000/health`)  
2. Is the URL correct in VS Code settings?  
3. If using ACA, is the container app running? (`az containerapp show ...`)

### Q: How do I make the PRism status check a required check for merging?

1. Go to your repo → Settings → Branches → Branch protection rules  
2. Edit the rule for your main branch  
3. Under "Require status checks to pass before merging", add **"PRism Risk Gate"**  
4. Now PRs with a `blocked` verdict cannot be merged

---

## Summary: Setup Complexity by Tier

| Tier | Time | What You Get | What You Need |
| :---- | :---- | :---- | :---- |
| **Local dev** | 5 min | Full scoring engine, mock data, API | Python \+ `pip install` |
| **\+ GitHub Actions** | 15 min | Automated PR comments \+ status checks | Add 1 workflow file \+ 1 repo variable |
| **\+ Azure OpenAI** | 25 min | LLM-enhanced risk briefs \+ playbooks | 3 env vars |
| **\+ Full Azure** | 45 min | Tracing, content safety, audit trail, AI Search | All env vars (or Bicep one-liner) |
| **Production (ACA)** | 1 hr | Scale-to-zero hosting, zero API keys, HTTPS | Managed Identity \+ RBAC (Section 10\) |

PRism scales from a **zero-config local tool** to a **fully governed enterprise deployment** — incrementally, with no breaking changes at any step.  
