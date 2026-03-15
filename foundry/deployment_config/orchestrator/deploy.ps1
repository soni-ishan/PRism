<#
.SYNOPSIS
    Step 2: Build, push, and deploy the PRism Orchestrator container
.DESCRIPTION
    Requires: infra\deploy.ps1 has been run first.
    1. Retrieves infra outputs (ACR name, etc.)
    2. Builds Docker image and pushes to ACR
    3. Deploys orchestrator.bicep (Container App)
.PARAMETER ResourceGroupName
    Must match the infra resource group (default: rg-prism-dev)
.PARAMETER SkipDocker
    Skip Docker build & push (redeploy Bicep only)
.EXAMPLE
    .\deploy.ps1
.EXAMPLE
    .\deploy.ps1 -SkipDocker
#>

[CmdletBinding()]
param(
    [string]$ResourceGroupName = "rg-prism-dev",
    [string]$Location = "eastus2",
    [string]$ParametersFile = "$PSScriptRoot\..\parameters.json",
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$DeploymentName = "prism-orchestrator-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..\..").Path

function Write-Step  { param([string]$M) Write-Host "`n--- $M ---`n" -ForegroundColor Cyan }
function Write-Ok    { param([string]$M) Write-Host "[OK] $M" -ForegroundColor Green }
function Write-Info  { param([string]$M) Write-Host "[..] $M" -ForegroundColor Yellow }
function Write-Err   { param([string]$M) Write-Host "[!!] $M" -ForegroundColor Red }

# ── Pre-flight ────────────────────────────────────────────────

Write-Step "PRism Orchestrator Deployment"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Write-Err "Azure CLI not found"; exit 1 }
if (-not $SkipDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Err "Docker not found"; exit 1 }
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker daemon not running"; exit 1 }
    Write-Ok "Docker ready"
}
if (-not (Test-Path $ParametersFile)) {
    Write-Err "Parameters file not found: $ParametersFile"
    Write-Info "Create ..\parameters.json from parameters.example.json and fill in secrets."
    exit 1
}

# ── Filter parameters to only those declared in the Bicep template ──
function Get-FilteredParamsFile {
    param([string]$BicepFile, [string]$ParamsFile)
    $bicepParams = (Select-String -Path $BicepFile -Pattern '^param\s+(\w+)' | ForEach-Object { $_.Matches.Groups[1].Value })
    $allParams   = (Get-Content $ParamsFile -Raw | ConvertFrom-Json).parameters
    $filtered    = [ordered]@{}
    foreach ($p in $bicepParams) {
        if ($allParams.PSObject.Properties[$p]) { $filtered[$p] = $allParams.$p }
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "prism-params-$(Get-Random).json"
    @{ '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion = '1.0.0.0'; parameters = $filtered } | ConvertTo-Json -Depth 10 | Set-Content $tmp
    return $tmp
}
$FilteredParamsFile = Get-FilteredParamsFile -BicepFile "$PSScriptRoot\orchestrator.bicep" -ParamsFile $ParametersFile
Write-Ok "Filtered parameters for orchestrator.bicep"

# ── Get infra outputs ─────────────────────────────────────────

Write-Step "Retrieving Infrastructure Outputs"

$latestInfra = az deployment group list `
    --resource-group $ResourceGroupName `
    --query "sort_by([?starts_with(name,'prism-infra-')], &properties.timestamp)[-1].name" `
    --output tsv
if ([string]::IsNullOrWhiteSpace($latestInfra)) {
    Write-Err "No infra deployment found. Run ..\infra\deploy.ps1 first."
    exit 1
}
Write-Info "Using infra deployment: $latestInfra"

$outputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $latestInfra `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

$acrName = $outputs.containerRegistryName.value
$acrLoginServer = $outputs.containerRegistryLoginServer.value

# Fallback: query ACR directly if deployment outputs are missing (e.g. partial failure)
if ([string]::IsNullOrWhiteSpace($acrName)) {
    Write-Info "Deployment outputs missing, querying ACR directly..."
    $acrName = (az acr list --resource-group $ResourceGroupName --query "[0].name" -o tsv 2>$null).Trim()
    $acrLoginServer = (az acr list --resource-group $ResourceGroupName --query "[0].loginServer" -o tsv 2>$null).Trim()
}

if ([string]::IsNullOrWhiteSpace($acrName)) {
    Write-Err "No ACR found in $ResourceGroupName. Run ..\infra\deploy.ps1 first."
    exit 1
}
Write-Ok "ACR: $acrLoginServer"

# ── Docker build & push ──────────────────────────────────────

if (-not $SkipDocker) {
    Write-Step "Building & Pushing Orchestrator Image"

    az acr login --name $acrName
    if ($LASTEXITCODE -ne 0) { Write-Err "ACR login failed"; exit 1 }

    $imageName = "${acrLoginServer}/prism-orchestrator:latest"
    Write-Info "Building: $imageName"

    docker build `
        --platform linux/amd64 `
        -t $imageName `
        -f "$PSScriptRoot\Dockerfile" `
        $ProjectRoot
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker build failed"; exit 1 }
    Write-Ok "Image built"

    docker push $imageName
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push failed"; exit 1 }
    Write-Ok "Image pushed"
} else {
    Write-Info "Skipping Docker build (--SkipDocker)"
}

# ── Deploy Container App ─────────────────────────────────────

Write-Step "Deploying Orchestrator Container App"
Write-Info "Deployment: $DeploymentName"

$t0 = Get-Date
$result = az deployment group create `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --template-file "$PSScriptRoot\orchestrator.bicep" `
    --parameters $FilteredParamsFile `
    --parameters location=$Location `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Err "Orchestrator deployment FAILED"
    Write-Host ($result -join "`n") -ForegroundColor Yellow
    exit 1
}
$elapsed = ((Get-Date) - $t0).TotalMinutes.ToString('0.0')
Write-Ok "Orchestrator deployed in $elapsed minutes"

$appOutputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

$url = $appOutputs.orchestratorUrl.value

Write-Step "Orchestrator Deployed!"
Write-Host @"

  Orchestrator URL:  $url
  Health check:      $url/health

"@ -ForegroundColor Green
