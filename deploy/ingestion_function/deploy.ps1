param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [string]$Location = 'eastus',

    [Parameter(Mandatory = $true)]
    [string]$FunctionAppName,

    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName,

    [Parameter(Mandatory = $true)]
    [string]$AzureLogWorkspaceId,

    [Parameter(Mandatory = $true)]
    [string]$AzureResourceName,

    [Parameter(Mandatory = $true)]
    [string]$AzureSearchEndpoint,

    [string]$AzureSearchKey = '',

    [int]$AzureIngestWindowMinutes = 30,

    [string]$AzureOpenAIEndpoint = '',

    [string]$AzureOpenAIDeployment = '',

    [string]$SearchServiceResourceId,

    [string]$LogAnalyticsWorkspaceResourceId,

    [string]$OpenAIResourceId
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

$outputsJson = az deployment group create `
    --resource-group $ResourceGroupName `
    --name "ingestion-func-$(Get-Date -Format 'yyyyMMddHHmmss')" `
    --template-file "$PSScriptRoot/main.bicep" `
    --parameters location=$Location `
                 functionAppName=$FunctionAppName `
                 storageAccountName=$StorageAccountName `
                 azureLogWorkspaceId=$AzureLogWorkspaceId `
                 azureResourceName=$AzureResourceName `
                 azureSearchEndpoint=$AzureSearchEndpoint `
                 azureSearchKey=$AzureSearchKey `
                 azureIngestWindowMinutes=$AzureIngestWindowMinutes `
                 azureOpenAIEndpoint=$AzureOpenAIEndpoint `
                 azureOpenAIDeployment=$AzureOpenAIDeployment `
    --query properties.outputs `
    -o json

$outputs = $outputsJson | ConvertFrom-Json
$functionPrincipalId = $outputs.functionAppPrincipalId.value
$functionHostName = $outputs.defaultHostName.value
$functionAppResourceId = $outputs.functionAppResourceId.value

Write-Host "Function App deployed: $FunctionAppName" -ForegroundColor Green

if ($SearchServiceResourceId) {
    az role assignment create `
        --assignee-object-id $functionPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Search Index Data Contributor" `
        --scope $SearchServiceResourceId `
        --only-show-errors | Out-Null
    Write-Host 'Assigned role: Search Index Data Contributor' -ForegroundColor Green
}
else {
    Write-Warning 'SearchServiceResourceId not provided. Grant Search Index Data Contributor to Function identity manually.'
}

if ($LogAnalyticsWorkspaceResourceId) {
    az role assignment create `
        --assignee-object-id $functionPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Log Analytics Reader" `
        --scope $LogAnalyticsWorkspaceResourceId `
        --only-show-errors | Out-Null
    Write-Host 'Assigned role: Log Analytics Reader' -ForegroundColor Green
}
else {
    Write-Warning 'LogAnalyticsWorkspaceResourceId not provided. Grant Log Analytics Reader to Function identity manually.'
}

if ($OpenAIResourceId) {
    az role assignment create `
        --assignee-object-id $functionPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Cognitive Services User" `
        --scope $OpenAIResourceId `
        --only-show-errors | Out-Null
    Write-Host 'Assigned role: Cognitive Services User' -ForegroundColor Green
}

Write-Host ''
Write-Host 'Deployment complete.' -ForegroundColor Green
Write-Host "Function host: https://$functionHostName"
Write-Host "Function resource id: $functionAppResourceId"
Write-Host "Function principal id: $functionPrincipalId"
Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Cyan
Write-Host '1) Publish app code: func azure functionapp publish <FUNCTION_APP_NAME> --python'
Write-Host '2) Create incidents index: python -m mcp_servers.azure_mcp_server.setup'
Write-Host '3) Optional: create Event Grid subscription for ingest_from_monitor_alert'
