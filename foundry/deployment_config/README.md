# PRism Deployment Configuration

This directory contains Infrastructure as Code (IaC) templates and deployment scripts for PRism on Azure.

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

## 🚀 Quick Start

### 1. Prerequisites
- Azure CLI installed and logged in
- Docker Desktop running
- Python 3.11+
- Azure subscription with appropriate permissions

### 2. Configure Parameters
```bash
# Copy the template and fill in your values
cp parameters.example.json parameters.json

# Edit parameters.json with your:
# - GitHub token
# - GitHub webhook secret
# - Repository details
```

### 3. Deploy

**Windows (PowerShell):**
```powershell
.\deploy.ps1
```

**Linux/Mac (Bash):**
```bash
chmod +x deploy.sh
./deploy.sh
```

### 4. Verify
```bash
# Check health endpoint
curl https://your-orchestrator-url/health

# View logs
az containerapp logs show --name prism-prod-orchestrator --resource-group rg-prism-prod --follow
```

## 🏗️ What Gets Deployed

The Bicep template creates:

1. **Compute:**
   - Azure Container Apps (Orchestrator)
   - Azure Functions (Azure MCP Server)

2. **AI Services:**
   - Azure OpenAI (GPT-4o)
   - Azure AI Search
   - Azure Content Safety

3. **Infrastructure:**
   - Container Registry (for Docker images)
   - Log Analytics Workspace
   - Application Insights
   - Key Vault (for secrets)
   - Storage Account (for Functions)

4. **Security:**
   - Managed Identities (for secure auth)
   - RBAC role assignments
   - Secrets stored in Key Vault

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

## 🧪 Local Development

Test the orchestrator locally before deploying:

```bash
# Create .env file from template
cp ../.env.template .env

# Fill in Azure resource endpoints in .env

# Start with Docker Compose
docker-compose up

# Or run directly with Python
cd ../..
uvicorn agents.orchestrator.server:app --reload --port 8000
```

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

## 🗑️ Cleanup

**⚠️ WARNING: This deletes ALL resources and is IRREVERSIBLE!**

**Windows:**
```powershell
.\cleanup.ps1 -ResourceGroupName "rg-prism-dev"
```

**Linux/Mac:**
```bash
./cleanup.sh --resource-group rg-prism-dev
```

With force (skip confirmation):
```bash
./cleanup.sh --resource-group rg-prism-dev --force
```

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

## 🐛 Troubleshooting

### Deployment Fails
```bash
# View deployment logs
az deployment group show \
  --resource-group rg-prism-prod \
  --name prism-deployment-latest \
  --output json

# Check activity log
az monitor activity-log list \
  --resource-group rg-prism-prod \
  --max-events 20
```

### Container App Not Starting
```bash
# View logs
az containerapp logs show \
  --name prism-prod-orchestrator \
  --resource-group rg-prism-prod \
  --follow

# Check revision status
az containerapp revision list \
  --name prism-prod-orchestrator \
  --resource-group rg-prism-prod \
  --output table
```

### OpenAI Quota Exceeded
1. Go to Azure Portal → Azure OpenAI
2. Navigate to Quotas
3. Request quota increase for your region

### Can't Push to ACR
```bash
# Re-login to ACR
az acr login --name <your-acr-name>

# Check ACR credentials
az acr credential show --name <your-acr-name>
```

## 📚 Additional Resources

- [Azure Bicep Documentation](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/)
- [Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/)
- [Azure OpenAI Service](https://learn.microsoft.com/en-us/azure/ai-services/openai/)
- [Azure AI Search](https://learn.microsoft.com/en-us/azure/search/)
- [Main Deployment Guide](../../DEPLOYMENT_GUIDE.md)

## 🤝 Contributing

When modifying deployment templates:
1. Test locally with Docker Compose first
2. Deploy to a dev environment
3. Validate all resources are created correctly
4. Update this README with any new parameters or steps
5. Test the cleanup script works correctly

## 📧 Support

For deployment issues, check:
1. This README
2. [DEPLOYMENT_GUIDE.md](../../DEPLOYMENT_GUIDE.md)
3. Azure Portal → Resource Health
4. Application Insights → Failures
