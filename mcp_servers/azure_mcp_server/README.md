# Azure MCP Server — Incident Ingestion & Query

Ingests exception logs from Azure Log Analytics into an Azure AI Search index (`incidents`), which the History Agent queries at PR analysis time.

## Architecture

```
Log Analytics (exceptions table)
        │  KQL query
        ▼
   ingest.py ─── fetch_exceptions()
        │
        ▼
   extract_files()          ← Azure OpenAI (or regex fallback)
        │
        ▼
   push_incident()          → Azure AI Search "incidents" index
        │
        ▼
   History Agent reads at PR time (query.py)
```

## Module layout

| File | Role |
|------|------|
| `setup.py` | Creates the `incidents` index schema in AI Search |
| `ingest.py` | Fetches exceptions, extracts file paths, pushes incident docs (write-only) |
| `query.py` | Searches incidents by file path or free text (read-only) |
| `function_app.py` | Azure Function triggers (Event Grid, Timer, HTTP) |
| `mcp_server.py` | Facade class used by the History Agent |
| `sample_data.py` | Uploads hardcoded test incidents for dev/demo |

## Azure Function triggers

`function_app.py` exposes three triggers:

| Trigger | Type | Description |
|---------|------|-------------|
| `ingest_from_monitor_alert` | Event Grid | Fires on Azure Monitor alert; calls `ingest_from_alert()` |
| `ingest_logs_timer` | Timer (every 10 min) | Pulls recent exceptions; calls `ingest_from_logs()` |
| `ingest_logs_http` | HTTP POST `/api/ingest/logs` | Manual/backfill endpoint |

## Environment variables

### Required

| Variable | Description |
|----------|-------------|
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint (e.g. `https://prism-search.search.windows.net`) |
| `AZURE_LOG_WORKSPACE_ID` | Log Analytics workspace GUID |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_SEARCH_KEY` | — | Admin key; if omitted, uses `DefaultAzureCredential` |
| `AZURE_INGEST_WINDOW_MINUTES` | `30` | KQL query window around fired time |
| `AZURE_OPENAI_ENDPOINT` | — | Enables LLM-based file extraction from stack traces |
| `AZURE_OPENAI_DEPLOYMENT` | — | Azure OpenAI model deployment name |

## Local setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create the AI Search index

```bash
python -m mcp_servers.azure_mcp_server.setup
```

Pass `--recreate` to drop and rebuild the index.

### 3. (Optional) Load sample data

```bash
python -m mcp_servers.azure_mcp_server.sample_data
```

Uploads 6 test incidents so you can verify the index and History Agent work end-to-end without a live Log Analytics workspace.

### 4. Run a real ingestion

```bash
python -m mcp_servers.azure_mcp_server.ingest \
  --workspace-id <LOG_ANALYTICS_WORKSPACE_GUID> \
  --fired-time 2026-03-07T12:00:00Z \
  --window-minutes 30
```

This queries the `exceptions` table for rows where `severityLevel >= 3`, extracts source file paths from stack traces, and pushes structured incident documents to AI Search.

## Production deployment

Deploy `function_app.py` as a Python Azure Function App. Assign its managed identity:

| Role | Scope |
|------|-------|
| **Log Analytics Reader** | Log Analytics workspace |
| **Search Index Data Contributor** | Azure AI Search resource |
| **Cognitive Services User** | Azure OpenAI resource (only if using LLM extraction) |

The timer trigger runs every 10 minutes. Event Grid triggers fire in real time on Monitor alerts. The HTTP trigger is available for on-demand backfills.
