<#
.SYNOPSIS
  Deletes a PRism resource group and purges all soft-deleted resources.

.DESCRIPTION
  1. Prompts for the resource group name
  2. Discovers Key Vaults and Cognitive Services accounts inside it
  3. Deletes the entire resource group (synchronous wait)
  4. Purges soft-deleted Key Vaults and Cognitive Services so names can be reused

.PARAMETER Force
  Skip the confirmation prompt.
#>
param(
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

# ── Ask for resource group name ────────────────────────────────
$ResourceGroupName = Read-Host "Enter the resource group name to delete"
if ([string]::IsNullOrWhiteSpace($ResourceGroupName)) {
    Write-Host "No resource group name provided. Aborted." -ForegroundColor Yellow
    exit 1
}

# ── Verify the group exists ────────────────────────────────────
$exists = az group exists --name $ResourceGroupName | ConvertFrom-Json
if (-not $exists) {
    Write-Host "Resource group '$ResourceGroupName' does not exist. Nothing to do." -ForegroundColor Yellow
    exit 0
}

# ── Discover resources to purge BEFORE deleting the RG ─────────
Write-Host "Scanning resources in '$ResourceGroupName' ..." -ForegroundColor Cyan

# Key Vaults
$vaults = az keyvault list --resource-group $ResourceGroupName --query "[].name" -o tsv 2>$null
if ($vaults) { $vaults = $vaults -split "`n" | Where-Object { $_.Trim() } } else { $vaults = @() }

# Cognitive Services (OpenAI, Content Safety, AI Search)
$cogAccounts = az cognitiveservices account list --resource-group $ResourceGroupName `
    --query "[].{name:name, kind:kind, location:location}" -o json 2>$null | ConvertFrom-Json
if (-not $cogAccounts) { $cogAccounts = @() }

Write-Host "  Key Vaults to purge:          $($vaults.Count)" -ForegroundColor Gray
Write-Host "  Cognitive Services to purge:   $($cogAccounts.Count)" -ForegroundColor Gray

# ── Confirm ────────────────────────────────────────────────────
if (-not $Force) {
    Write-Host ""
    Write-Host "WARNING: This will permanently delete resource group '$ResourceGroupName'" -ForegroundColor Red
    Write-Host "         and ALL resources inside it, then purge soft-deleted resources." -ForegroundColor Red
    Write-Host ""
    $answer = Read-Host "Type the resource group name to confirm deletion"
    if ($answer -ne $ResourceGroupName) {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 1
    }
}

# ── Delete the resource group (synchronous) ────────────────────
Write-Host "`nDeleting resource group '$ResourceGroupName' ..." -ForegroundColor Cyan
az group delete --name $ResourceGroupName --yes
Write-Host "Resource group deleted." -ForegroundColor Green

# ── Purge soft-deleted Key Vaults ──────────────────────────────
foreach ($vault in $vaults) {
    Write-Host "Purging soft-deleted Key Vault '$vault' ..." -ForegroundColor Cyan
    az keyvault purge --name $vault 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Purged." -ForegroundColor Green
    } else {
        Write-Host "  Skipped (not soft-deleted or purge protection enabled)." -ForegroundColor Yellow
    }
}

# ── Purge soft-deleted Cognitive Services ──────────────────────
foreach ($acct in $cogAccounts) {
    Write-Host "Purging soft-deleted Cognitive Services '$($acct.name)' ($($acct.kind)) ..." -ForegroundColor Cyan
    az cognitiveservices account purge --name $acct.name --resource-group $ResourceGroupName --location $acct.location 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Purged." -ForegroundColor Green
    } else {
        Write-Host "  Skipped (not soft-deleted or already purged)." -ForegroundColor Yellow
    }
}

Write-Host "`nCleanup complete." -ForegroundColor Green
