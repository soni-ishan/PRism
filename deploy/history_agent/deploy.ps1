param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = 'eastus',

    [Parameter(Mandatory = $true)]
    [string]$SearchServiceName,

    [switch]$EnableApiKeyAuth,

    [string]$PrincipalObjectId,

    [switch]$GrantContributorAccess
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

Test-AzureCliInstalled

if (-not (Test-AzureLogin)) {
    Write-Host 'No Azure login session found. Opening interactive login...' -ForegroundColor Yellow
    az login | Out-Null
}

$subscriptionId = az account show --query id -o tsv
Write-Host "Using subscription: $subscriptionId" -ForegroundColor Cyan

az group create --name $ResourceGroupName --location $Location --only-show-errors | Out-Null
Write-Host "Resource group ready: $ResourceGroupName" -ForegroundColor Green

$disableLocalAuth = if ($EnableApiKeyAuth) { 'false' } else { 'true' }

$outputsJson = az deployment group create `
    --resource-group $ResourceGroupName `
    --name "history-agent-$(Get-Date -Format 'yyyyMMddHHmmss')" `
    --template-file "$PSScriptRoot/main.bicep" `
    --parameters location=$Location `
                 searchServiceName=$SearchServiceName `
                 disableLocalAuth=$disableLocalAuth `
    --query properties.outputs `
    -o json

$outputs = $outputsJson | ConvertFrom-Json
$searchResourceId = $outputs.searchServiceId.value
$searchEndpoint = $outputs.searchEndpoint.value

if (-not $PrincipalObjectId) {
    $PrincipalObjectId = az ad signed-in-user show --query id -o tsv 2>$null
}

if ($PrincipalObjectId) {
    az role assignment create `
        --assignee-object-id $PrincipalObjectId `
        --assignee-principal-type User `
        --role "Search Index Data Reader" `
        --scope $searchResourceId `
        --only-show-errors | Out-Null

    if ($GrantContributorAccess) {
        az role assignment create `
            --assignee-object-id $PrincipalObjectId `
            --assignee-principal-type User `
            --role "Search Index Data Contributor" `
            --scope $searchResourceId `
            --only-show-errors | Out-Null
    }

    Write-Host 'RBAC role assignment complete for your Entra identity.' -ForegroundColor Green
}
else {
    Write-Warning 'Could not resolve signed-in user object ID automatically. Pass -PrincipalObjectId to assign RBAC.'
}

$apiKey = ''
if ($EnableApiKeyAuth) {
    $apiKey = az search admin-key show --resource-group $ResourceGroupName --service-name $SearchServiceName --query primaryKey -o tsv
}

Write-Host ''
Write-Host 'Deployment complete.' -ForegroundColor Green
Write-Host "AZURE_SEARCH_ENDPOINT=$searchEndpoint"

if ($EnableApiKeyAuth) {
    Write-Host "AZURE_SEARCH_KEY=$apiKey"
}
else {
    Write-Host 'AZURE_SEARCH_KEY=' 
    Write-Host 'Authentication mode: Azure AD (recommended). Use az login locally.'
}

Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host '1) Add the environment values above to your .env file'
Write-Host '2) Create the incidents index: python -m mcp_servers.azure_mcp_server.setup'
Write-Host '3) Run History Agent'
