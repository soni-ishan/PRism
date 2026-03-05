# Ingestion Azure Function Deployment (Bicep)

This folder provisions the Azure resources for `mcp_servers/azure_mcp_server/function_app.py` so ingestion can run on Azure Functions without manual portal creation.

## What this deploys

- Storage account (Functions runtime storage)
- Linux Consumption App Service plan (`Y1`)
- Application Insights
- Azure Function App (Python 3.11, system-assigned managed identity)
- Function app settings required by ingestion

## Easiest authentication model

- Run `az login` locally
- Use Function managed identity in Azure (recommended)
- Keep `AZURE_SEARCH_KEY` empty to use Entra auth in code (`DefaultAzureCredential`)

## One-command infra deploy

```powershell
./deploy/ingestion_function/deploy.ps1 `
  -ResourceGroupName rg-prism-dev `
  -Location eastus `
  -FunctionAppName prism-ingest-func-<unique> `
  -StorageAccountName prismingest<unique> `
  -AzureLogWorkspaceId <workspace-guid> `
  -AzureResourceName <cloud-role-name> `
  -AzureSearchEndpoint https://<search>.search.windows.net `
  -SearchServiceResourceId /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Search/searchServices/<name> `
  -LogAnalyticsWorkspaceResourceId /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<name>
```

Optional OpenAI RBAC assignment:

```powershell
  -OpenAIResourceId /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name>
```

## What the script also does

If resource IDs are supplied, it automatically grants the Function identity:

- `Search Index Data Contributor` on Azure AI Search
- `Log Analytics Reader` on Log Analytics workspace
- `Cognitive Services User` on Azure OpenAI (optional)

## Publish Function code

After infra deployment, publish code from the repo root:

```powershell
func azure functionapp publish <FUNCTION_APP_NAME> --python
```

## Required app settings already configured by template

- `AZURE_LOG_WORKSPACE_ID`
- `AZURE_RESOURCE_NAME`
- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_KEY`
- `AZURE_INGEST_WINDOW_MINUTES`
- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_DEPLOYMENT`

## Remaining one-time setup

Create/validate the `incidents` search index:

```powershell
python -m mcp_servers.azure_mcp_server.setup
```

## Notes

- Event Grid wiring for alert-driven ingest is not created by this template; create subscription separately to function `ingest_from_monitor_alert`.
- If you prefer key-based search auth, pass `-AzureSearchKey` during deployment.
