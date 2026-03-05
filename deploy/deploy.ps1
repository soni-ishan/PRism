#!/usr/bin/env pwsh
<#
.SYNOPSIS
    One-command PRism deployment — login once, run this, done.

.DESCRIPTION
    Deploys both History Agent (Azure AI Search) and Ingestion Function
    automatically using sensible defaults and auto-discovered Azure resources.
    
    Requires only:
    - Azure subscription ID
    - Resource group name (created if needed)

.EXAMPLE
    ./deploy/deploy.ps1 -SubscriptionId "12345678-1234-1234-1234-123456789012" -ResourceGroupName "rg-prism-dev"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = 'eastus',

    [switch]$SkipFunctionDeployment
)

$ErrorActionPreference = 'Stop'

function Test-AzureCliInstalled {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw 'Azure CLI is not installed. Install from https://aka.ms/azure-cli'
    }
}

function Test-AzureLogin {
    try {
        az account show --only-show-errors | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-UniqueResourceName {
    param(
        [string]$Base,
        [int]$MaxLength = 24,
        [string]$Suffix = ''
    )
    
    if (-not $Suffix) {
        $Suffix = Get-Random -Minimum 1000 -Maximum 9999
    }
    
    $candidate = "$Base$Suffix"
    if ($candidate.Length -gt $MaxLength) {
        $candidate = $candidate.Substring(0, $MaxLength)
    }
    
    return $candidate
}

function Discover-LogAnalyticsWorkspace {
    param([string]$ResourceGroupName)
    
    $workspace = az monitor log-analytics workspace list --resource-group $ResourceGroupName --query "[0]" -o json 2>$null | ConvertFrom-Json
    if ($workspace -and $workspace.id) {
        return @{
            id = $workspace.id
            workspaceId = $workspace.customerId
            name = $workspace.name
        }
    }
    return $null
}

function Discover-OpenAI {
    param([string]$ResourceGroupName)
    
    $openai = az cognitiveservices account list --resource-group $ResourceGroupName --query "[?kind=='OpenAI'] | [0]" -o json 2>$null | ConvertFrom-Json
    if ($openai -and $openai.id) {
        return @{
            id = $openai.id
            endpoint = $openai.properties.endpoint
            name = $openai.name
        }
    }
    return $null
}

# ──────────────────────────────────────────────────────────────

Test-AzureCliInstalled

if (-not (Test-AzureLogin)) {
    Write-Host "No Azure login session found. Run:" -ForegroundColor Yellow
    Write-Host "  az login" -ForegroundColor Cyan
    exit 1
}

$currentSub = az account show --query id -o tsv
if ($currentSub -ne $SubscriptionId) {
    Write-Host "Setting subscription to $SubscriptionId..." -ForegroundColor Yellow
    az account set --subscription $SubscriptionId
}

Write-Host "🔐 Authenticated. Subscription: $SubscriptionId" -ForegroundColor Green

# Create resource group
az group create --name $ResourceGroupName --location $Location --only-show-errors | Out-Null
Write-Host "📦 Resource group ready: $ResourceGroupName ($Location)" -ForegroundColor Green

# ──────────────────── HISTORY AGENT (SEARCH SERVICE) ────────────────────

Write-Host ""
Write-Host "📍 Deploying History Agent (Azure AI Search)..." -ForegroundColor Cyan

$searchServiceName = Get-UniqueResourceName "prismsearch"
$searchEndpoint = "https://$searchServiceName.search.windows.net"

Write-Host "  → Search service: $searchServiceName"

$searchOutputsJson = az deployment group create `
    --resource-group $ResourceGroupName `
    --name "prism-search-$(Get-Date -Format 'yyyyMMddHHmmss')" `
    --template-file "$PSScriptRoot/history_agent/main.bicep" `
    --parameters location=$Location `
                 searchServiceName=$searchServiceName `
                 disableLocalAuth=true `
    --query properties.outputs `
    -o json

$searchOutputs = $searchOutputsJson | ConvertFrom-Json
$searchResourceId = $searchOutputs.searchServiceId.value

Write-Host "  ✅ Azure AI Search ready" -ForegroundColor Green
Write-Host "     Endpoint: $searchEndpoint"

# Get current user for RBAC
$userObjectId = az ad signed-in-user show --query id -o tsv
if ($userObjectId) {
    az role assignment create `
        --assignee-object-id $userObjectId `
        --assignee-principal-type User `
        --role "Search Index Data Contributor" `
        --scope $searchResourceId `
        --only-show-errors 2>$null | Out-Null
    Write-Host "  ✅ RBAC assigned to your user account"
}

# ──────────────────── INGESTION FUNCTION ────────────────────

if (-not $SkipFunctionDeployment) {
    Write-Host ""
    Write-Host "📍 Deploying Ingestion Function (Azure Functions)..." -ForegroundColor Cyan

    $functionAppName = Get-UniqueResourceName "prism-ingest"
    $storageAccountName = Get-UniqueResourceName "prismingest"

    Write-Host "  → Function app: $functionAppName"
    Write-Host "  → Storage account: $storageAccountName"

    # Discover Log Analytics workspace (required)
    $logWorkspace = Discover-LogAnalyticsWorkspace $ResourceGroupName
    if (-not $logWorkspace) {
        Write-Host "  ⚠️  No Log Analytics workspace found in this resource group." -ForegroundColor Yellow
        Write-Host "     Create one first: az monitor log-analytics workspace create --resource-group $ResourceGroupName --workspace-name <name>"
        Write-Host "     Then re-run this script."
        exit 1
    }

    Write-Host "  ✅ Discovered Log Analytics workspace: $($logWorkspace.name)"

    # Discover OpenAI (optional)
    $openaiInfo = Discover-OpenAI $ResourceGroupName
    $azureOpenAIEndpoint = if ($openaiInfo) { $openaiInfo.endpoint } else { '' }
    $azureOpenAIDeployment = '' # User will need to set this manually
    $openaiResourceId = if ($openaiInfo) { $openaiInfo.id } else { '' }

    if ($openaiInfo) {
        Write-Host "  ✅ Discovered Azure OpenAI: $($openaiInfo.name)"
    }
    else {
        Write-Host "  ℹ️  No OpenAI found (optional, file extraction will use regex fallback)"
    }

    # Deploy Function
    $functionOutputsJson = az deployment group create `
        --resource-group $ResourceGroupName `
        --name "prism-func-$(Get-Date -Format 'yyyyMMddHHmmss')" `
        --template-file "$PSScriptRoot/ingestion_function/main.bicep" `
        --parameters location=$Location `
                     functionAppName=$functionAppName `
                     storageAccountName=$storageAccountName `
                     azureLogWorkspaceId=$($logWorkspace.workspaceId) `
                     azureResourceName="prism-service" `
                     azureSearchEndpoint=$searchEndpoint `
                     azureOpenAIEndpoint=$azureOpenAIEndpoint `
        --query properties.outputs `
        -o json

    $functionOutputs = $functionOutputsJson | ConvertFrom-Json
    $functionPrincipalId = $functionOutputs.functionAppPrincipalId.value
    $functionHostName = $functionOutputs.defaultHostName.value

    Write-Host "  ✅ Azure Function deployed"
    Write-Host "     Host: https://$functionHostName"

    # Assign RBAC to Function identity
    az role assignment create `
        --assignee-object-id $functionPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Search Index Data Contributor" `
        --scope $searchResourceId `
        --only-show-errors 2>$null | Out-Null

    az role assignment create `
        --assignee-object-id $functionPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Log Analytics Reader" `
        --scope $logWorkspace.id `
        --only-show-errors 2>$null | Out-Null

    if ($openaiResourceId) {
        az role assignment create `
            --assignee-object-id $functionPrincipalId `
            --assignee-principal-type ServicePrincipal `
            --role "Cognitive Services User" `
            --scope $openaiResourceId `
            --only-show-errors 2>$null | Out-Null
    }

    Write-Host "  ✅ RBAC assigned to Function identity"
}

# ──────────────────── FINAL SUMMARY ────────────────────

Write-Host ""
Write-Host "✨ PRism deployment complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Configure your .env file with:" -ForegroundColor Cyan
Write-Host "  AZURE_SEARCH_ENDPOINT=$searchEndpoint"
Write-Host "  AZURE_SEARCH_KEY=(leave empty for Entra auth)"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Create incidents index:"
Write-Host "     python -m mcp_servers.azure_mcp_server.setup"
Write-Host "  2. Run History Agent:"
Write-Host "     python agents/history_agent/agent.py <file>"

if (-not $SkipFunctionDeployment) {
    Write-Host "  3. Publish Function code:"
    Write-Host "     func azure functionapp publish $functionAppName --python"
    Write-Host "  4. (Optional) Create Event Grid subscription for alert-driven ingestion"
}
