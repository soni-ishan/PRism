# PRism Azure Deployment - Step-by-Step Working Plan

This document provides a structured plan for deploying PRism to Azure. We'll work through this together, step by step, to ensure a fully reproducible deployment.

---

## 🎯 Deployment Strategy Overview

We're using **Infrastructure as Code (IaC)** with Azure Bicep templates to create a fully automated, reproducible deployment. The user provides minimal configuration (GitHub token, webhook secret, repo details), and everything else is automated.

### What's Automated
✅ **Azure resource provisioning** (Bicep)  
✅ **Docker image build & push** (Scripts)  
✅ **Application deployment** (Scripts)  
✅ **Managed identities & RBAC** (Bicep)  
✅ **Secrets management** (Key Vault)  
✅ **Monitoring setup** (Application Insights)  

### What User Provides
- GitHub Personal Access Token
- GitHub Webhook Secret
- Repository owner and name
- Azure subscription (where to deploy)

---

## 📅 Deployment Timeline

| Phase | Time | Tasks |
|-------|------|-------|
| **Phase 1: Setup** | 10-15 min | Install tools, configure parameters |
| **Phase 2: Infrastructure** | 15-20 min | Deploy Azure resources via Bicep |
| **Phase 3: Applications** | 10-15 min | Build & deploy Docker images, Functions |
| **Phase 4: Configuration** | 5-10 min | Configure GitHub webhook, test endpoints |
| **Phase 5: Verification** | 5 min | End-to-end testing |
| **TOTAL** | **45-65 min** | |

---

## 🛠️ Phase 1: Setup & Prerequisites (10-15 minutes)

### Step 1.1: Install Required Tools

**What we need:**
- Azure CLI
- Docker Desktop
- Python 3.11+
- Git

**Windows (PowerShell):**
```powershell
# Check if Azure CLI is installed
az --version

# If not installed, download from:
# https://aka.ms/install-azure-cli

# Check Docker
docker --version
docker ps  # Verify Docker is running

# Check Python
python --version
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

**✅ Verification:**
```powershell
az --version          # Should show 2.50+
docker --version      # Should show 20.10+
python --version      # Should show 3.11+
```

---

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

**✅ Verification:**
You should see your account details and the correct subscription selected.

---

### Step 1.3: Generate GitHub Token

1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Name: `PRism Deployment`
4. Expiration: 90 days (or custom)
5. Select scopes:
   - ✅ `repo` (all)
   - ✅ `read:org`
   - ✅ `read:user`
6. Click "Generate token"
7. **Copy the token immediately** (you won't see it again!)

**Save it somewhere secure for the next step.**

---

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

**Save this webhook secret for the next step.**

---

### Step 1.5: Configure Deployment Parameters

```powershell
# Navigate to the deployment config directory
cd c:\Users\spx437\Desktop\PRism\foundry\deployment_config\bicep

# Copy the example parameters file
cp parameters.example.json parameters.json

# Edit the parameters file
code parameters.json
```

**Fill in these values in `parameters.json`:**

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "projectName": {
      "value": "prism"
    },
    "environment": {
      "value": "prod"
    },
    "location": {
      "value": "eastus2"
    },
    "githubToken": {
      "value": "ghp_YOUR_TOKEN_FROM_STEP_1.3"
    },
    "githubWebhookSecret": {
      "value": "YOUR_SECRET_FROM_STEP_1.4"
    },
    "githubRepoOwner": {
      "value": "spx437"
    },
    "githubRepoName": {
      "value": "PRism"
    },
    "openAiModelDeployment": {
      "value": "gpt-4o"
    },
    "openAiModelVersion": {
      "value": "2024-11-20"
    },
    "openAiModelCapacity": {
      "value": 30
    }
  }
}
```

**✅ Verification:**
```powershell
# Validate the parameters file exists and has valid JSON
Test-Path parameters.json
Get-Content parameters.json | ConvertFrom-Json
```

---

## 🏗️ Phase 2: Deploy Infrastructure (15-20 minutes)

### Step 2.1: Run the Deployment Script

```powershell
# Make sure you're in the PRism root directory
cd c:\Users\spx437\Desktop\PRism

# Run the deployment script
.\foundry\deployment_config\scripts\deploy.ps1
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
✓ Docker daemon is running
✓ Python is installed
✓ Parameters file found
✓ Bicep template found

Step 2: Validating Azure Authentication
✓ Logged in as: your-email@domain.com
ℹ Subscription: Your Subscription Name (abc-123-def)

Step 3: Creating Resource Group
ℹ Resource group 'rg-prism-prod' already exists / created

Step 4: Deploying Azure Infrastructure
ℹ This will take 10-15 minutes...
[Deployment progress...]
✓ Infrastructure deployed in 12.5 minutes

[... continues ...]
```

---

### Step 2.2: Monitor Deployment Progress

**Open another PowerShell window and monitor:**

```powershell
# Watch deployment status
az deployment group list --resource-group rg-prism-prod --output table

# View deployment operation details
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.{Status:provisioningState, Duration:properties.duration}" `
  --output table
```

---

### Step 2.3: Review Deployed Resources

After deployment completes:

```powershell
# List all resources
az resource list --resource-group rg-prism-prod --output table

# Expected resources:
# - Container Registry (ACR)
# - Container Apps Environment
# - Container App (orchestrator)
# - Function App (Azure MCP)
# - Azure OpenAI
# - AI Search Service
# - Content Safety
# - Key Vault
# - Log Analytics Workspace
# - Application Insights
# - Storage Account
# - Managed Identities (2)
```

**✅ Verification:**
You should see approximately **15-17 resources** in the resource group.

---

## 📦 Phase 3: Deploy Applications (10-15 minutes)

This phase is **automatically done by the deploy.ps1 script**, but here's what happens:

### Step 3.1: Build Docker Image
```powershell
# The script does this automatically:
az acr login --name <your-acr-name>
docker build -t <acr-name>.azurecr.io/prism-orchestrator:latest .
docker push <acr-name>.azurecr.io/prism-orchestrator:latest
```

### Step 3.2: Deploy to Container App
```powershell
# The script updates the Container App with the new image
az containerapp update --name prism-prod-orchestrator --resource-group rg-prism-prod --image <image>
```

### Step 3.3: Deploy Azure Functions
```powershell
# The script publishes the function app
cd mcp_servers/azure_mcp_server
func azure functionapp publish prism-prod-func --python
```

**✅ Verification:**
```powershell
# Check Container App status
az containerapp show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --query "properties.runningStatus"

# Should return: "Running"

# Check Function App status
az functionapp show `
  --name prism-prod-func `
  --resource-group rg-prism-prod `
  --query "state"

# Should return: "Running"
```

---

## ⚙️ Phase 4: Configuration (5-10 minutes)

### Step 4.1: Get Deployment Outputs

The deployment script saves this to `.env.azure`, but you can also retrieve it:

```powershell
# View all deployment outputs
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.outputs" `
  --output json

# Get specific values
$orchestratorUrl = az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.outputs.orchestratorUrl.value" `
  --output tsv

Write-Host "Orchestrator URL: $orchestratorUrl"
```

**Save these values:**
- `orchestratorUrl` - For GitHub webhook
- `openAiEndpoint` - For reference
- `aiSearchEndpoint` - For reference
- `keyVaultUrl` - For secrets access

---

### Step 4.2: Test Health Endpoint

```powershell
# Test the orchestrator is running
curl $orchestratorUrl/health

# Expected response:
# {"status":"ok","service":"prism"}
```

**If it fails:**
```powershell
# Check logs
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --tail 50
```

---

### Step 4.3: Configure GitHub Webhook

1. Go to your GitHub repository: `https://github.com/spx437/PRism`
2. Navigate to **Settings** → **Webhooks** → **Add webhook**
3. Fill in:
   - **Payload URL**: `https://your-orchestrator-url.azurecontainerapps.io/webhook/pr`
   - **Content type**: `application/json`
   - **Secret**: [Your webhook secret from parameters.json]
   - **SSL verification**: Enable SSL verification
   - **Events**: Select "Let me select individual events"
     - ✅ Pull requests
   - **Active**: ✅ Checked
4. Click **Add webhook**

**✅ Verification:**
GitHub will send a test ping. Check the webhook deliveries to ensure it succeeded (should show 200 OK).

---

### Step 4.4: Load Sample Incident Data (Optional)

```powershell
# Set environment variables
$env:AZURE_SEARCH_ENDPOINT = "https://your-search-service.search.windows.net"
$searchKey = az search admin-key show `
  --resource-group rg-prism-prod `
  --service-name prism-prod-search-... `
  --query "primaryKey" `
  --output tsv
$env:AZURE_SEARCH_KEY = $searchKey

# Load sample data
python mcp_servers\azure_mcp_server\sample_data.py
```

---

## ✅ Phase 5: Verification & Testing (5 minutes)

### Step 5.1: End-to-End Test - Create a Test PR

1. Create a new branch:
   ```powershell
   git checkout -b test-prism-deployment
   ```

2. Make a change to a file (e.g., README.md):
   ```powershell
   echo "Testing PRism deployment" >> README.md
   git add README.md
   git commit -m "test: PRism deployment verification"
   git push origin test-prism-deployment
   ```

3. Open a Pull Request on GitHub

4. **Watch for PRism analysis:**
   - GitHub should trigger the webhook
   - PRism orchestrator receives the PR event
   - Agents analyze the PR in parallel
   - Verdict agent computes deployment confidence score

---

### Step 5.2: Monitor in Application Insights

```powershell
# Get Application Insights instrumentation key
az monitor app-insights component show `
  --app prism-prod-appins `
  --resource-group rg-prism-prod `
  --query "instrumentationKey"
```

1. Go to Azure Portal: https://portal.azure.com
2. Navigate to your resource group: `rg-prism-prod`
3. Click on Application Insights: `prism-prod-appins`
4. Go to **Live Metrics** to see real-time traces
5. Go to **Transaction search** to see individual requests

**You should see:**
- Incoming webhook requests
- Agent execution traces
- OpenAI API calls
- AI Search queries

---

### Step 5.3: View Logs

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

---

### Step 5.4: Test Manual Analysis Endpoint

```powershell
# Create test payload
$testPayload = @{
  pr_number = 123
  repository = "spx437/PRism"
  title = "Test PR"
  changed_files = @("payment_service.py", "auth_service.py")
  diff = "+removed retry logic"
  author = "spx437"
  created_at = (Get-Date).ToString("o")
} | ConvertTo-Json

# Send to orchestrator
Invoke-RestMethod `
  -Method POST `
  -Uri "$orchestratorUrl/analyze" `
  -Body $testPayload `
  -ContentType "application/json"

# Expected response: Verdict with deployment confidence score
```

---

## 🎉 Success Criteria

Your deployment is successful if:

- [x] All Azure resources are created (15-17 resources)
- [x] Health endpoint returns `{"status": "ok"}`
- [x] Docker image is in Azure Container Registry
- [x] Container App shows "Running" status
- [x] Function App shows "Running" status
- [x] GitHub webhook is configured and active
- [x] Test PR triggers PRism analysis
- [x] Application Insights shows traces
- [x] Logs visible in Azure Portal
- [x] Manual `/analyze` endpoint works

---

## 🐛 Troubleshooting

### Issue: Deployment Script Fails

**Check:**
```powershell
# View deployment errors
az deployment group show `
  --resource-group rg-prism-prod `
  --name prism-deployment-latest `
  --query "properties.error"
```

**Common causes:**
- OpenAI quota not available in region → Try different location
- Resource name conflicts → Change projectName in parameters.json
- Insufficient permissions → Check Azure RBAC roles

---

### Issue: Container App Won't Start

**Check logs:**
```powershell
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --tail 100
```

**Common causes:**
- Image pull failed → Check ACR credentials
- Environment variables missing → Check Container App configuration
- Application crash on startup → Check Python dependencies

---

### Issue: Health Endpoint Returns 503

**Causes:**
- Container not yet started (wait 30-60 seconds)
- Application crashed (check logs)
- Port misconfiguration (should be 8000)

**Fix:**
```powershell
# Restart the container app
az containerapp revision restart `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --revision-name [revision-name]
```

---

### Issue: GitHub Webhook Fails

**Check:**
1. Go to GitHub → Settings → Webhooks
2. Click on the PRism webhook
3. Check "Recent Deliveries"
4. Look for error messages

**Common causes:**
- Wrong URL → Update webhook URL
- SSL certificate error → Verify URL is HTTPS
- 401 Unauthorized → Check webhook secret matches
- 500 Internal Server Error → Check orchestrator logs

---

## 🔄 Making Changes & Redeployment

### Update Application Code Only

```powershell
# Skip infrastructure deployment
.\foundry\deployment_config\scripts\deploy.ps1 -SkipInfrastructure
```

### Update Infrastructure Only

```powershell
# Skip Docker build
.\foundry\deployment_config\scripts\deploy.ps1 -SkipDocker
```

### Full Redeployment

```powershell
# Delete resource group
.\foundry\deployment_config\scripts\cleanup.ps1 -ResourceGroupName "rg-prism-prod"

# Deploy again
.\foundry\deployment_config\scripts\deploy.ps1
```

---

## 📚 Next Steps

Now that PRism is deployed:

1. **Configure additional incident sources**
   - Set up Azure Monitor alerts
   - Configure Log Analytics queries
   - Add custom incident data

2. **Customize agent logic**
   - Modify agent thresholds in `agents/*/`
   - Add new detection patterns
   - Adjust risk scoring

3. **Set up CI/CD**
   - GitHub Actions workflow is at `.github/workflows/deploy-azure.yml`
   - Configure repository secrets
   - Enable automated deployments

4. **Configure VS Code Extension**
   - See `vscode_extension/README.md`
   - Install and configure the PRism sidebar
   - View deployment confidence scores in VS Code

5. **Production Hardening**
   - Enable Private Endpoints
   - Configure VNet integration
   - Set up Azure Front Door
   - Enable Advanced Threat Protection
   - Configure backup and DR

---

## 📊 Cost Optimization

### Development Environment
```json
{
  "environment": "dev",
  "openAiModelCapacity": 10,   // Reduce from 30
  "searchSku": "free"            // Use free tier
}
```

### Production Environment
```json
{
  "environment": "prod",
  "openAiModelCapacity": 50,     // Increase for scale
  "searchSku": "standard"         // Better performance
}
```

---

## 🎓 What We've Accomplished

✅ **Fully automated Azure deployment**
✅ **Infrastructure as Code with Bicep**
✅ **Containerized orchestrator service**
✅ **Serverless Azure Functions**
✅ **Managed identities for security**
✅ **Secrets in Key Vault**
✅ **Comprehensive monitoring**
✅ **GitHub webhook integration**
✅ **One-command deployment & cleanup**
✅ **CI/CD pipeline ready**

---

**Congratulations!** 🎉 PRism is now deployment-ready on Azure with a fully reproducible, automated deployment process.

For questions or issues:
- See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for detailed documentation
- Check [DEPLOYMENT_QUICKSTART.md](DEPLOYMENT_QUICKSTART.md) for quick reference commands
- Review [foundry/deployment_config/README.md](foundry/deployment_config/README.md) for IaC details
