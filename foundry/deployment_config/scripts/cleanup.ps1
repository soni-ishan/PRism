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
    [switch]$Force
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

# ===============================================================
# Main Script
# ===============================================================

Write-Host "`n===============================================================" -ForegroundColor Red
Write-Host "  PRism Azure Resource Cleanup" -ForegroundColor Red
Write-Host "===============================================================`n" -ForegroundColor Red

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

# ===============================================================
# Purge Soft-Deleted Resources (BEFORE deleting resource group)
# ===============================================================

Write-Host "`nPurging any previously soft-deleted resources to allow clean redeployment..." -ForegroundColor Cyan

# --- Cognitive Services / Azure OpenAI ---
try {
    $deletedAccounts = az cognitiveservices account list-deleted --output json 2>$null | ConvertFrom-Json
    if ($deletedAccounts) {
        foreach ($acct in $deletedAccounts) {
            $acctName = if ($acct.properties.resourceName) { $acct.properties.resourceName } else { $acct.name }
            $acctLocation = if ($acct.location) { $acct.location } else { 'eastus2' }
            if ($acctName -match '^prism-') {
                Write-Host "  Purging Cognitive Services: $acctName" -ForegroundColor Yellow
                az cognitiveservices account purge --name $acctName --resource-group $ResourceGroupName --location $acctLocation 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $acctName" }
                else { Write-Warning-Custom "Could not purge $acctName (may require manual purge)" }
            }
        }
    }
    else { Write-Host "  No soft-deleted Cognitive Services accounts found." }
}
catch { Write-Warning-Custom "Could not check soft-deleted Cognitive Services: $($_.Exception.Message)" }

# --- Key Vaults ---
try {
    $deletedVaults = az keyvault list-deleted --output json 2>$null | ConvertFrom-Json
    if ($deletedVaults) {
        foreach ($vault in $deletedVaults) {
            $vaultName = $vault.name
            if ($vaultName -match '^prism-') {
                Write-Host "  Purging Key Vault: $vaultName" -ForegroundColor Yellow
                az keyvault purge --name $vaultName 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $vaultName" }
                else { Write-Warning-Custom "Could not purge $vaultName (purge protection may be enabled)" }
            }
        }
    }
    else { Write-Host "  No soft-deleted Key Vaults found." }
}
catch { Write-Warning-Custom "Could not check soft-deleted Key Vaults: $($_.Exception.Message)" }

# --- API Management Services ---
try {
    $deletedApims = az rest --method GET --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.ApiManagement/deletedservices?api-version=2022-08-01" --output json 2>$null | ConvertFrom-Json
    if ($deletedApims.value) {
        foreach ($apim in $deletedApims.value) {
            $apimName = $apim.name
            $apimLocation = $apim.properties.serviceId -replace '.*/', ''
            if ($apimName -match '^prism-') {
                Write-Host "  Purging API Management: $apimName" -ForegroundColor Yellow
                az rest --method DELETE --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.ApiManagement/locations/$($apim.location)/deletedservices/$($apimName)?api-version=2022-08-01" 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $apimName" }
                else { Write-Warning-Custom "Could not purge $apimName" }
            }
        }
    }
    else { Write-Host "  No soft-deleted API Management services found." }
}
catch { Write-Warning-Custom "Could not check soft-deleted API Management: $($_.Exception.Message)" }

# --- App Configuration Stores ---
try {
    $deletedAppConfigs = az rest --method GET --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.AppConfiguration/deletedConfigurationStores?api-version=2023-03-01" --output json 2>$null | ConvertFrom-Json
    if ($deletedAppConfigs.value) {
        foreach ($cfg in $deletedAppConfigs.value) {
            $cfgName = $cfg.name
            $cfgLocation = $cfg.properties.location
            if ($cfgName -match '^prism-') {
                Write-Host "  Purging App Configuration: $cfgName" -ForegroundColor Yellow
                az rest --method POST --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.AppConfiguration/locations/$cfgLocation/deletedConfigurationStores/$cfgName/purge?api-version=2023-03-01" 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $cfgName" }
                else { Write-Warning-Custom "Could not purge $cfgName" }
            }
        }
    }
    else { Write-Host "  No soft-deleted App Configuration stores found." }
}
catch { Write-Warning-Custom "Could not check soft-deleted App Configuration: $($_.Exception.Message)" }

Write-Host ""

# ===============================================================
# Delete Resource Group
# ===============================================================

Write-Host "Deleting resource group '$ResourceGroupName'..." -ForegroundColor Red
Write-Host "This may take several minutes...`n"

az group delete `
    --name $ResourceGroupName `
    --yes

if ($LASTEXITCODE -eq 0) {
    Write-Success "Resource group '$ResourceGroupName' has been deleted."
}
else {
    # Deletion may have been accepted but is still in progress; poll until gone
    Write-Host "Waiting for resource group deletion to complete..." -ForegroundColor Yellow
    $maxAttempts = 60
    $attempt = 0
    while ($attempt -lt $maxAttempts) {
        $attempt++
        $rgCheck = az group exists --name $ResourceGroupName 2>$null
        if ($rgCheck -eq "false") {
            Write-Success "Resource group '$ResourceGroupName' has been deleted."
            break
        }
        Write-Host "  Still deleting... (attempt $attempt/$maxAttempts)" -ForegroundColor Yellow
        Start-Sleep -Seconds 15
    }
    if ($attempt -ge $maxAttempts) {
        Write-Warning-Custom "Timed out waiting for deletion. Check the Azure Portal for status."
    }
}

# ===============================================================
# Post-Deletion Purge (catch resources soft-deleted by the RG delete)
# ===============================================================

Write-Host "`nWaiting 15 seconds for Azure to register soft-deleted resources..." -ForegroundColor Yellow
Start-Sleep -Seconds 15
Write-Host "Purging any resources soft-deleted during group deletion..." -ForegroundColor Cyan

# Re-check Cognitive Services / Azure OpenAI / Content Safety
try {
    $deletedAccounts = az cognitiveservices account list-deleted --output json 2>$null | ConvertFrom-Json
    if ($deletedAccounts) {
        foreach ($acct in $deletedAccounts) {
            $acctName = if ($acct.properties.resourceName) { $acct.properties.resourceName } else { $acct.name }
            $acctLocation = if ($acct.location) { $acct.location } else { 'eastus2' }
            if ($acctName -match '^prism-') {
                Write-Host "  Purging Cognitive Services: $acctName" -ForegroundColor Yellow
                az cognitiveservices account purge --name $acctName --resource-group $ResourceGroupName --location $acctLocation 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $acctName" }
                else { Write-Warning-Custom "Could not purge $acctName (may require manual purge)" }
            }
        }
    }
    else { Write-Host "  No soft-deleted Cognitive Services accounts found." }
}
catch { Write-Warning-Custom "Post-deletion Cognitive Services purge check failed: $($_.Exception.Message)" }

# Re-check Key Vaults
try {
    $deletedVaults = az keyvault list-deleted --output json 2>$null | ConvertFrom-Json
    if ($deletedVaults) {
        foreach ($vault in $deletedVaults) {
            $vaultName = $vault.name
            if ($vaultName -match '^prism-') {
                Write-Host "  Purging Key Vault: $vaultName" -ForegroundColor Yellow
                az keyvault purge --name $vaultName 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $vaultName" }
                else { Write-Warning-Custom "Could not purge $vaultName (purge protection may be enabled)" }
            }
        }
    }
    else { Write-Host "  No soft-deleted Key Vaults found." }
}
catch { Write-Warning-Custom "Post-deletion Key Vault purge check failed: $($_.Exception.Message)" }

# Re-check API Management Services
try {
    $deletedApims = az rest --method GET --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.ApiManagement/deletedservices?api-version=2022-08-01" --output json 2>$null | ConvertFrom-Json
    if ($deletedApims.value) {
        foreach ($apim in $deletedApims.value) {
            $apimName = $apim.name
            if ($apimName -match '^prism-') {
                Write-Host "  Purging API Management: $apimName" -ForegroundColor Yellow
                az rest --method DELETE --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.ApiManagement/locations/$($apim.location)/deletedservices/$($apimName)?api-version=2022-08-01" 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $apimName" }
                else { Write-Warning-Custom "Could not purge $apimName" }
            }
        }
    }
    else { Write-Host "  No soft-deleted API Management services found." }
}
catch { Write-Warning-Custom "Post-deletion API Management purge check failed: $($_.Exception.Message)" }

# Re-check App Configuration Stores
try {
    $deletedAppConfigs = az rest --method GET --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.AppConfiguration/deletedConfigurationStores?api-version=2023-03-01" --output json 2>$null | ConvertFrom-Json
    if ($deletedAppConfigs.value) {
        foreach ($cfg in $deletedAppConfigs.value) {
            $cfgName = $cfg.name
            $cfgLocation = $cfg.properties.location
            if ($cfgName -match '^prism-') {
                Write-Host "  Purging App Configuration: $cfgName" -ForegroundColor Yellow
                az rest --method POST --url "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.AppConfiguration/locations/$cfgLocation/deletedConfigurationStores/$cfgName/purge?api-version=2023-03-01" 2>$null
                if ($LASTEXITCODE -eq 0) { Write-Success "Purged: $cfgName" }
                else { Write-Warning-Custom "Could not purge $cfgName" }
            }
        }
    }
    else { Write-Host "  No soft-deleted App Configuration stores found." }
}
catch { Write-Warning-Custom "Post-deletion App Configuration purge check failed: $($_.Exception.Message)" }

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

Write-Host "`n===============================================================" -ForegroundColor Green
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "===============================================================`n" -ForegroundColor Green