# PRism Azure Deployment Guide

This guide provides a step-by-step process to deploy PRism to Azure using Infrastructure as Code (Bicep templates). The deployment is reproducible, automated, and requires minimal user input.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Architecture Overview](#architecture-overview)
3. [Quick Start](#quick-start)
4. [Detailed Deployment Steps](#detailed-deployment-steps)
5. [Post-Deployment Configuration](#post-deployment-configuration)
6. [Verification](#verification)
7. [Cleanup](#cleanup)

---

## Prerequisites

### Required Tools
- **Azure CLI** (v2.50+): [Install Guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- **Docker Desktop**: [Install Guide](https://docs.docker.com/desktop/)
- **Git**: For version control
- **PowerShell 7+** (Windows) or Bash (Linux/Mac)

### Azure Permissions
You need an Azure subscription with:
- **Contributor** role (to create resources)
- **Role Based Access Control Administrator** (to assign managed identities)
- Ability to create service principals

### GitHub Requirements
- GitHub Personal Access Token (PAT) with `repo` scope
- Repository webhook access

---

## Architecture Overview

The deployment creates the following Azure resources:

```
┌─────────────────────────────────────────────────────────────┐
│                     Resource Group                           │
│                                                              │
│  ┌──────────────────┐       ┌──────────────────┐           │
│  │  Container App   │       │ Azure Functions  │           │
│  │  (Orchestrator)  │◄─────►│  (Azure MCP)     │           │
│  └────────┬─────────┘       └────────┬─────────┘           │
│           │                          │                      │
│           ├──────────┬───────────────┼───────────┐         │
│           ▼          ▼               ▼           ▼         │
│  ┌────────────┐ ┌──────────┐  ┌──────────┐ ┌─────────┐   │
│  │  OpenAI    │ │ AI Search│  │ Log      │ │  Key    │   │
│  │  (GPT-4o)  │ │          │  │ Analytics│ │  Vault  │   │
│  └────────────┘ └──────────┘  └──────────┘ └─────────┘   │
│                                                              │
│  ┌──────────────────┐       ┌──────────────────┐           │
│  │ Container        │       │ App Insights     │           │
│  │ Registry         │       │ (Monitoring)     │           │
│  └──────────────────┘       └──────────────────┘           │
│                                                              │
│  ┌──────────────────┐       ┌──────────────────┐           │
│  │ Content Safety   │       │ Storage Account  │           │
│  │                  │       │ (Functions)      │           │
│  └──────────────────┘       └──────────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

### Resource List
1. **Azure Container Registry** - Stores Docker images
2. **Container Apps Environment** - Hosts the orchestrator
3. **Container App** - Runs the FastAPI orchestrator server
4. **Azure Functions App** - Runs the Azure MCP server for incident ingestion
5. **Azure OpenAI Service** - GPT-4o deployment for AI agents
6. **Azure AI Search** - Semantic search for incident correlation
7. **Azure Log Analytics** - Centralized logging
8. **Application Insights** - Observability and tracing
9. **Azure Content Safety** - Content filtering
10. **Key Vault** - Secrets management
11. **Storage Account** - Required for Azure Functions
12. **Managed Identities** - Secure authentication between services

---

## Quick Start

### 1. Clone and Navigate
```powershell
cd c:\Users\spx437\Desktop\PRism
```

### 2. Login to Azure
```powershell
az login
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"
```

### 3. Configure Deployment Parameters
Edit `foundry/deployment_config/parameters.json`:
```json
{
  "projectName": "prism",
  "environment": "prod",
  "location": "eastus2",
  "githubToken": "ghp_your_token_here",
  "githubWebhookSecret": "your_webhook_secret_here",
  "githubRepoOwner": "your-org",
  "githubRepoName": "your-repo"
}
```

### 4. Run the Deployment Script
```powershell
.\foundry\deployment_config\deploy.ps1
```

That's it! The script will:
- ✅ Validate your Azure login
- ✅ Create all Azure resources using Bicep
- ✅ Build and push Docker images
- ✅ Deploy the application
- ✅ Configure webhooks
- ✅ Output all connection strings and endpoints

**Estimated deployment time:** 15-25 minutes

---

## Detailed Deployment Steps

### Step 1: Prepare Configuration

#### 1.1 Generate GitHub Token
1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Grant scopes: `repo`, `read:org`, `read:user`
4. Copy the token immediately (you won't see it again)

#### 1.2 Generate Webhook Secret
```powershell
# Generate a secure random secret
$webhookSecret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Write-Host "Webhook Secret: $webhookSecret"
```

#### 1.3 Edit Parameters File
Open `foundry/deployment_config/parameters.json` and fill in your values:
```json
{
  "projectName": "prism",
  "environment": "prod",
  "location": "eastus2",
  "githubToken": "YOUR_GITHUB_TOKEN",
  "githubWebhookSecret": "YOUR_WEBHOOK_SECRET",
  "githubRepoOwner": "your-org",
  "githubRepoName": "your-repo",
  "openAiModelDeployment": "gpt-4o",
  "openAiModelVersion": "2024-11-20"
}
```

### Step 2: Deploy Infrastructure

#### 2.1 Set Azure Context
```powershell
# Login
az login

# List subscriptions
az account list --output table

# Set the subscription
az account set --subscription "<SUBSCRIPTION_ID or NAME>"

# Verify
az account show --output table
```

#### 2.2 Create Resource Group
```powershell
$resourceGroup = "rg-prism-prod"
$location = "eastus2"

az group create `
  --name $resourceGroup `
  --location $location
```

#### 2.3 Deploy Bicep Template
```powershell
az deployment group create `
  --resource-group $resourceGroup `
  --template-file foundry/deployment_config/main.bicep `
  --parameters foundry/deployment_config/parameters.json `
  --parameters location=$location `
  --verbose
```

**This will create all Azure resources.** The deployment outputs include:
- Container Registry login server
- OpenAI endpoint and key
- AI Search endpoint and key
- Function App URL
- Container App URL
- Key Vault name

#### 2.4 Capture Deployment Outputs
```powershell
# Save outputs to a file for reference
az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs `
  --output json > deployment-outputs.json

# Display key outputs
az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query "properties.outputs.{
    OrchestratorURL: orchestratorUrl.value,
    FunctionAppURL: functionAppUrl.value,
    OpenAIEndpoint: openAiEndpoint.value,
    AISearchEndpoint: aiSearchEndpoint.value
  }" `
  --output table
```

### Step 3: Build and Deploy Applications

#### 3.1 Build Orchestrator Docker Image
```powershell
# Get ACR login server from outputs
$acrName = az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs.containerRegistryName.value `
  --output tsv

$acrLoginServer = "${acrName}.azurecr.io"

# Login to ACR
az acr login --name $acrName

# Build and push orchestrator image
docker build `
  --platform linux/amd64 `
  -t ${acrLoginServer}/prism-orchestrator:latest `
  -f foundry/deployment_config/Dockerfile.orchestrator `
  .

docker push ${acrLoginServer}/prism-orchestrator:latest
```

#### 3.2 Deploy Azure Functions
```powershell
# Get Function App name
$functionAppName = az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs.functionAppName.value `
  --output tsv

# Deploy function code
cd mcp_servers/azure_mcp_server
func azure functionapp publish $functionAppName --python

cd ../..
```

#### 3.3 Update Container App with Image
```powershell
$containerAppName = az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs.containerAppName.value `
  --output tsv

az containerapp update `
  --name $containerAppName `
  --resource-group $resourceGroup `
  --image ${acrLoginServer}/prism-orchestrator:latest
```

### Step 4: Configure AI Search

#### 4.1 Initialize Search Index
```powershell
# Run the search index setup script
python mcp_servers/azure_mcp_server/setup.py
```

This creates the `incidents` index with:
- Vector fields for semantic search
- Filterable fields (severity, resource, timestamp)
- Searchable fields (description, root_cause)

### Step 5: Configure GitHub Webhook

#### 5.1 Get Orchestrator URL
```powershell
$orchestratorUrl = az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs.orchestratorUrl.value `
  --output tsv

Write-Host "Orchestrator Webhook URL: ${orchestratorUrl}/webhook/pr"
```

#### 5.2 Configure in GitHub
1. Go to your repository on GitHub
2. Navigate to **Settings** → **Webhooks** → **Add webhook**
3. Enter:
   - **Payload URL**: `https://<your-container-app-url>/webhook/pr`
   - **Content type**: `application/json`
   - **Secret**: Your webhook secret from parameters.json
   - **Events**: Select "Pull requests"
4. Click **Add webhook**5. Test by opening a PR

---

## Post-Deployment Configuration

### 1. Verify Application Insights
```powershell
# Get Application Insights instrumentation key
$appInsightsName = az deployment group show `
  --resource-group $resourceGroup `
  --name main `
  --query properties.outputs.appInsightsName.value `
  --output tsv

az monitor app-insights component show `
  --app $appInsightsName `
  --resource-group $resourceGroup `
  --query "instrumentationKey" `
  --output tsv
```

### 2. Populate Sample Incident Data (Optional)
```powershell
# Load sample incidents for testing
python mcp_servers/azure_mcp_server/sample_data.py
```

### 3. Test the Orchestrator Endpoint
```powershell
# Health check
curl "${orchestratorUrl}/health"

# Manual analysis trigger (test payload)
$testPayload = @{
  pr_number = 123
  repository = "your-org/your-repo"
  title = "Test PR"
  changed_files = @("payment_service.py", "auth_service.py")
  diff = "+retry_logic removed"
  author = "test-user"
  created_at = (Get-Date).ToString("o")
} | ConvertTo-Json

Invoke-RestMethod `
  -Method POST `
  -Uri "${orchestratorUrl}/analyze" `
  -Body $testPayload `
  -ContentType "application/json"
```

---

## Verification

### 1. Resource Status
```powershell
# Check all resources in the resource group
az resource list `
  --resource-group $resourceGroup `
  --output table

# Check Container App status
az containerapp show `
  --name $containerAppName `
  --resource-group $resourceGroup `
  --query "properties.runningStatus" `
  --output tsv

# Check Function App status
az functionapp show `
  --name $functionAppName `
  --resource-group $resourceGroup `
  --query "state" `
  --output tsv
```

### 2. View Logs
```powershell
# Container App logs
az containerapp logs show `
  --name $containerAppName `
  --resource-group $resourceGroup `
  --follow

# Function App logs
az functionapp logs tail `
  --name $functionAppName `
  --resource-group $resourceGroup
```

### 3. Test End-to-End
1. Open a Pull Request in your GitHub repository
2. PRism webhook should trigger automatically
3. Check Application Insights for trace data:
   ```powershell
   # Open Application Insights in browser
   az monitor app-insights component show `
     --app $appInsightsName `
     --resource-group $resourceGroup `
     --query "appId" `
     --output tsv
   ```
4. Navigate to Azure Portal → Application Insights → Live Metrics
5. You should see real-time requests and traces

---

## Cleanup

### Remove All Resources
```powershell
# Delete the entire resource group (WARNING: This is irreversible)
az group delete `
  --name $resourceGroup `
  --yes `
  --no-wait
```

### Remove GitHub Webhook
1. Go to your repository → Settings → Webhooks
2. Find the PRism webhook
3. Click "Delete"

---

## Troubleshooting

### Common Issues

#### 1. Azure CLI Not Logged In
```powershell
az login
az account show
```

#### 2. Docker Build Fails
```powershell
# Ensure Docker Desktop is running
docker ps

# Try building without cache
docker build --no-cache ...
```

#### 3. Container App Not Starting
```powershell
# Check logs
az containerapp logs show --name $containerAppName --resource-group $resourceGroup

# Check revision status
az containerapp revision list `
  --name $containerAppName `
  --resource-group $resourceGroup `
  --output table
```

#### 4. OpenAI Quota Issues
If you get quota errors:
1. Go to Azure Portal → Azure OpenAI
2. Check your quota limits
3. Request quota increase if needed

#### 5. AI Search Index Not Created
```powershell
# Manually run setup script
python mcp_servers/azure_mcp_server/setup.py
```

---

## Next Steps

1. **Configure VS Code Extension**: See [vscode_extension/README.md](vscode_extension/README.md)
2. **Customize Agents**: Modify agent logic in `agents/` directory
3. **Add More Incident Data**: Use Azure Monitor alerts or manual ingestion
4. **Set Up CI/CD**: Create GitHub Actions workflow for automated deployments
5. **Enable RBAC**: Configure fine-grained access control for production

---

## Support

- **Documentation**: See [README.md](README.md) and [INTEGRATION_STATUS.md](INTEGRATION_STATUS.md)
- **Architecture**: See [docs/architecture.mermaid](docs/architecture.mermaid)
- **Issues**: Open a GitHub issue

---

**Congratulations!** 🎉 PRism is now deployed and ready to analyze your Pull Requests with AI-powered risk intelligence.
