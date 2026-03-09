# PRism Azure Deployment Guide

Comprehensive guide for deploying PRism to Azure using Infrastructure as Code (Bicep) with full automation.

## 🎯 Quick Deploy (TL;DR)

```powershell
# 1. Edit parameters
code parameters.json

# 2. Deploy everything
.\deploy.ps1
```

That's it! ☕ Grab coffee while Azure deploys (15-20 minutes).

---

## 📁 Files Overview

| File | Purpose |
|------|---------|
| `main.bicep` | Main Azure Bicep template - defines all Azure resources |
| `parameters.json` | Deployment parameters (fill in your values) |
| `parameters.example.json` | Example parameters file |
| `Dockerfile.orchestrator` | Docker image for the orchestrator service |
| `docker-compose.yml` | Local development environment |
| `deploy.ps1` | PowerShell deployment script (Windows) |
| `deploy.sh` | Bash deployment script (Linux/Mac) |
| `cleanup.ps1` | PowerShell cleanup script (Windows) |
| `cleanup.sh` | Bash cleanup script (Linux/Mac) |

---

## 🛠️ Phase 1: Prerequisites & Setup (10-15 minutes)

### Step 1.1: Install Required Tools

**What you need:**
- **Azure CLI** (v2.50+): [Install Guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- **Docker Desktop**: [Install Guide](https://docs.docker.com/desktop/) 
- **Python 3.11+**: For local development
- **Git**: For version control

**Windows (PowerShell):**
```powershell
# Check installations
az --version          # Should show 2.50+
docker --version      # Should show 20.10+
python --version      # Should show 3.11+
docker ps             # Verify Docker is running
```

**Mac (Homebrew):**
```bash
brew install azure-cli
brew install --cask docker
brew install python@3.11
```

**Linux (Ubuntu/Debian):**
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
sudo apt-get install docker.io python3.11
```

### Step 1.2: Login to Azure

```powershell
# Login to Azure
az login

# List available subscriptions
az account list --output table

# Set the subscription you want to use
az account set --subscription "YOUR_SUBSCRIPTION_NAME_OR_ID"

# Verify
az account show --output table
```

### Step 1.3: Generate GitHub Token

1. Go to **GitHub** → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. Click **"Generate new token (classic)"**
3. Name: `PRism Deployment`
4. Expiration: 90 days (or custom)
5. Select scopes:
   - ✅ `repo` (all)
   - ✅ `read:org`
   - ✅ `read:user`
6. Click **"Generate token"**
7. **Copy the token immediately** (you won't see it again!)

### Step 1.4: Generate Webhook Secret

```powershell
# PowerShell - Generate a secure random secret
$webhookSecret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Write-Host "Webhook Secret: $webhookSecret"
```

```bash
# Bash - Generate a secure random secret
openssl rand -base64 32
```

### Step 1.5: Configure Deployment Parameters

```powershell
# Copy the example parameters file
cp parameters.example.json parameters.json

# Edit the parameters file
code parameters.json
```

**Fill in your values in `parameters.json`:**

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "projectName": {"value": "prism"},
    "environment": {"value": "prod"},
    "location": {"value": "eastus2"},
    "githubToken": {"value": "ghp_YOUR_TOKEN_FROM_STEP_1.3"},
    "githubWebhookSecret": {"value": "YOUR_SECRET_FROM_STEP_1.4"},
    "githubRepoOwner": {"value": "your-org"},
    "githubRepoName": {"value": "your-repo"},
    "openAiModelDeployment": {"value": "gpt-4o"},
    "openAiModelVersion": {"value": "2024-11-20"},
    "openAiModelCapacity": {"value": 30}
  }
}
```

**✅ Verification:**
```powershell
# Validate the parameters file
Test-Path parameters.json
Get-Content parameters.json | ConvertFrom-Json
```

---

## 🏗️ Phase 2: Deploy Infrastructure (15-20 minutes)

### Step 2.1: Run the Deployment Script

```powershell
# Run the deployment script
.\deploy.ps1

# Or with custom options
.\deploy.ps1 -ResourceGroupName "rg-prism-dev" -Location "westus2"
```

**What happens:**
1. ✅ Validates prerequisites
2. ✅ Creates resource group  
3. ✅ Deploys Bicep template (all Azure resources)
4. ✅ Builds Docker image
5. ✅ Pushes to Azure Container Registry
6. ✅ Deploys Container App
7. ✅ Deploys Azure Functions
8. ✅ Configures AI Search index
9. ✅ Saves configuration to `.env.azure`

**Expected Output:**
```
═══════════════════════════════════════════════════════════════
  PRism Azure Deployment Script
═══════════════════════════════════════════════════════════════

Step 1: Validating Prerequisites
✓ Azure CLI is installed
✓ Docker is installed
✓ Python is installed
✓ Bicep template found

Step 2: Validating Azure Authentication  
✓ Logged in as: your-email@domain.com

Step 3: Creating Resource Group
✓ Resource group created

Step 4: Deploying Azure Infrastructure
ℹ This will take 10-15 minutes...
✓ Infrastructure deployed in 12.5 minutes

[... continues ...]
```

### Step 2.2: Monitor Deployment Progress

**Open another PowerShell window to monitor:**

```powershell
# Watch deployment status
az deployment group list --resource-group rg-prism-prod --output table

# View deployment details
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.{Status:provisioningState, Duration:duration}"
```

### Step 2.3: Review Deployed Resources

```powershell
# List all resources (should see 15-17 resources)
az resource list --resource-group rg-prism-prod --output table

# Expected resources:
# - Container Registry, Container Apps Environment, Container App
# - Function App, Azure OpenAI, AI Search, Content Safety  
# - Key Vault, Log Analytics, Application Insights
# - Storage Account, Managed Identities (2)
```

---

## 🏗️ What Gets Deployed

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

### **Compute Resources**
- **Azure Container Apps** - Runs the orchestrator (FastAPI server)
- **Azure Functions** - Runs the Azure MCP server for incident ingestion

### **AI Services** 
- **Azure OpenAI** - GPT-4o deployment for AI agents
- **Azure AI Search** - Semantic search for incident correlation  
- **Azure Content Safety** - Content filtering

### **Infrastructure**
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

### **Security**
- **Managed Identities** - Secure authentication between services
- **RBAC role assignments** - Granular permissions
- **Secrets stored in Key Vault** - No hardcoded credentials

---

## ⚙️ Phase 3: Configuration & Verification (10-15 minutes)

### Step 3.1: Get Deployment Outputs

The deployment script saves configuration to `.env.azure`, but you can also retrieve it:

```powershell
# Get specific values
$orchestratorUrl = az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.outputs.orchestratorUrl.value" `
  --output tsv

Write-Host "Orchestrator URL: $orchestratorUrl"
```

### Step 3.2: Test Health Endpoint

```powershell
# Test the orchestrator is running
curl $orchestratorUrl/health

# Expected response: {"status":"ok","service":"prism"}
```

**If it fails:**
```powershell
# Check logs
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --tail 50
```

### Step 3.3: Configure GitHub Webhook

1. Go to your GitHub repository 
2. Navigate to **Settings** → **Webhooks** → **Add webhook**
3. Fill in:
   - **Payload URL**: `https://your-orchestrator-url.azurecontainerapps.io/webhook/pr`  
   - **Content type**: `application/json`
   - **Secret**: [Your webhook secret from parameters.json]
   - **Events**: Select "Pull requests"
4. Click **Add webhook**

### Step 3.4: End-to-End Test

1. Create a test PR:
   ```powershell
   git checkout -b test-prism-deployment
   echo "Testing PRism deployment" >> README.md
   git add README.md
   git commit -m "test: PRism deployment verification"  
   git push origin test-prism-deployment
   ```

2. Open a Pull Request on GitHub

3. **Watch for PRism analysis:**
   - GitHub triggers the webhook
   - PRism orchestrator analyzes the PR
   - Check Application Insights for traces

### Step 3.5: Test Manual Analysis Endpoint

```powershell
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

# Expected response: Verdict with deployment confidence score
```

---

## ✅ Verification & Monitoring

### Step 4.1: Resource Status Check

```powershell
# Check all resources in the resource group (should see 15-17 resources)
az resource list --resource-group rg-prism-prod --output table

# Check Container App status
az containerapp show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --query "properties.runningStatus" `
  --output tsv
# Expected: "Running"

# Check Function App status  
az functionapp show `
  --name prism-prod-func `
  --resource-group rg-prism-prod `
  --query "state" `
  --output tsv
# Expected: "Running"
```

### Step 4.2: Application Insights Monitoring

```powershell
# Get Application Insights instrumentation key
$appInsightsName = az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.outputs.appInsightsName.value" `
  --output tsv

az monitor app-insights component show `
  --app $appInsightsName `
  --resource-group rg-prism-prod `
  --query "instrumentationKey" `
  --output tsv
```

**Monitor in Azure Portal:**
1. Go to Azure Portal → Resource Group → Application Insights
2. Navigate to **Live Metrics** for real-time traces
3. Check **Transaction search** for individual requests
4. You should see:
   - Incoming webhook requests
   - Agent execution traces  
   - OpenAI API calls
   - AI Search queries

### Step 4.3: View Logs

```powershell
# Container App logs (orchestrator)
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --follow

# Function App logs (Azure MCP)  
az functionapp logs tail `
  --name prism-prod-func `
  --resource-group rg-prism-prod
```

### Step 4.4: Configure AI Search Index

```powershell
# Run the search index setup script
python mcp_servers/azure_mcp_server/setup.py
```

This creates the `incidents` index with:
- Vector fields for semantic search
- Filterable fields (severity, resource, timestamp)  
- Searchable fields (description, root_cause)

### Step 4.5: Load Sample Data (Optional)

```powershell
# Load sample incidents for testing
python mcp_servers/azure_mcp_server/sample_data.py
```

---

## 📝 Deployment Options

### Standard Deployment
```powershell
.\deploy.ps1
```

### Custom Resource Group & Location
```powershell
.\deploy.ps1 -ResourceGroupName "rg-prism-dev" -Location "westus2"
```

### Skip Infrastructure (Update apps only)
```powershell
.\deploy.ps1 -SkipInfrastructure
```

### Skip Docker Build (Infrastructure only)
```powershell
.\deploy.ps1 -SkipDocker
```
---

## 🧪 Local Development

Test the orchestrator locally before deploying:

```powershell
# Create .env file from template
cp ..\.env.template .env

# Fill in Azure resource endpoints in .env

# Start with Docker Compose
docker-compose up

# Or run directly with Python
cd ../..
uvicorn agents.orchestrator.server:app --reload --port 8000
```

### Environment Variables (Auto-Configured)

These are automatically set by the deployment:

```bash
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT
AZURE_AI_SEARCH_ENDPOINT
AZURE_CONTENT_SAFETY_ENDPOINT
APPLICATIONINSIGHTS_CONNECTION_STRING
KEY_VAULT_URL
AZURE_CLIENT_ID  # Managed Identity
```

---

## 🔧 Customization

### Modify Azure Resources

Edit `main.bicep` to customize:
- **SKUs**: Change `sku.name` for any service (e.g., OpenAI capacity)
- **Regions**: Update `location` parameter  
- **Scaling**: Adjust Container App min/max replicas
- **Retention**: Change Log Analytics retention period

Example - Increase OpenAI capacity:
```bicep
param openAiModelCapacity int = 50  // Increase from 30
```

### Environment Variables

Add environment variables to the Container App in `main.bicep`:
```bicep
{
  name: 'MY_CUSTOM_VAR'
  value: 'my-value'
}
```

Or use secrets:
```bicep
{
  name: 'MY_SECRET'
  secretRef: 'my-secret'
}
```

---

## 📊 Cost Estimation

Approximate monthly costs (East US 2, March 2026):

| Service | SKU | Estimated Cost |
|---------|-----|----------------|
| Azure OpenAI | Standard (30K TPM) | ~$150-300 |
| AI Search | Basic | ~$75 |
| Container Apps | 1 vCPU, 2GB RAM | ~$40 |
| Azure Functions | Consumption | ~$10-20 |
| Content Safety | Standard | ~$10 |
| Container Registry | Basic | ~$5 |
| Log Analytics | Per GB | ~$10-30 |
| **Total** | | **~$300-480/month** |

> Costs vary based on usage. Use Azure Pricing Calculator for accurate estimates.

### Cost Optimization

**Development Environment:**
```json
{
  "environment": "dev",
  "openAiModelCapacity": 10,   // Reduce from 30
  "searchSku": "free"          // Use free tier
}
```

**Production Environment:**
```json
{
  "environment": "prod", 
  "openAiModelCapacity": 50,   // Increase for scale
  "searchSku": "standard"      // Better performance
}
```

---

## 🔒 Security Best Practices

1. **Use Key Vault** for all secrets (already configured)
2. **Enable Managed Identities** (already configured)  
3. **Restrict network access** in production:
   ```bicep
   publicNetworkAccess: 'Disabled'
   ```
4. **Rotate secrets regularly**
5. **Enable diagnostic settings** for audit logs
6. **Use Private Endpoints** for production (requires VNet)

---

## 🐛 Troubleshooting

### Common Issues

#### Deployment Fails
```powershell
# View deployment logs
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --output json

# Check activity log  
az monitor activity-log list `
  --resource-group rg-prism-prod `
  --max-events 20
```

**Common causes:**
- OpenAI quota not available in region → Try different location
- Resource name conflicts → Change projectName in parameters.json
- Insufficient permissions → Check Azure RBAC roles

#### Container App Won't Start
```powershell
# View logs
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --follow

# Check revision status
az containerapp revision list `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --output table
```

**Common causes:**
- Image pull failed → Check ACR credentials
- Environment variables missing → Check Container App configuration
- Application crash on startup → Check Python dependencies

#### Health Endpoint Returns 503
```powershell
# Check Container App status
az containerapp show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --query "properties.{Status:runningStatus, URL:configuration.ingress.fqdn}"

# Restart Container App
az containerapp revision restart `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --revision-name [revision-name]
```

**Causes:**
- Container not yet started (wait 30-60 seconds)
- Application crashed (check logs)
- Port misconfiguration (should be 8000)

#### GitHub Webhook Fails

**Check webhook deliveries:**
1. Go to GitHub → Settings → Webhooks
2. Click on the PRism webhook  
3. Check "Recent Deliveries"
4. Look for error messages

**Common causes:**
- Wrong URL → Update webhook URL
- SSL certificate error → Verify URL is HTTPS
- 401 Unauthorized → Check webhook secret matches
- 500 Internal Server Error → Check orchestrator logs

#### OpenAI Quota Issues
1. Go to **Azure Portal** → **Azure OpenAI**
2. Check your quota limits  
3. Request quota increase if needed

#### Can't Push to ACR
```powershell
# Re-login to ACR
az acr login --name <your-acr-name>

# Check ACR credentials
az acr credential show --name <your-acr-name>
```

#### AI Search Index Not Created
```powershell
# Manually run setup script
python mcp_servers/azure_mcp_server/setup.py

# Check if index exists
az search index list --resource-group rg-prism-prod --service-name <search-service-name>
```

#### Azure CLI Authentication Issues
```powershell
# Re-login to Azure
az login
az account show

# List subscriptions if you need to switch
az account list --output table
az account set --subscription "YOUR_SUBSCRIPTION_NAME"
```

#### Docker Build Failures
```powershell
# Ensure Docker Desktop is running
docker ps

# Try building without cache
docker build --no-cache -t <image-name> .

# Check Docker daemon status
docker version
```

### Advanced Troubleshooting

#### View Deployment Operation Details
```powershell
# Get deployment history
az deployment group list --resource-group rg-prism-prod --output table

# View specific deployment details
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.{Status:provisioningState, Error:error, Duration:duration}"

# View deployment operations (detailed)
az deployment operation group list `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --output table
```

#### Resource-Specific Diagnostics
```powershell
# Check Azure OpenAI deployment status
az cognitiveservices account deployment list `
  --resource-group rg-prism-prod `
  --name prism-prod-openai `
  --output table

# Check Container App revision history  
az containerapp revision list `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --output table

# Check Function App configuration
az functionapp config show `
  --name prism-prod-func `
  --resource-group rg-prism-prod
```

### Quick Support

| Issue | Solution |
|-------|----------|
| Deployment fails | Check `az deployment group show --name ...` |
| Container app won't start | Check `az containerapp logs show ...` |
| OpenAI quota error | Request quota increase in Azure Portal |
| Can't push to ACR | Run `az acr login --name ...` |
| Health check fails | Check Application Insights Failures |

---

## 🗑️ Cleanup

**⚠️ WARNING: This deletes ALL resources and is IRREVERSIBLE!**

**Windows:**
```powershell
.\cleanup.ps1 -ResourceGroupName "rg-prism-prod"
```

**Linux/Mac:**
```bash
./cleanup.sh --resource-group rg-prism-prod
```

With force (skip confirmation):
```bash
./cleanup.sh --resource-group rg-prism-prod --force
```

---

## 🎉 Success Checklist

After deployment, verify:

- [ ] Health endpoint returns `{"status": "ok"}`
- [ ] GitHub webhook configured and active
- [ ] Test PR triggers PRism analysis  
- [ ] Application Insights shows traces
- [ ] Logs visible in Azure Portal
- [ ] No errors in Container App logs

---

## � Next Steps & Advanced Configuration

### 1. Configure VS Code Extension
- Install PRism VS Code extension from `vscode_extension/` directory
- View deployment confidence scores directly in VS Code
- Get real-time PR analysis feedback

### 2. Customize Agent Logic
- Modify agent thresholds in `agents/` directory
- Add new detection patterns to Diff Analyst
- Adjust risk scoring in individual agents
- Add custom findings and recommendations

### 3. Production Incident Data Integration
- **Azure Monitor Alerts**: Configure real-time alert ingestion
- **Log Analytics Queries**: Custom KQL queries for incident detection
- **Manual Ingestion API**: Bulk load historical incident data
- **Third-party Integrations**: PagerDuty, ServiceNow, etc.

### 4. Set Up CI/CD Pipeline
```yaml
# GitHub Actions workflow is included at .github/workflows/deploy-azure.yml
# Configure these repository secrets:
AZURE_CREDENTIALS     # Service principal JSON
GITHUB_TOKEN          # For webhook configuration  
GITHUB_WEBHOOK_SECRET # For webhook validation
```

### 5. Enhanced Security & Compliance
```powershell
# Enable Private Endpoints (requires VNet)
# Configure network isolation
# Set up Azure Policy guardrails
# Enable Azure Security Center
# Configure audit logging
```

### 6. Monitoring & Observability
```powershell
# Set up alerts in Application Insights
az monitor metrics alert create \
  --name "PRism-HighLatency" \
  --resource-group rg-prism-prod \
  --description "Alert when request latency > 5 seconds"

# Configure Log Analytics queries for insights
# Set up Azure Dashboards for operational metrics
# Enable distributed tracing across all agents
```

### 7. Scale & Performance Optimization
```bicep
// Increase Container App resources for high-volume repos
resources: {
  cpu: json('1.0')        // Increase from 0.5
  memory: '2Gi'          // Increase from 1Gi  
}

// Auto-scaling rules
scale: {
  minReplicas: 2         // Always have 2 instances
  maxReplicas: 20        // Scale up to 20 for burst
}

// OpenAI capacity planning
param openAiModelCapacity int = 50  // Increase TPM for high volume
```

---

## �🔄 Making Changes & Redeployment

### Update Application Code Only
```powershell
# Skip infrastructure deployment  
.\deploy.ps1 -SkipInfrastructure
```

### Update Infrastructure Only
```powershell
# Skip Docker build
.\deploy.ps1 -SkipDocker
```

### Full Redeployment
```powershell
# Delete resource group
.\cleanup.ps1 -ResourceGroupName "rg-prism-prod"

# Deploy again
.\deploy.ps1
```

---

## 📚 Additional Resources

- [Azure Bicep Documentation](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/)
- [Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/)
- [Azure OpenAI Service](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
- [Azure AI Search](https://learn.microsoft.com/en-us/azure/search/)  

---

## 🤝 Contributing

When modifying deployment templates:
1. Test locally with Docker Compose first
2. Deploy to a dev environment
3. Validate all resources are created correctly
4. Update this README with any new parameters or steps
5. Test the cleanup script works correctly

---

## 📞 Support

For deployment issues:
1. Check this README  
2. Azure Portal → Resource Health
3. Application Insights → Failures
4. GitHub Issues

---

**Ready to deploy?** Run:
```powershell
.\deploy.ps1
```

**Estimated time:** 45-65 minutes total deployment time
