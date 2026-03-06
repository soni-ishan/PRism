<#
.SYNOPSIS
    Cleanup PRism Azure resources
.DESCRIPTION
    Removes all Azure resources created by PRism deployment.
    This is DESTRUCTIVE and IRREVERSIBLE!
.PARAMETER ResourceGroupName
    Name of the Azure resource group to delete
.PARAMETER Force
    Skip confirmation prompt
.EXAMPLE
    .\cleanup.ps1 -ResourceGroupName "rg-prism-dev"
.EXAMPLE
    .\cleanup.ps1 -ResourceGroupName "rg-prism-dev" -Force
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,
    
    [Parameter()]
    [switch]$Force,

    [Parameter()]
    [string]$PurgeCognitiveName,

    [Parameter()]
    [string]$PurgeCognitiveLocation
)

$ErrorActionPreference = "Stop"

function Write-Warning-Custom {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

# ═══════════════════════════════════════════════════════════════
# Main Script
# ═══════════════════════════════════════════════════════════════

Write-Host "`n═══════════════════════════════════════════════════════════════" -ForegroundColor Red
Write-Host "  PRism Azure Resource Cleanup" -ForegroundColor Red
Write-Host "═══════════════════════════════════════════════════════════════`n" -ForegroundColor Red

# Check if logged in
try {
    $account = az account show --output json | ConvertFrom-Json
    Write-Host "Logged in as: $($account.user.name)"
    Write-Host "Subscription: $($account.name)`n"
}
catch {
    Write-Error-Custom "Not logged in to Azure. Please run 'az login' first."
    exit 1
}

# Check if resource group exists
$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "false") {
    Write-Error-Custom "Resource group '$ResourceGroupName' does not exist."
    exit 1
}

# List resources in the group
Write-Host "Resources in '$ResourceGroupName':" -ForegroundColor Cyan
az resource list --resource-group $ResourceGroupName --output table
Write-Host ""

# Optional: purge soft-deleted Cognitive Services account
if ($PurgeCognitiveName) {
    if (-not $PurgeCognitiveLocation) {
        Write-Warning-Custom "Purge location not provided; defaulting to 'eastus2'."
        $PurgeCognitiveLocation = 'eastus2'
    }

    Write-Warning-Custom "About to purge soft-deleted Cognitive Services account: $PurgeCognitiveName in $PurgeCognitiveLocation"
    $confirmPurge = Read-Host "Type 'PURGE' to confirm purging the deleted Cognitive Services account (irreversible)"
    if ($confirmPurge -eq 'PURGE') {
        try {
            Write-Host "Purging Cognitive Services account..." -ForegroundColor Yellow
            az cognitiveservices account purge --name $PurgeCognitiveName --resource-group $ResourceGroupName --location $PurgeCognitiveLocation --yes
            if ($LASTEXITCODE -eq 0) {
                Write-Success "Purged soft-deleted Cognitive Services account: $PurgeCognitiveName"
            }
            else {
                Write-Warning-Custom "Purge command exited with code $LASTEXITCODE; check Azure Portal for status."
            }
        }
        catch {
            Write-Warning-Custom "Purge failed: $($_.Exception.Message)"
        }
    }
    else {
        Write-Host "Purge skipped by user." -ForegroundColor Yellow
    }
}

# Confirmation
if (-not $Force) {
    Write-Warning-Custom "This will DELETE the resource group '$ResourceGroupName' and ALL its resources!"
    Write-Warning-Custom "This action is IRREVERSIBLE and cannot be undone!"
    Write-Host ""
    $confirmation = Read-Host "Type 'DELETE' to confirm deletion"
    
    if ($confirmation -ne "DELETE") {
        Write-Host "Cleanup cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# Delete resource group
Write-Host "`nDeleting resource group '$ResourceGroupName'..." -ForegroundColor Red
Write-Host "This may take several minutes...`n"

az group delete `
    --name $ResourceGroupName `
    --yes `
    --no-wait

Write-Success "Deletion initiated. Resources are being removed in the background."
Write-Host "You can check the status in the Azure Portal or by running:"
Write-Host "  az group show --name $ResourceGroupName" -ForegroundColor Cyan

# Clean up local config files
$ProjectRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
$azureEnvFile = Join-Path $ProjectRoot ".env.azure"

if (Test-Path $azureEnvFile) {
    $removeConfig = Read-Host "`nRemove local configuration file (.env.azure)? (y/N)"
    if ($removeConfig -eq "y" -or $removeConfig -eq "Y") {
        Remove-Item $azureEnvFile
        Write-Success "Removed $azureEnvFile"
    }
}

Write-Host "`n═══════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════`n" -ForegroundColor Green
