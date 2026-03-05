# History Agent - Azure AI Search Integration

## Overview

The History Agent correlates PR file changes with past production incidents to assess deployment risk. It fetches **real incident data from Azure AI Search** using semantic search to find relevant historical incidents.

## Architecture

```
┌─────────────────┐
│  Orchestrator   │
└────────┬────────┘
         │ changed_files: ["payment_service.py"]
         ▼
┌─────────────────┐
│ History Agent   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Azure MCP       │
│ Server          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Azure AI Search │  ← Semantic search for incidents
│ (incidents idx) │     by file names
└─────────────────┘
```

## Data Flow

1. **Orchestrator** calls `HistoryAgent.analyze_pr(changed_files)` with list of changed files
2. **History Agent** connects to Azure MCP Server
3. **Azure MCP Server** queries Azure AI Search using semantic search:
   - Queries the `incidents` index with file names
   - Returns incidents ranked by relevance (scoring based on file matches)
   - Returns up to 50 historical incidents per query
4. **History Agent** correlates incidents with changed files and calculates risk
5. Returns standardized `AgentResult` back to orchestrator

## Quick Start

### 1. Deploy Azure Resources (Easiest)

Run the unified deploy script from the repo root:

```powershell
az login
./deploy/deploy.ps1 -SubscriptionId <sub-id> -ResourceGroupName <rg-name>
```

This will:
- Create Azure AI Search service
- Create Azure Function App for ingestion
- Auto-discover Log Analytics workspace and OpenAI
- Assign all RBAC roles
- Print your `.env` configuration

See [deploy/README.md](../../deploy/README.md) for details.

### 2. Configure Azure Credentials (after deploy script runs)

Run the setup script to create the index and upload sample incidents:

```powershell
python -m mcp_servers.azure_mcp_server.setup
```

This will:
- Connect to your Azure AI Search service (using `AZURE_SEARCH_ENDPOINT` from `.env`)
- Create the `incidents` index (if it doesn't exist)
- Upload 8 sample incidents for testing

### 3. Test the History Agent

```powershell
python agents\history_agent\agent.py payment_service.py
```

Expected output:
```
[HistoryAgent] ✅ Connected to Azure AI Search
[HistoryAgent] 🔍 Querying Azure AI Search for incidents involving: ['payment_service.py']
[HistoryAgent] ✅ Found 4 incidents from Azure AI Search
{
  "agent_name": "History Agent",
  "risk_score_modifier": 50,
  "status": "warning",
  "findings": [
    "payment_service.py involved in 4 incident(s) (50% of all incidents)",
    "  └─ 2026-02-24: Payment service timeout spike (high)",
    "  └─ 2026-02-20: Memory leak in payment retry loop (high)"
  ],
  "recommended_action": "CAUTION: This file has incident history..."
}
```

## Usage

### Standalone Testing

```python
from agents.history_agent.agent import HistoryAgent

# Automatically connects to Azure AI Search
agent = HistoryAgent()
result = agent.analyze_pr(["payment_service.py"])
print(result)
```

### From Orchestrator (Async Interface)

```python
from agents.history_agent.agent import run

# PRism standard interface - automatically uses Azure AI Search
result = await run(changed_files=["payment_service.py", "retry_handler.py"])
```

### Command Line

```powershell
# Single file
python agents\history_agent\agent.py payment_service.py

# Multiple files
python agents\history_agent\agent.py payment_service.py retry_handler.py database.py
```

## How Azure Semantic Search Works

The History Agent queries Azure AI Search with the changed file names:

```python
# Example: changed_files = ["payment_service.py", "retry_handler.py"]
query = "payment_service.py OR retry_handler.py"
results = azure_mcp.query_incidents_by_files_search(
    file_paths=changed_files,
    top_k=50
)
```

Azure AI Search:
1. **Full-text searches** the `files_involved` field across all incidents
2. **Ranks results** by relevance score (BM25 algorithm)
3. **Returns incidents** with metadata (severity, root cause, error messages, timestamps)

The agent then:
- Correlates each file with its incident history
- Calculates risk score based on incident frequency and severity
- Generates actionable recommendations based on historical patterns

## Azure AI Search Index Schema

The `incidents` index is auto-created with this schema:

| Field | Type | Features | Description |
|-------|------|----------|-------------|
| `id` | String | Key | Unique incident identifier (e.g., "INC-2026-0001") |
| `title` | String | Searchable | Incident title/summary |
| `severity` | String | Filterable, Facetable | low\|medium\|high\|critical |
| `timestamp` | String | Filterable, Sortable | ISO 8601 format |
| `files_involved` | Collection | Searchable, Filterable | List of files involved in the incident |
| `error_message` | String | Searchable | Error message or symptom |
| `root_cause` | String | Searchable | Root cause analysis |
| `affected_services` | Collection | Filterable | Services impacted by the incident |
| `duration_minutes` | Int32 | Filterable, Sortable | Incident duration |

## Error Handling

The History Agent will **fail fast** if Azure AI Search is not available:

```
[HistoryAgent] ❌ Failed to connect to Azure AI Search: [error details]
RuntimeError: Azure AI Search connection required.
```

This is intentional - the agent requires real incident data to provide accurate risk assessment. If you see this error:

1. Verify `.env` has all required Azure credentials
2. Check Azure Search service is running and accessible
3. Run `python setup_azure_search.py` to initialize the index
4. Verify service principal has proper permissions

## Output Format

The agent returns a standardized `AgentResult`:

```json
{
  "agent_name": "History Agent",
  "risk_score_modifier": 65,
  "status": "warning",
  "findings": [
    "payment_service.py involved in 4 incident(s) (50% of all incidents)",
    "  └─ 2026-02-24: Payment service timeout spike (high)",
    "  └─ 2026-02-20: Memory leak in payment retry loop (high)"
  ],
  "recommended_action": "CAUTION: This file has incident history. Require extended test validation and peer review."
}
```

### Risk Score Calculation

- **Base risk**: 10 points per incident, up to 50 points per file
- **Deployment frequency**: +15 points for 1 deploy today, +30 for 3+ deploys today
- **Status thresholds**:
  - `pass`: 0-39 points
  - `warning`: 40-69 points
  - `critical`: 70+ points

## Troubleshooting

### "Failed to connect to Azure AI Search"
**Cause**: Missing or invalid Azure credentials

**Solution**:
1. Create `.env` file with required credentials (see Configuration section)
2. Verify service principal exists and has credentials
3. Check Azure Search service is running in Azure Portal

### "No incidents found in Azure"
**Cause**: Empty index or no matching incidents

**Solution**:
1. Run `python setup_azure_search.py` to populate sample data
2. Verify the `incidents` index exists in Azure Portal → Search Service → Indexes
3. Check that file names in your query match indexed data (exact or substring match)

### "Index 'incidents' was not found"
**Cause**: Index not created yet

**Solution**:
- The MCP server auto-creates the index on first connection
- If it fails, check service principal has `Search Service Contributor` role
- Manually create index in Azure Portal if needed (see schema above)

### "RuntimeError: Azure AI Search connection required"
**Cause**: Agent cannot connect to Azure (by design - no fallback mode)

**Solution**:
- The History Agent requires Azure AI Search to function
- This ensures accurate risk assessment based on real data
- Follow the Quick Start steps above to configure Azure

## Sample Data

The setup script loads 8 sample incidents covering common scenarios:

1. **Payment service timeout spike** (high) - payment_service.py
2. **Database migration deadlock** (critical) - database.py, models/user.py
3. **Memory leak in retry loop** (high) - payment_service.py
4. **Payment failures during peak** (critical) - payment_service.py, retry_handler.py
5. **Authentication bypass** (critical) - auth_service.py
6. **Silent error handling** (high) - error_handler.py, payment_service.py
7. **Cache invalidation failure** (medium) - cache_manager.py
8. **Friday evening deployment** (medium) - checkout_flow.py

These cover various severities, file combinations, and incident patterns for realistic testing.

## Future Enhancements

- [ ] Add deployment event tracking (separate Azure AI Search index)
- [ ] Support Application Insights direct integration for real-time incidents
- [ ] Add Azure DevOps work item integration for incident tracking
- [ ] Implement vector embeddings for better semantic search
- [ ] Cache Azure results to reduce API calls and improve performance
- [ ] Add incident trend analysis over time (spike detection)
- [ ] Support custom incident severity weighting
