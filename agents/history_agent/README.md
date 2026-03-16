<p align="center">
  <img src="../../vscode_extension/media/prism-icon.png" alt="PRism" width="80" />
</p>

# History Agent — Azure AI Search Integration

## Overview

The History Agent correlates PR file changes with **past production incidents** to assess deployment risk. It queries a per-repository Azure AI Search index populated from Azure Monitor / Log Analytics, returning a structured risk assessment that feeds into the Verdict Agent's Deployment Confidence Score.

## Architecture

```
┌─────────────────┐
│  Orchestrator   │
└────────┬────────┘
         │ changed_files: ["payment_service.py", "retry_handler.py"]
         ▼
┌─────────────────┐
│  History Agent  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Azure MCP      │
│  Server         │
└────────┬────────┘
         │
         ▼
┌──────────────────────────────┐
│  Azure AI Search             │  ← BM25 + semantic search
│  index: incidents-owner-repo │     scoped to this repo only
└──────────────────────────────┘
         ▲
         │ ingestion pipeline
┌──────────────────────────────┐
│  Azure Function App          │  ← Timer · Event Grid · HTTP triggers
│  ← Azure Log Analytics       │
└──────────────────────────────┘
```

## Data Flow

1. **Orchestrator** calls `HistoryAgent.analyze_pr(changed_files, repo_context)` with the list of changed files and the optional `RepoContext` (Azure workspace config linked during onboarding)
2. **History Agent** connects to the Azure MCP Server using the per-repo index name (`incidents-{owner}-{repo}`)
3. **Azure MCP Server** queries Azure AI Search:
   - Builds an OR-joined query from changed file names
   - Returns incidents ranked by BM25 relevance score
   - Returns up to 50 historical incidents per query
4. **History Agent** applies strict local matching (exact path, basename, or stem) to prevent false positives
5. Risk score computed and `AgentResult` returned to orchestrator

If no Azure workspace is linked for the repo, the agent returns `risk_score_modifier: 0` with status `"pass"` and a note that no deployment history is connected.

## Quick Start

### 1. Deploy Azure Resources

Run the unified deploy script from the repo root:

```powershell
az login
cd foundry/deployment_config/infra
.\deploy.ps1 -SubscriptionId <sub-id> -ResourceGroupName <rg-name>
```

This creates Azure AI Search, Azure Function App, Log Analytics workspace, and all required RBAC roles.

See [`foundry/deployment_config/README.md`](../../foundry/deployment_config/README.md) for details.

### 2. Initialize the Search Index and Sample Data

```powershell
python -m mcp_servers.azure_mcp_server.setup
```

This:
- Creates the `incidents-{owner}-{repo}` index (if it doesn't exist)
- Uploads 8 sample incidents for testing

### 3. Test the History Agent

```powershell
python agents\history_agent\agent.py payment_service.py
```

Expected output:
```
[HistoryAgent] ✅ Connected to Azure AI Search
[HistoryAgent] 🔍 Querying for incidents involving: ['payment_service.py']
[HistoryAgent] ✅ Found 4 incidents
{
  "agent_name": "History Agent",
  "risk_score_modifier": 50,
  "status": "warning",
  "findings": [
    "payment_service.py involved in 4 incident(s)",
    "  └─ 2026-02-24: Payment service timeout spike (high)",
    "  └─ 2026-02-20: Memory leak in payment retry loop (high)"
  ],
  "recommended_action": "CAUTION: This file has incident history..."
}
```

## Usage

### Standalone

```python
from agents.history_agent.agent import HistoryAgent

agent = HistoryAgent()
result = agent.analyze_pr(["payment_service.py"])
print(result)
```

### From Orchestrator (Async)

```python
from agents.history_agent.agent import run

result = await run(changed_files=["payment_service.py", "retry_handler.py"])
```

### Command Line

```powershell
# Single file
python agents\history_agent\agent.py payment_service.py

# Multiple files
python agents\history_agent\agent.py payment_service.py retry_handler.py database.py
```

## Azure AI Search Index Schema

One index is created per registered repository: `incidents-{owner}-{repo}`.

| Field | Type | Features | Description |
|---|---|---|---|
| `id` | String | Key | Unique incident ID (e.g., `INC-2026-0001`) |
| `title` | String | Searchable | Incident title / summary |
| `severity` | String | Filterable, Facetable | `low` · `medium` · `high` · `critical` |
| `timestamp` | String | Filterable, Sortable | ISO 8601 |
| `files_involved` | Collection | Searchable, Filterable | Files involved in the incident |
| `error_message` | String | Searchable | Error message or symptom |
| `root_cause` | String | Searchable | Root cause analysis |
| `affected_services` | Collection | Filterable | Services impacted |
| `duration_minutes` | Int32 | Filterable, Sortable | Incident duration |

## Risk Score Calculation

| Condition | Points |
|---|---|
| Per incident involving a changed file | +10 (capped at 50 per file) |
| 1 deployment today for this repo | +15 |
| 3+ deployments today | +30 |

**Status thresholds:** `pass` 0–39 · `warning` 40–69 · `critical` 70+

## Sample Incidents (loaded by setup script)

1. **Payment service timeout spike** (high) — `payment_service.py`
2. **Database migration deadlock** (critical) — `database.py`, `models/user.py`
3. **Memory leak in retry loop** (high) — `payment_service.py`
4. **Payment failures during peak** (critical) — `payment_service.py`, `retry_handler.py`
5. **Authentication bypass** (critical) — `auth_service.py`
6. **Silent error handling removal** (high) — `error_handler.py`, `payment_service.py`
7. **Cache invalidation failure** (medium) — `cache_manager.py`
8. **Friday evening deployment** (medium) — `checkout_flow.py`

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Failed to connect to Azure AI Search` | Missing/invalid credentials | Check `.env` for `AZURE_SEARCH_ENDPOINT` and `AZURE_SEARCH_KEY` |
| `No incidents found` | Empty index | Run `python -m mcp_servers.azure_mcp_server.setup` to load sample data |
| `Index 'incidents-...' was not found` | Index not created | The MCP server auto-creates on first connection; check `Search Service Contributor` RBAC role |
| `RuntimeError: Azure AI Search connection required` | Agent cannot connect (by design — no fallback) | Follow Quick Start steps above |
