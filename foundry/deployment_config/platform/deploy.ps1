<#
.SYNOPSIS
    Step 3: Build, push, and deploy the PRism Platform container
.DESCRIPTION
    Requires: infra\deploy.ps1 has been run first.
    1. Retrieves infra outputs (ACR name, etc.)
    2. Builds Docker image and pushes to ACR
    3. Deploys platform-app.bicep (Container App)
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
    [string]$ImageTag,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$DeploymentName = "prism-platform-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..\..\..").Path

function Write-Step  { param([string]$M) Write-Host "`n--- $M ---`n" -ForegroundColor Cyan }
function Write-Ok    { param([string]$M) Write-Host "[OK] $M" -ForegroundColor Green }
function Write-Info  { param([string]$M) Write-Host "[..] $M" -ForegroundColor Yellow }
function Write-Err   { param([string]$M) Write-Host "[!!] $M" -ForegroundColor Red }

# ── Pre-flight ────────────────────────────────────────────────

Write-Step "PRism Platform Deployment"

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
$FilteredParamsFile = Get-FilteredParamsFile -BicepFile "$PSScriptRoot\platform-app.bicep" -ParamsFile $ParametersFile
Write-Ok "Filtered parameters for platform-app.bicep"

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

# Prefer immutable image tags to avoid stale revisions when using mutable "latest".
if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    if ($SkipDocker) {
        $ImageTag = "latest"
        Write-Info "No -ImageTag supplied with -SkipDocker; defaulting to '$ImageTag'"
    } else {
        $gitSha = ""
        if (Get-Command git -ErrorAction SilentlyContinue) {
            $gitSha = (git -C $ProjectRoot rev-parse --short HEAD 2>$null).Trim()
        }
        $timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
        $ImageTag = if ([string]::IsNullOrWhiteSpace($gitSha)) { "build-$timestamp" } else { "build-$timestamp-$gitSha" }
        Write-Info "Generated image tag: $ImageTag"
    }
} else {
    Write-Info "Using image tag: $ImageTag"
}

# ── Docker build & push ──────────────────────────────────────

if (-not $SkipDocker) {
    Write-Step "Building & Pushing Platform Image"

    az acr login --name $acrName
    if ($LASTEXITCODE -ne 0) { Write-Err "ACR login failed"; exit 1 }

    $taggedImage = "${acrLoginServer}/prism-platform:$ImageTag"
    $latestImage = "${acrLoginServer}/prism-platform:latest"
    Write-Info "Building: $taggedImage"

    # Docker sends build progress to stderr; temporarily lower ErrorActionPreference
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $buildOutput = & docker build `
        --platform linux/amd64 `
        -t $taggedImage `
        -f "$PSScriptRoot\Dockerfile" `
        "$ProjectRoot\platform" 2>&1
    $ErrorActionPreference = $savedEAP
    $buildOutput | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker build failed"; exit 1 }
    Write-Ok "Image built"

    $ErrorActionPreference = "Continue"
    $pushOutput = & docker push $taggedImage 2>&1
    $ErrorActionPreference = $savedEAP
    $pushOutput | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push failed"; exit 1 }

    docker tag $taggedImage $latestImage | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker tag latest failed"; exit 1 }

    $ErrorActionPreference = "Continue"
    $pushLatestOutput = & docker push $latestImage 2>&1
    $ErrorActionPreference = $savedEAP
    $pushLatestOutput | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Err "Docker push latest failed"; exit 1 }

    Write-Ok "Images pushed: $taggedImage and $latestImage"
} else {
    Write-Info "Skipping Docker build (--SkipDocker)"
}

# ── Deploy Container App ─────────────────────────────────────

Write-Step "Deploying Platform Container App"
Write-Info "Deployment: $DeploymentName"

$t0 = Get-Date
$result = az deployment group create `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --template-file "$PSScriptRoot\platform-app.bicep" `
    --parameters $FilteredParamsFile `
    --parameters location=$Location `
    --parameters imageTag=$ImageTag `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Err "Platform deployment FAILED"
    Write-Host ($result -join "`n") -ForegroundColor Yellow
    exit 1
}
$elapsed = ((Get-Date) - $t0).TotalMinutes.ToString('0.0')
Write-Ok "Platform deployed in $elapsed minutes"

$appOutputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

$url = $appOutputs.platformUrl.value

Write-Step "Platform Deployed!"
Write-Host @"

  Platform URL:  $url
  Health check:  $url/health

"@ -ForegroundColor Green
