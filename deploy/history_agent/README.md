# History Agent Azure Deployment (Bicep)

This folder provisions the Azure resources needed for the History Agent without manual portal setup.

## What this deploys

- Azure AI Search service (used by `HistoryAgent` via `mcp_servers.azure_mcp_server.query`)

## Easiest authentication (recommended)

Use Microsoft Entra auth with Azure CLI:

1. Run `az login`
2. Deploy using `deploy.ps1` (defaults to Entra auth)
3. Keep `AZURE_SEARCH_KEY` empty in `.env`

`DefaultAzureCredential` will automatically use your Azure CLI session.

## One-command deploy

From repo root:

```powershell
./deploy/history_agent/deploy.ps1 `
  -ResourceGroupName rg-prism-dev `
  -Location eastus `
  -SearchServiceName prismsearch<unique>
```

## Optional: API key auth instead

```powershell
./deploy/history_agent/deploy.ps1 `
  -ResourceGroupName rg-prism-dev `
  -Location eastus `
  -SearchServiceName prismsearch<unique> `
  -EnableApiKeyAuth
```

## Parameters

- `-ResourceGroupName` (required)
- `-SearchServiceName` (required)
- `-Location` (optional, default `eastus`)
- `-EnableApiKeyAuth` (optional, defaults to Entra-only auth)
- `-PrincipalObjectId` (optional, auto-detected from `az ad signed-in-user`)
- `-GrantContributorAccess` (optional, also grants `Search Index Data Contributor`)

## After deploy

1. Put output values into `.env`:
   - `AZURE_SEARCH_ENDPOINT=...`
   - `AZURE_SEARCH_KEY=` (empty when using Entra auth)
2. Create the incidents index:

```powershell
python -m mcp_servers.azure_mcp_server.setup
```

3. Run History Agent tests or local flow.

## End-to-end pipeline

If you also want to deploy the Azure ingestion Function via IaC, use:

- [deploy/ingestion_function/README.md](../ingestion_function/README.md)
