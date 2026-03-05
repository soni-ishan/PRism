# PRism One-Command Deployment

## The simplest way to deploy PRism

### Prerequisites
- Azure subscription
- Azure CLI installed (`az --version` to verify)

### Deploy in 2 steps

**Step 1: Authenticate (once)**
```powershell
az login
```

**Step 2: Deploy (one command)**
```powershell
./deploy/deploy.ps1 -SubscriptionId <sub-id> -ResourceGroupName <rg-name>
```

Replace:
- `<sub-id>`: Your Azure subscription ID (e.g., `12345678-1234-1234-1234-123456789012`)
- `<rg-name>`: Any name you want for your resource group (e.g., `rg-prism-dev`)

**That's it.** The script will:

- Create the resource group (if needed)
- Deploy Azure AI Search for History Agent
- Deploy Azure Function App for ingestion
- Auto-discover Log Analytics workspace and OpenAI (if available)
- Assign all required RBAC roles automatically
- Print your `.env` configuration

### What resources are created?

- **Search service** (auto-named, e.g., `prismsearch1234`)
- **Function App** (auto-named, e.g., `prism-ingest1234`)
- **Storage account** (for Function runtime)
- **Application Insights** (monitoring)
- **Role assignments** (Entra ID RBAC)

### What's NOT created

- Log Analytics workspace (discovered in your RG; create separately if missing)
- OpenAI (discovered if available; optional)
- Event Grid subscription (create manually if alert-driven ingestion needed)

## Troubleshooting

**"No Log Analytics workspace found"**
- Create a workspace in the resource group first:
  ```powershell
  az monitor log-analytics workspace create `
    --resource-group <rg-name> `
    --workspace-name <ws-name>
  ```
- Then re-run the deploy script.

**"Azure CLI is not installed"**
- Install: https://aka.ms/azure-cli

**"No Azure login session found"**
- Run: `az login`

## After deployment

1. Set your `.env` file:
   ```bash
   AZURE_SEARCH_ENDPOINT=https://prismsearch1234.search.windows.net
   AZURE_SEARCH_KEY=
   ```

2. Create the incidents index:
   ```powershell
   python -m mcp_servers.azure_mcp_server.setup
   ```

3. Run History Agent:
   ```powershell
   python agents/history_agent/agent.py payment_service.py
   ```

4. (If not skipping Function) Publish code and set up triggers:
   ```powershell
   func azure functionapp publish prism-ingest1234 --python
   ```

## Advanced options

### Skip Function deployment (history agent only)
```powershell
./deploy/deploy.ps1 -SubscriptionId <id> -ResourceGroupName <rg> -SkipFunctionDeployment
```

### Custom location
```powershell
./deploy/deploy.ps1 -SubscriptionId <id> -ResourceGroupName <rg> -Location westus
```

## For detailed resource configuration

- [History Agent search resources](history_agent/README.md)
- [Ingestion Function resources](ingestion_function/README.md)
