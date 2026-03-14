<#
.SYNOPSIS
    Step 1: Deploy all shared infrastructure for PRism (orchestrator + platform)
.DESCRIPTION
    Deploys infra.bicep into a single resource group:
      ACR, Container Apps Env, Key Vault, OpenAI, AI Search, Content Safety,
      Log Analytics, App Insights, PostgreSQL, Managed Identities

    Run this ONCE (or when infra changes), then use:
      ..\orchestrator\deploy.ps1   -- build & deploy orchestrator container
      ..\platform\deploy.ps1       -- build & deploy platform container
.PARAMETER ResourceGroupName
    Azure resource group (default: rg-prism-dev)
.PARAMETER Location
    Azure region (default: eastus2)
.PARAMETER ParametersFile
    Path to parameters.json
.EXAMPLE
    .\deploy.ps1
.EXAMPLE
    .\deploy.ps1 -ResourceGroupName "rg-prism-prod" -Location "eastus"
#>

[CmdletBinding()]
param(
    [string]$ResourceGroupName = "rg-prism-dev",
    [string]$Location = "eastus2",
    [string]$ParametersFile = "$PSScriptRoot\parameters.json"
)

$ErrorActionPreference = "Stop"
$DeploymentName = "prism-infra-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

function Write-Step  { param([string]$M) Write-Host "`n--- $M ---`n" -ForegroundColor Cyan }
function Write-Ok    { param([string]$M) Write-Host "[OK] $M" -ForegroundColor Green }
function Write-Info  { param([string]$M) Write-Host "[..] $M" -ForegroundColor Yellow }
function Write-Err   { param([string]$M) Write-Host "[!!] $M" -ForegroundColor Red }

# ── Pre-flight ────────────────────────────────────────────────

Write-Step "PRism Infrastructure Deployment"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { Write-Err "Azure CLI not found"; exit 1 }
Write-Ok "Azure CLI found"

if (-not (Test-Path $ParametersFile)) {
    Write-Err "Parameters file not found: $ParametersFile"
    Write-Info "Copy parameters.example.json -> parameters.json and fill in secrets."
    exit 1
}
Write-Ok "Parameters file found"

if (-not (Test-Path "$PSScriptRoot\infra.bicep")) { Write-Err "infra.bicep not found"; exit 1 }

# ── Azure auth ────────────────────────────────────────────────

Write-Step "Checking Azure Login"
$acct = az account show --output json 2>$null | ConvertFrom-Json
if (-not $acct) {
    Write-Info "Not logged in -- opening browser..."
    az login
    $acct = az account show --output json | ConvertFrom-Json
}
Write-Ok "Logged in as $($acct.user.name) -- subscription: $($acct.name)"

# ── Resource group ────────────────────────────────────────────

Write-Step "Resource Group"
$exists = az group exists --name $ResourceGroupName
if ($exists -eq "true") { Write-Info "Already exists: $ResourceGroupName" }
else {
    az group create --name $ResourceGroupName --location $Location --output none
    Write-Ok "Created $ResourceGroupName in $Location"
}

# ── Deploy Bicep ──────────────────────────────────────────────

Write-Step "Deploying Infrastructure (this may take 5-10 minutes)"
Write-Info "Deployment: $DeploymentName"

$t0 = Get-Date
$result = az deployment group create `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --template-file "$PSScriptRoot\infra.bicep" `
    --parameters $ParametersFile `
    --parameters location=$Location `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Err "Infrastructure deployment FAILED"
    Write-Host ($result -join "`n") -ForegroundColor Yellow
    exit 1
}
$elapsed = ((Get-Date) - $t0).TotalMinutes.ToString('0.0')
Write-Ok "Infrastructure deployed in $elapsed minutes"

# ── Retrieve outputs ──────────────────────────────────────────

Write-Step "Retrieving Outputs"
$outputs = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $DeploymentName `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

# ── Summary ───────────────────────────────────────────────────

Write-Step "Infrastructure Deployment Complete!"
Write-Host @"

╔═══════════════════════════════════════════════════════════╗
║            PRISM INFRASTRUCTURE SUMMARY                   ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║  Resource Group:  $ResourceGroupName
║  Location:        $Location
║                                                           ║
║  ACR:             $($outputs.containerRegistryLoginServer.value)
║  Container Env:   $($outputs.containerAppEnvName.value)
║  Key Vault:       $($outputs.keyVaultName.value)
║  OpenAI:          $($outputs.openAiEndpoint.value)
║  AI Search:       $($outputs.aiSearchEndpoint.value)
║  PostgreSQL:      $($outputs.pgServerFqdn.value)
║  Database:        $($outputs.pgDatabaseName.value)
║                                                           ║
╠═══════════════════════════════════════════════════════════╣
║  NEXT STEPS:                                              ║
║    ..\orchestrator\deploy.ps1  -- deploy orchestrator     ║
║    ..\platform\deploy.ps1      -- deploy platform         ║
╚═══════════════════════════════════════════════════════════╝

"@ -ForegroundColor Green
