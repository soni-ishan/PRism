# Azure MCP Server Ingestion

This module now supports an Azure-native ingestion pipeline via Azure Functions.

## What runs where

- `agents/history_agent/*`: **read-only query path** during PR analysis
- `mcp_servers/azure_mcp_server/function_app.py`: **write/ingestion path**
  - pulls exception logs from Log Analytics/App Insights
  - extracts source files (Azure OpenAI, with regex fallback)
  - upserts incidents into Azure AI Search (`incidents` index)

## Azure Function triggers

`mcp_servers/azure_mcp_server/function_app.py` exposes 3 triggers:

1. `ingest_from_monitor_alert` (Event Grid)
   - Triggered by Azure Monitor alert events
   - Calls `ingest_from_alert(...)`

2. `ingest_logs_timer` (Timer, every 10 minutes)
   - Pulls recent exceptions from Log Analytics
   - Calls `ingest_from_logs(...)`

3. `ingest_logs_http` (HTTP POST)
   - Manual/backfill endpoint for ops
   - Route: `/api/ingest/logs`

## Required environment variables

- `AZURE_LOG_WORKSPACE_ID` - Log Analytics workspace ID
- `AZURE_RESOURCE_NAME` - service name (`cloud_RoleName`) for timer/http default
- `AZURE_SEARCH_ENDPOINT` - Azure AI Search endpoint
- `AZURE_SEARCH_KEY` - optional; if omitted uses Managed Identity
- `AZURE_INGEST_WINDOW_MINUTES` - optional, default `30`

Optional (for higher quality file extraction):

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`

## Local run flow

1. Install deps:
   - `pip install -r requirements.txt`
2. Set env vars above
3. Run ingest directly (without Functions host):
   - `python -m mcp_servers.azure_mcp_server.ingest --workspace-id <id> --resource-name <name> --fired-time 2026-03-04T12:00:00Z`

## Deploy shape (recommended)

- Deploy `mcp_servers/azure_mcp_server/function_app.py` as Python Azure Function App
- Assign Function App managed identity at least:
  - Log Analytics Reader (workspace)
  - Search Index Data Contributor (Azure AI Search)
  - Cognitive Services User (if using Azure OpenAI with Entra auth)

## Runtime behavior

- Timer/Event Grid trigger executes ingest pipeline
- Pipeline writes incident docs to AI Search index `incidents`
- History Agent only reads from `incidents` during PR scoring
