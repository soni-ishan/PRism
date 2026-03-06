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
    [string]$ParametersFile = "$PSScriptRoot\..\bicep\parameters.json",
    
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

$BicepTemplate = "$PSScriptRoot\..\bicep\main.bicep"
$DeploymentName = "prism-deployment-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$ProjectRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent

# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

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
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error-Custom "Docker daemon is not running. Please start Docker Desktop."
        exit 1
    }
    Write-Success "Docker daemon is running"
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

try {
    $account = az account show --output json | ConvertFrom-Json
    Write-Success "Logged in as: $($account.user.name)"
    Write-Info "Subscription: $($account.name) ($($account.id))"
}
catch {
    Write-Error-Custom "Failed to parse Azure account information: $($_.Exception.Message)"
    exit 1
}

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

    Write-Info "Starting infrastructure deployment (capturing output)..."
    try {
        $deployResult = & az deployment group create `
            --resource-group $ResourceGroupName `
            --name $DeploymentName `
            --template-file $BicepTemplate `
            --parameters $ParametersFile `
            --parameters location=$Location `
            --output json 2>&1
        $deployExit = $LASTEXITCODE
    }
    catch {
        $deployResult = $_.Exception.Message
        $deployExit = 1
    }

    if ($deployExit -ne 0) {
        if ($deployResult -is [System.Array]) { $deployText = ($deployResult -join "`n") } else { $deployText = [string]$deployResult }

        if ($deployText -match "MANIFEST_UNKNOWN" -or $deployText -match "manifest tagged" -or $deployText -match "ContainerAppOperationError") {
            Write-Info "Container App provisioning failed due to image missing in ACR."
            Write-Info "Will continue to build/push the Docker image and then update the Container App to the pushed image."
        }
        elseif ($deployText -match "Additional quota" -or $deployText -match "Dynamic VMs" -or $deployText -match "Unauthorized") {
            Write-Error-Custom "Infrastructure deployment failed due to quota/authorization issues."
            Write-Info "Error details:"
            Write-Host $deployText -ForegroundColor Yellow
            Write-Info "Please check your subscription quotas and role permissions, then retry."
            exit 1
        }
        else {
            Write-Error-Custom "Infrastructure deployment failed"
            Write-Info "Error details:"
            Write-Host $deployText -ForegroundColor Yellow

            Write-Info "Retrieving deployment operations for more details..."
            try {
                $opsRaw = & az deployment operation group list --resource-group $ResourceGroupName --name $DeploymentName --output json 2>&1
                $opsExit = $LASTEXITCODE
            }
            catch {
                $opsRaw = $_.Exception.Message
                $opsExit = 1
            }

            if ($opsExit -eq 0) {
                try {
                    $ops = $opsRaw | ConvertFrom-Json
                    $failedOps = $ops | Where-Object { $_.properties.provisioningState -ne 'Succeeded' }
                    if ($failedOps -and $failedOps.Count -gt 0) {
                        Write-Info "Failed operations:"
                        foreach ($op in $failedOps) {
                            $resType = $op.properties.targetResource.resourceType
                            $resName = $op.properties.targetResource.resourceName
                            Write-Host "- $resType : $resName" -ForegroundColor Yellow
                            if ($op.properties.statusMessage) {
                                $msg = $op.properties.statusMessage
                                try { $msgJson = $msg | ConvertTo-Json -Compress; Write-Host $msgJson -ForegroundColor Yellow }
                                catch { Write-Host $msg -ForegroundColor Yellow }
                            }
                        }
                    }
                    else {
                        Write-Info "No failed operations found in deployment operation list."
                        Write-Host $opsRaw -ForegroundColor Yellow
                    }
                }
                catch {
                    Write-Host $opsRaw -ForegroundColor Yellow
                }
            }
            else {
                Write-Host $opsRaw -ForegroundColor Yellow
            }

            Write-Info "If the error mentions missing image, run the script again to build/push the Docker image and then update the Container App."
            exit 1
        }
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

# When SkipInfrastructure is used, the current $DeploymentName won't exist.
# Fall back to the latest successful deployment in the resource group.
if ($SkipInfrastructure) {
    Write-Info "Looking up latest successful deployment in '$ResourceGroupName'..."
    $latestDeploy = az deployment group list `
        --resource-group $ResourceGroupName `
        --filter "provisioningState eq 'Succeeded'" `
        --query "sort_by([?starts_with(name,'prism-deployment-')], &properties.timestamp)[-1].name" `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($latestDeploy)) {
        Write-Error-Custom "Could not find a previous successful deployment in '$ResourceGroupName'."
        Write-Info "Run a full deployment first (without -SkipInfrastructure)."
        exit 1
    }
    $DeploymentName = $latestDeploy
    Write-Info "Using existing deployment: $DeploymentName"
}

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
        -f "$PSScriptRoot\..\docker\Dockerfile.orchestrator" `
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
    
    try {
        python $setupScript
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "AI Search index configured"
        }
        else {
            Write-Info "AI Search index creation had issues. You may need to run setup.py manually."
        }
    }
    finally {
        # Clear sensitive search key from environment
        Remove-Item Env:\AZURE_AI_SEARCH_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:\AZURE_AI_SEARCH_ENDPOINT -ErrorAction SilentlyContinue
        $searchKey = $null
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
   https://portal.azure.com/#@/resource/subscriptions/$($account.id)/resourceGroups/$ResourceGroupName/providers/Microsoft.Insights/components/$($outputs.appInsightsName.value)

═══════════════════════════════════════════════════════════════

Configuration file saved to: $configFile

For more information, see: DEPLOYMENT_GUIDE.md

"@ -ForegroundColor Green

Write-Host "Deployment completed successfully at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
