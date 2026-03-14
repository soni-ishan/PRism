<#
.SYNOPSIS
    Generates .env files for PRism orchestrator and platform from deployed Azure resources.
.DESCRIPTION
    Queries the rg-prism-dev resource group and writes:
      - <repo>\.env              (orchestrator)
      - <repo>\platform\.env     (platform)
.PARAMETER ResourceGroupName
    Azure resource group (default: rg-prism-dev)
#>

[CmdletBinding()]
param(
    [string]$ResourceGroupName = "rg-prism-dev"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..").Path

function Write-Step  { param([string]$M) Write-Host "`n--- $M ---`n" -ForegroundColor Cyan }
function Write-Ok    { param([string]$M) Write-Host "[OK] $M" -ForegroundColor Green }
function Write-Info  { param([string]$M) Write-Host "[..] $M" -ForegroundColor Yellow }
function Write-Err   { param([string]$M) Write-Host "[!!] $M" -ForegroundColor Red }

# ── Discover resources ────────────────────────────────────────

Write-Step "Querying Azure resources in $ResourceGroupName"

# OpenAI
Write-Info "OpenAI..."
$openaiName = (az cognitiveservices account list --resource-group $ResourceGroupName --query "[?kind=='OpenAI'].name" -o tsv 2>$null).Trim()
$openaiEndpoint = (az cognitiveservices account show --resource-group $ResourceGroupName --name $openaiName --query "properties.endpoint" -o tsv 2>$null).Trim()
$openaiKey = (az cognitiveservices account keys list --resource-group $ResourceGroupName --name $openaiName --query "key1" -o tsv 2>$null).Trim()

# Get deployment name
$openaiDeployment = (az cognitiveservices account deployment list --resource-group $ResourceGroupName --name $openaiName --query "[0].name" -o tsv 2>$null).Trim()

Write-Ok "OpenAI: $openaiEndpoint (deployment: $openaiDeployment)"

# Content Safety
Write-Info "Content Safety..."
$csName = (az cognitiveservices account list --resource-group $ResourceGroupName --query "[?kind=='ContentSafety'].name" -o tsv 2>$null).Trim()
$csEndpoint = (az cognitiveservices account show --resource-group $ResourceGroupName --name $csName --query "properties.endpoint" -o tsv 2>$null).Trim()
$csKey = (az cognitiveservices account keys list --resource-group $ResourceGroupName --name $csName --query "key1" -o tsv 2>$null).Trim()
Write-Ok "Content Safety: $csEndpoint"

# AI Search
Write-Info "AI Search..."
$searchName = (az search service list --resource-group $ResourceGroupName --query "[0].name" -o tsv 2>$null).Trim()
$searchEndpoint = "https://${searchName}.search.windows.net"
$searchKey = (az search admin-key show --resource-group $ResourceGroupName --service-name $searchName --query "primaryKey" -o tsv 2>$null).Trim()
Write-Ok "AI Search: $searchEndpoint"

# App Insights
Write-Info "Application Insights..."
$aiJson = az resource show --resource-group $ResourceGroupName --resource-type "Microsoft.Insights/components" --name prism-dev-appins --query "{name:name, connectionString:properties.ConnectionString}" -o json 2>$null | ConvertFrom-Json
$aiName = $aiJson.name
$aiConnStr = $aiJson.connectionString
Write-Ok "App Insights: $aiName"

# Log Analytics
Write-Info "Log Analytics..."
$laCustomerId = (az resource show --resource-group $ResourceGroupName --resource-type "Microsoft.OperationalInsights/workspaces" --name prism-dev-logs --query "properties.customerId" -o tsv 2>$null).Trim()
Write-Ok "Log Analytics workspace ID: $laCustomerId"

# Key Vault
Write-Info "Key Vault..."
$kvName = (az keyvault list --resource-group $ResourceGroupName --query "[0].name" -o tsv 2>$null).Trim()
$kvUrl = (az keyvault show --name $kvName --resource-group $ResourceGroupName --query "properties.vaultUri" -o tsv 2>$null).Trim()
Write-Ok "Key Vault: $kvUrl"

# Managed Identities
Write-Info "Managed Identities..."
$orchClientId = (az identity show --resource-group $ResourceGroupName --name prism-dev-orchestrator-identity --query "clientId" -o tsv 2>$null).Trim()
$orchTenantId = (az identity show --resource-group $ResourceGroupName --name prism-dev-orchestrator-identity --query "tenantId" -o tsv 2>$null).Trim()
Write-Ok "Orchestrator identity: $orchClientId"

# PostgreSQL
Write-Info "PostgreSQL..."
$pgFqdn = (az postgres flexible-server show --resource-group $ResourceGroupName --name prism-dev-pg --query "fullyQualifiedDomainName" -o tsv 2>$null).Trim()
Write-Ok "PostgreSQL: $pgFqdn"

# Subscription
$subId = (az account show --query "id" -o tsv 2>$null).Trim()

# ── Write orchestrator .env ───────────────────────────────────

Write-Step "Writing orchestrator .env"

$orchEnv = @"
# ── Azure OpenAI ──
AZURE_OPENAI_ENDPOINT=$openaiEndpoint
AZURE_OPENAI_API_KEY=$openaiKey
AZURE_OPENAI_DEPLOYMENT=$openaiDeployment
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# ── Azure Identity ──
AZURE_CLIENT_ID=$orchClientId
AZURE_TENANT_ID=$orchTenantId
AZURE_SUBSCRIPTION_ID=$subId

# ── Azure Content Safety ──
AZURE_CONTENT_SAFETY_ENDPOINT=$csEndpoint
AZURE_CONTENT_SAFETY_KEY=$csKey

# ── Azure AI Search (History Agent) ──
AZURE_SEARCH_ENDPOINT=$searchEndpoint
AZURE_SEARCH_KEY=$searchKey

# ── Log Analytics (Incident Ingestion) ──
AZURE_LOG_WORKSPACE_ID=$laCustomerId

# ── Application Insights (Tracing) ──
APPLICATIONINSIGHTS_CONNECTION_STRING=$aiConnStr

# ── Key Vault ──
KEY_VAULT_URL=$kvUrl

# ── GitHub (Fine-grained PAT) ──
GH_PAT=
"@

$orchEnvPath = Join-Path $ProjectRoot ".env"
$orchEnv | Set-Content -Path $orchEnvPath -Encoding UTF8
Write-Ok "Written to $orchEnvPath"

# ── Write platform .env ──────────────────────────────────────

Write-Step "Writing platform .env"

$platEnv = @"
# ── Platform Configuration ──
PRISM_ORCHESTRATOR_URL=http://localhost:8000
DATABASE_URL=postgresql+asyncpg://prismadmin:Pr1sm%40Dev2026!@${pgFqdn}:5432/prism_platform?ssl=require

# ── GitHub OAuth ──
GITHUB_OAUTH_CLIENT_ID=
GITHUB_OAUTH_CLIENT_SECRET=
GITHUB_OAUTH_REDIRECT_URI=http://localhost:8080/api/setup/github/callback

# ── Azure AD OAuth ──
AZURE_AD_CLIENT_ID=
AZURE_AD_CLIENT_SECRET=
AZURE_AD_TENANT_ID=common
AZURE_AD_REDIRECT_URI=http://localhost:8080/api/setup/azure/callback

# ── Security ──
JWT_SECRET=
ENCRYPTION_KEY=

# ── Optional ──
PLATFORM_ORIGIN=
PLATFORM_CONFIG_PATH=/tmp/prism_workspace_config.json
"@

$platEnvPath = Join-Path $ProjectRoot "platform\.env"
$platEnv | Set-Content -Path $platEnvPath -Encoding UTF8
Write-Ok "Written to $platEnvPath"

# ── Summary ───────────────────────────────────────────────────

Write-Step "Environment Files Generated!"
Write-Host @"

  Orchestrator .env:  $orchEnvPath
  Platform .env:      $platEnvPath

  REMAINING manual steps:
    1. Set GH_PAT in .env (GitHub Personal Access Token)
    2. Set GITHUB_OAUTH_CLIENT_ID/SECRET in platform\.env
    3. Set AZURE_AD_CLIENT_ID/SECRET in platform\.env
    4. Set JWT_SECRET in platform\.env
    5. Set ENCRYPTION_KEY in platform\.env

"@ -ForegroundColor Green
