# PRism Azure Deployment - Quick Reference

> This is a condensed quick-reference guide. For detailed instructions, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

## 🎯 One-Command Deployment

```powershell
# 1. Edit parameters
code foundry/deployment_config/parameters.json

# 2. Deploy everything
.\foundry\deployment_config\deploy.ps1
```

That's it! ☕ Grab coffee while Azure deploys (15-20 minutes).

---

## 📋 Pre-Deployment Checklist

- [ ] Azure CLI installed (`az --version`)
- [ ] Docker Desktop running (`docker ps`)
- [ ] Logged into Azure (`az login`)
- [ ] GitHub token created (Settings → Developer settings → Tokens)
- [ ] Webhook secret generated
- [ ] `parameters.json` configured

---

## ⚙️ parameters.json Template

```json
{
  "projectName": {"value": "prism"},
  "environment": {"value": "prod"},
  "location": {"value": "eastus2"},
  "githubToken": {"value": "ghp_YOUR_TOKEN"},
  "githubWebhookSecret": {"value": "YOUR_SECRET"},
  "githubRepoOwner": {"value": "your-org"},
  "githubRepoName": {"value": "your-repo"}
}
```

---

## 🚀 Deployment Commands

### Full Deployment
```powershell
.\foundry\deployment_config\deploy.ps1
```

### Custom Resource Group
```powershell
.\foundry\deployment_config\deploy.ps1 `
  -ResourceGroupName "rg-prism-dev" `
  -Location "westus2"
```

### Update Apps Only (skip infrastructure)
```powershell
.\foundry\deployment_config\deploy.ps1 -SkipInfrastructure
```

---

## ✅ Post-Deployment Verification

### 1. Test Health Endpoint
```powershell
# Get URL from deployment output
curl https://your-app-url.azurecontainerapps.io/health
```

### 2. Configure GitHub Webhook
```
URL:     https://your-app-url.azurecontainerapps.io/webhook/pr
Secret:  [from parameters.json]
Events:  Pull requests
```

### 3. View Logs
```powershell
az containerapp logs show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --follow
```

---

## 🔍 Troubleshooting Commands

### Check Deployment Status
```powershell
az deployment group list `
  --resource-group rg-prism-prod `
  --output table
```

### View Container App Status
```powershell
az containerapp show `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --query "properties.{Status:runningStatus, URL:configuration.ingress.fqdn}"
```

### View Recent Errors
```powershell
az monitor activity-log list `
  --resource-group rg-prism-prod `
  --max-events 10 `
  --query "[?level=='Error']"
```

### Restart Container App
```powershell
az containerapp revision restart `
  --name prism-prod-orchestrator `
  --resource-group rg-prism-prod `
  --revision-name [revision-name]
```

---

## 🗑️ Cleanup

```powershell
# Delete everything (IRREVERSIBLE!)
.\foundry\deployment_config\cleanup.ps1 -ResourceGroupName "rg-prism-prod"
```

---

## 📊 Deployed Resources

| Resource | Purpose | SKU |
|----------|---------|-----|
| Container App | Orchestrator (FastAPI) | 0.5 vCPU, 1GB RAM |
| Azure Functions | Azure MCP Server | Consumption |
| Azure OpenAI | GPT-4o for agents | Standard (30K TPM) |
| AI Search | Incident correlation | Basic |
| Content Safety | Content filtering | S0 |
| Container Registry | Docker images | Basic |
| Key Vault | Secrets storage | Standard |
| App Insights | Monitoring | - |
| Log Analytics | Centralized logging | Pay-as-you-go |

**Estimated Cost**: ~$300-480/month

---

## 🔐 Environment Variables (Auto-Configured)

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

## 🧪 Test Locally

```powershell
# Create environment file
cp .env.template .env

# Edit .env with Azure endpoints from deployment

# Run with Docker Compose
cd foundry/deployment_config
docker-compose up

# Or run directly
uvicorn agents.orchestrator.server:app --reload --port 8000
```

---

## 📞 Quick Support

| Issue | Solution |
|-------|----------|
| Deployment fails | Check `az deployment group show --name ...` |
| Container app won't start | Check `az containerapp logs show ...` |
| OpenAI quota error | Request quota increase in Azure Portal |
| Can't push to ACR | Run `az acr login --name ...` |
| Health check fails | Check Application Insights Failures |

---

## 🔗 Important URLs (After Deployment)

All these are output after deployment:

- **Orchestrator**: `https://prism-prod-orchestrator.*.azurecontainerapps.io`
- **Azure Portal**: `https://portal.azure.com/#@/resource/...`
- **Application Insights**: Check Azure Portal → Monitor
- **Logs**: Azure Portal → Container Apps → Log stream

---

## 📚 Documentation

- [Full Deployment Guide](DEPLOYMENT_GUIDE.md) - Detailed step-by-step
- [Deployment Config README](foundry/deployment_config/README.md) - IaC details
- [Main README](README.md) - PRism overview
- [Architecture](docs/architecture.mermaid) - System design

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

**Ready to deploy? Run:**
```powershell
.\foundry\deployment_config\deploy.ps1
```

**Questions?** Check [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for details.
