<#
.SYNOPSIS
    Deploys PRism to Azure using Bicep templates
.DESCRIPTION
    This script automates the complete deployment of PRism to Azure:
    - Validates prerequisites
    - Deploys Azure infrastructure via Bicep
    - Builds and pushes Docker images
    - Deploys applications
    - Configures AI Search index
    - Outputs connection details
.PARAMETER ResourceGroupName
    Name of the Azure resource group (default: rg-prism-prod)
.PARAMETER Location
    Azure region for deployment (default: eastus2)
.PARAMETER ParametersFile
    Path to parameters.json file (default: ./parameters.json)
.PARAMETER SkipInfrastructure
    Skip infrastructure deployment (useful for app-only updates)
.PARAMETER SkipDocker
    Skip Docker build and push (useful for infrastructure-only updates)
.EXAMPLE
    .\deploy.ps1
.EXAMPLE
    .\deploy.ps1 -ResourceGroupName "rg-prism-dev" -Location "eastus"
.EXAMPLE
    .\deploy.ps1 -SkipInfrastructure -SkipDocker
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroupName = "rg-prism-prod",
    
    [Parameter()]
    [string]$Location = "eastus2",
    
    [Parameter()]
    [string]$ParametersFile = "$PSScriptRoot\parameters.json",
    
    [Parameter()]
    [switch]$SkipInfrastructure,
    
    [Parameter()]
    [switch]$SkipDocker
)

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$BicepTemplate = "$PSScriptRoot\main.bicep"
$DeploymentName = "prism-deployment-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$ProjectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

function Write-Step {
    param([string]$Message)
    Write-Host "`n═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════════════════════`n" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "ℹ $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

function Test-Command {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

# ═══════════════════════════════════════════════════════════════
# Pre-flight Checks
# ═══════════════════════════════════════════════════════════════

Write-Step "PRism Azure Deployment Script"
Write-Host "Starting deployment at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

Write-Step "Step 1: Validating Prerequisites"

# Check Azure CLI
if (-not (Test-Command "az")) {
    Write-Error-Custom "Azure CLI is not installed. Please install from: https://aka.ms/install-azure-cli"
    exit 1
}
Write-Success "Azure CLI is installed"

# Check Azure CLI version
$azVersion = (az version --output json | ConvertFrom-Json).'azure-cli'
Write-Info "Azure CLI version: $azVersion"

# Check Docker
if (-not $SkipDocker) {
    if (-not (Test-Command "docker")) {
        Write-Error-Custom "Docker is not installed. Please install Docker Desktop."
        exit 1
    }
    Write-Success "Docker is installed"
    
    # Check if Docker is running
    try {
        docker ps | Out-Null
        Write-Success "Docker daemon is running"
    }
    catch {
        Write-Error-Custom "Docker daemon is not running. Please start Docker Desktop."
        exit 1
    }
}

# Check Python
if (-not (Test-Command "python")) {
    Write-Error-Custom "Python is not installed. Please install Python 3.11+."
    exit 1
}
Write-Success "Python is installed"

# Check parameters file
if (-not (Test-Path $ParametersFile)) {
    Write-Error-Custom "Parameters file not found: $ParametersFile"
    Write-Info "Please create parameters.json from parameters.example.json"
    exit 1
}
Write-Success "Parameters file found"

# Check Bicep template
if (-not (Test-Path $BicepTemplate)) {
    Write-Error-Custom "Bicep template not found: $BicepTemplate"
    exit 1
}
Write-Success "Bicep template found"

# ═══════════════════════════════════════════════════════════════
# Azure Login & Subscription
# ═══════════════════════════════════════════════════════════════

Write-Step "Step 2: Validating Azure Authentication"

# Check if logged in
$accountInfo = az account show 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Info "Not logged in to Azure. Opening browser for authentication..."
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Azure login failed"
        exit 1
    }
}

$account = az account show --output json | ConvertFrom-Json
Write-Success "Logged in as: $($account.user.name)"
Write-Info "Subscription: $($account.name) ($($account.id))"

# ═══════════════════════════════════════════════════════════════
# Create Resource Group
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
# Deploy Infrastructure
# ═══════════════════════════════════════════════════════════════

if (-not $SkipInfrastructure) {
    Write-Step "Step 4: Deploying Azure Infrastructure"
    Write-Info "This will take 10-15 minutes..."
    Write-Info "Deployment name: $DeploymentName"
    
    $deploymentStartTime = Get-Date
    
    az deployment group create `
        --resource-group $ResourceGroupName `
        --name $DeploymentName `
        --template-file $BicepTemplate `
        --parameters $ParametersFile `
        --parameters location=$Location `
        --output table
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Infrastructure deployment failed"
        exit 1
    }
    
    $deploymentDuration = (Get-Date) - $deploymentStartTime
    Write-Success "Infrastructure deployed in $($deploymentDuration.TotalMinutes.ToString('0.0')) minutes"
}
else {
    Write-Info "Skipping infrastructure deployment (--SkipInfrastructure flag set)"
}

# ═══════════════════════════════════════════════════════════════
# Get Deployment Outputs
# ═══════════════════════════════════════════════════════════════

Write-Step "Step 5: Retrieving Deployment Outputs"

$outputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    Write-Error-Custom "Failed to retrieve deployment outputs"
    exit 1
}

$acrName = $outputs.containerRegistryName.value
$acrLoginServer = $outputs.containerRegistryLoginServer.value
$containerAppName = $outputs.containerAppName.value
$functionAppName = $outputs.functionAppName.value
$orchestratorUrl = $outputs.orchestratorUrl.value
$searchName = $outputs.aiSearchName.value

Write-Success "Retrieved deployment outputs"

# ═══════════════════════════════════════════════════════════════
# Build and Push Docker Image
# ═══════════════════════════════════════════════════════════════

if (-not $SkipDocker) {
    Write-Step "Step 6: Building and Pushing Docker Image"
    
    # Login to ACR
    Write-Info "Logging in to Azure Container Registry..."
    az acr login --name $acrName
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to login to ACR"
        exit 1
    }
    Write-Success "Logged in to ACR: $acrLoginServer"
    
    # Build image
    $imageName = "${acrLoginServer}/prism-orchestrator:latest"
    Write-Info "Building Docker image: $imageName"
    Write-Info "Build context: $ProjectRoot"
    
    docker build `
        --platform linux/amd64 `
        -t $imageName `
        -f "$PSScriptRoot\Dockerfile.orchestrator" `
        $ProjectRoot
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Docker build failed"
        exit 1
    }
    Write-Success "Docker image built"
    
    # Push image
    Write-Info "Pushing Docker image to ACR..."
    docker push $imageName
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Docker push failed"
        exit 1
    }
    Write-Success "Docker image pushed to ACR"
    
    # Update Container App
    Write-Info "Updating Container App with new image..."
    az containerapp update `
        --name $containerAppName `
        --resource-group $ResourceGroupName `
        --image $imageName `
        --output none
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Failed to update Container App"
        exit 1
    }
    Write-Success "Container App updated"
}
else {
    Write-Info "Skipping Docker build and push (--SkipDocker flag set)"
}

# ═══════════════════════════════════════════════════════════════
# Deploy Azure Functions
# ═══════════════════════════════════════════════════════════════

Write-Step "Step 7: Deploying Azure Functions"

# Check if func CLI is available
if (Test-Command "func") {
    Push-Location "$ProjectRoot\mcp_servers\azure_mcp_server"
    
    Write-Info "Deploying function app: $functionAppName"
    func azure functionapp publish $functionAppName --python
    
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Function app deployment failed"
        Pop-Location
        exit 1
    }
    
    Pop-Location
    Write-Success "Function app deployed"
}
else {
    Write-Info "Azure Functions Core Tools not installed. Skipping function deployment."
    Write-Info "Install from: https://aka.ms/func-core-tools"
    Write-Info "Then run: func azure functionapp publish $functionAppName --python"
}

# ═══════════════════════════════════════════════════════════════
# Configure AI Search Index
# ═══════════════════════════════════════════════════════════════

Write-Step "Step 8: Configuring AI Search Index"

$setupScript = "$ProjectRoot\mcp_servers\azure_mcp_server\setup.py"
if (Test-Path $setupScript) {
    Write-Info "Creating AI Search index..."
    
    # Set environment variables for setup script
    $env:AZURE_AI_SEARCH_ENDPOINT = $outputs.aiSearchEndpoint.value
    $searchKey = az search admin-key show `
        --resource-group $ResourceGroupName `
        --service-name $searchName `
        --query "primaryKey" `
        --output tsv
    $env:AZURE_AI_SEARCH_KEY = $searchKey
    
    python $setupScript
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "AI Search index configured"
    }
    else {
        Write-Info "AI Search index creation had issues. You may need to run setup.py manually."
    }
}
else {
    Write-Info "Setup script not found. You may need to configure AI Search manually."
}

# ═══════════════════════════════════════════════════════════════
# Save Deployment Configuration
# ═══════════════════════════════════════════════════════════════

Write-Step "Step 9: Saving Deployment Configuration"

$configFile = "$ProjectRoot\.env.azure"
$configContent = @"
# PRism Azure Deployment Configuration
# Generated on $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
# Deployment: $DeploymentName

# Orchestrator
ORCHESTRATOR_URL=$orchestratorUrl

# Function App
FUNCTION_APP_URL=$($outputs.functionAppUrl.value)

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=$($outputs.openAiEndpoint.value)
AZURE_OPENAI_DEPLOYMENT=$($outputs.openAiDeploymentName.value)

# Azure AI Search
AZURE_AI_SEARCH_ENDPOINT=$($outputs.aiSearchEndpoint.value)

# Azure Content Safety
AZURE_CONTENT_SAFETY_ENDPOINT=$($outputs.contentSafetyEndpoint.value)

# Application Insights
APPLICATIONINSIGHTS_CONNECTION_STRING=$($outputs.appInsightsConnectionString.value)

# Key Vault
KEY_VAULT_URL=$($outputs.keyVaultUrl.value)

# Managed Identity
AZURE_CLIENT_ID=$($outputs.orchestratorIdentityClientId.value)

# Resource Group
AZURE_RESOURCE_GROUP=$ResourceGroupName
"@

$configContent | Out-File -FilePath $configFile -Encoding UTF8
Write-Success "Configuration saved to: $configFile"

# ═══════════════════════════════════════════════════════════════
# Deployment Summary
# ═══════════════════════════════════════════════════════════════

Write-Step "Deployment Complete! 🎉"

Write-Host @"

═══════════════════════════════════════════════════════════════
                    DEPLOYMENT SUMMARY
═══════════════════════════════════════════════════════════════

Resource Group:        $ResourceGroupName
Location:              $Location

Orchestrator URL:      $orchestratorUrl
Function App URL:      $($outputs.functionAppUrl.value)

OpenAI Endpoint:       $($outputs.openAiEndpoint.value)
AI Search Endpoint:    $($outputs.aiSearchEndpoint.value)
Key Vault:             $($outputs.keyVaultName.value)

Application Insights:  $($outputs.appInsightsName.value)
Log Analytics:         $($outputs.logAnalyticsName.value)

═══════════════════════════════════════════════════════════════
                        NEXT STEPS
═══════════════════════════════════════════════════════════════

1. Test the health endpoint:
   curl $orchestratorUrl/health

2. Configure GitHub Webhook:
   URL:    $orchestratorUrl/webhook/pr
   Secret: (use the value from your parameters.json)
   Events: Pull requests

3. View logs:
   az containerapp logs show --name $containerAppName --resource-group $ResourceGroupName --follow

4. Monitor in Azure Portal:
   https://portal.azure.com/#@/resource/subscriptions/$($account.id)/resourceGroups/$ResourceGroupName

5. View Application Insights:
   https://portal.azure.com/#@/resource$($outputs.appInsightsName.value)

═══════════════════════════════════════════════════════════════

Configuration file saved to: $configFile

For more information, see: DEPLOYMENT_GUIDE.md

"@ -ForegroundColor Green

Write-Host "Deployment completed successfully at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
