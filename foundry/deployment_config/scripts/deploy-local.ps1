<#
.SYNOPSIS
    Deploys PRism infrastructure and generates a local .env for development
.DESCRIPTION
    This script always provisions the Azure foundation (infra.bicep) and then
    fetches all secrets/endpoints to create a .env file so you can run the
    orchestrator locally.

    Steps:
      1. Deploy foundation infrastructure (ACR, OpenAI, AI Search, Key Vault, etc.)
      2. Retrieve all deployment outputs
      3. Fetch API keys from Azure (OpenAI, Search, Content Safety)
      4. Write a comprehensive .env to the project root
.PARAMETER ResourceGroupName
    Name of the Azure resource group (default: rg-prism-prod)
.PARAMETER Location
    Azure region for deployment (default: eastus2)
.PARAMETER ParametersFile
    Path to parameters.json file (default: ./parameters.json)
.EXAMPLE
    .\deploy-local.ps1
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroupName = "rg-prism-prod",

    [Parameter()]
    [string]$Location = "eastus2",

    [Parameter()]
    [string]$ParametersFile = "$PSScriptRoot\..\bicep\parameters.json"
)

# ===============================================================
# Configuration
# ===============================================================

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$InfraBicepTemplate = "$PSScriptRoot\..\bicep\infra.bicep"
$InfraDeploymentName = "prism-infra-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$ProjectRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent

# ===============================================================
# Helper Functions
# ===============================================================

function Write-Step {
    param([string]$Message)
    Write-Host "`n---------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "---------------------------------------------------------------`n" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Test-Command {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

# ===============================================================
# Pre-flight Checks
# ===============================================================

Write-Step "PRism Local Development Setup"
Write-Host "Starting at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

Write-Step "Step 1: Validating Prerequisites"

# Check Azure CLI
if (-not (Test-Command "az")) {
    Write-Error-Custom "Azure CLI is not installed. Please install from: https://aka.ms/install-azure-cli"
    exit 1
}
Write-Success "Azure CLI is installed"

$azVersion = (az version --output json | ConvertFrom-Json).'azure-cli'
Write-Info "Azure CLI version: $azVersion"

# Check parameters file
if (-not (Test-Path $ParametersFile)) {
    Write-Error-Custom "Parameters file not found: $ParametersFile"
    Write-Info "Please create parameters.json from parameters.example.json"
    exit 1
}
Write-Success "Parameters file found"

# Check Bicep template
if (-not (Test-Path $InfraBicepTemplate)) {
    Write-Error-Custom "Infra Bicep template not found: $InfraBicepTemplate"
    exit 1
}
Write-Success "Infra Bicep template found"

# ===============================================================
# Azure Login & Subscription
# ===============================================================

Write-Step "Step 2: Validating Azure Authentication"

$accountInfo = az account show 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Info "Not logged in to Azure. Opening browser for authentication..."
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Azure login failed"
        exit 1
    }
}

try {
    $account = az account show --output json | ConvertFrom-Json
    Write-Success "Logged in as: $($account.user.name)"
    Write-Info "Subscription: $($account.name) ($($account.id))"
}
catch {
    Write-Error-Custom "Failed to parse Azure account information: $($_.Exception.Message)"
    exit 1
}

# ===============================================================
# Create Resource Group
# ===============================================================

Write-Step "Step 3: Creating Resource Group"

$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "true") {
    Write-Info "Resource group '$ResourceGroupName' already exists"
}
else {
    Write-Info "Creating resource group '$ResourceGroupName' in '$Location'..."
    az group create --name $ResourceGroupName --location $Location --output none
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to create resource group"
        exit 1
    }
    Write-Success "Resource group created"
}

# ===============================================================
# Deploy Infrastructure
# ===============================================================

Write-Step "Step 4: Deploying Foundation Infrastructure (infra.bicep)"
Write-Info "Deploying ACR, OpenAI, AI Search, Key Vault, identities, etc."
Write-Info "Deployment name: $InfraDeploymentName"
Write-Info "This may take 5-10 minutes..."

$deploymentStartTime = Get-Date

try {
    $deployResult = az deployment group create `
        --resource-group $ResourceGroupName `
        --name $InfraDeploymentName `
        --template-file $InfraBicepTemplate `
        --parameters $ParametersFile `
        --parameters location=$Location `
        --output json

    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI returned exit code $LASTEXITCODE"
    }
}
catch {
    Write-Error-Custom "Infrastructure deployment failed"
    Write-Host $_ -ForegroundColor Red
    exit 1
}

$deploymentDuration = (Get-Date) - $deploymentStartTime
Write-Success "Foundation infrastructure deployed in $($deploymentDuration.TotalMinutes.ToString('0.0')) minutes"
# ===============================================================
# Retrieve Infrastructure Outputs
# ===============================================================

Write-Step "Step 5: Retrieving Infrastructure Outputs"

$outputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $InfraDeploymentName `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to retrieve deployment outputs"
    exit 1
}

$searchName = $outputs.aiSearchName.value
Write-Success "Retrieved infrastructure outputs"

# ===============================================================
# Fetch Secrets from Azure
# ===============================================================

Write-Step "Step 6: Fetching API Keys from Azure"

Write-Info "Fetching OpenAI API key..."
$openAiName = $outputs.openAiEndpoint.value -replace 'https://',''
$openAiName = $openAiName -replace '\.openai\.azure\.com/?.*',''
$openAiKey = az cognitiveservices account keys list `
    --resource-group $ResourceGroupName `
    --name $openAiName `
    --query "key1" --output tsv
if ($LASTEXITCODE -ne 0) { Write-Error-Custom "Failed to fetch OpenAI key"; exit 1 }
Write-Success "OpenAI API key retrieved"

Write-Info "Fetching AI Search admin key..."
$searchKey = az search admin-key show `
    --resource-group $ResourceGroupName `
    --service-name $searchName `
    --query "primaryKey" --output tsv
if ($LASTEXITCODE -ne 0) { Write-Error-Custom "Failed to fetch Search key"; exit 1 }
Write-Success "AI Search key retrieved"

Write-Info "Fetching Content Safety key..."
$contentSafetyName = $outputs.contentSafetyEndpoint.value -replace 'https://',''
$contentSafetyName = $contentSafetyName -replace '\.cognitiveservices\.azure\.com/?.*',''
$contentSafetyKey = az cognitiveservices account keys list `
    --resource-group $ResourceGroupName `
    --name $contentSafetyName `
    --query "key1" --output tsv
if ($LASTEXITCODE -ne 0) { Write-Error-Custom "Failed to fetch Content Safety key"; exit 1 }
Write-Success "Content Safety key retrieved"

Write-Info "Reading GitHub token from parameters file..."
$params = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$ghToken = $params.parameters.githubToken.value
$ghWebhookSecret = $params.parameters.githubWebhookSecret.value


# ===============================================================
# Write Local .env
# ===============================================================

Write-Step "Step 7: Writing .env File"

$localEnvFile = "$ProjectRoot\.env"
$localEnvContent = @"
# ── Azure OpenAI (GPT-4o-mini, Sweden Central) ──
AZURE_OPENAI_ENDPOINT=$($outputs.openAiEndpoint.value)
AZURE_OPENAI_API_KEY=$openAiKey
AZURE_OPENAI_DEPLOYMENT=$($outputs.openAiDeploymentName.value)
AZURE_OPENAI_API_VERSION=

# ── Azure AI Foundry ──
AZURE_FOUNDRY_PROJECT_CONNECTION_STRING=

# ── Azure Identity (Service Principal) ──
AZURE_CLIENT_ID=$($outputs.orchestratorIdentityClientId.value)
AZURE_TENANT_ID=$($account.tenantId)
AZURE_CLIENT_SECRET=
AZURE_SUBSCRIPTION_ID=$($account.id)

# ── Azure Content Safety ──
AZURE_CONTENT_SAFETY_ENDPOINT=$($outputs.contentSafetyEndpoint.value)
AZURE_CONTENT_SAFETY_KEY=$contentSafetyKey

# ── Azure AI Search (History Agent) ──
AZURE_SEARCH_ENDPOINT=$($outputs.aiSearchEndpoint.value)
AZURE_SEARCH_KEY=$searchKey

# ── Log Analytics (Incident Ingestion) ──
AZURE_LOG_WORKSPACE_ID=$($outputs.logAnalyticsWorkspaceId.value)

# ── Application Insights (Tracing) ──
APPLICATIONINSIGHTS_CONNECTION_STRING=$($outputs.appInsightsConnectionString.value)

# ── GitHub (Fine-grained PAT) ──
GH_PAT=$ghToken
"@

$localEnvContent | Out-File -FilePath $localEnvFile -Encoding ASCII -NoNewline
Write-Success "Local .env written to: $localEnvFile"

# ===============================================================
# Deployment Summary
# ===============================================================

Write-Step "Local Setup Complete!"

Write-Host @"

===============================================================
            LOCAL DEVELOPMENT ENVIRONMENT READY
===============================================================

Resource Group:        $ResourceGroupName
Location:              $Location
Local .env:            $localEnvFile

OpenAI Endpoint:       $($outputs.openAiEndpoint.value)
AI Search Endpoint:    $($outputs.aiSearchEndpoint.value)
Content Safety:        $($outputs.contentSafetyEndpoint.value)
Key Vault:             $($outputs.keyVaultName.value)

Application Insights:  $($outputs.appInsightsName.value)
Log Analytics:         $($outputs.logAnalyticsName.value)

===============================================================
                        NEXT STEPS
===============================================================

1. Start the local orchestrator:
   python -m uvicorn agents.orchestrator.server:app --port 8000 --reload

2. Test the health endpoint:
   curl http://localhost:8000/health

3. For webhook testing, expose with ngrok:
   ngrok http 8000

4. Configure GitHub Webhook (use ngrok URL):
   URL:    https://<ngrok-id>.ngrok.io/webhook/pr
   Secret: (from your parameters.json)
   Events: Pull requests

===============================================================

"@ -ForegroundColor Green

Write-Host "Completed at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
