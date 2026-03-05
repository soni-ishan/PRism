#!/bin/bash
###############################################################################
# PRism Azure Resource Cleanup (Bash)
# ====================================
# Removes all Azure resources created by PRism deployment
# This is DESTRUCTIVE and IRREVERSIBLE!
#
# Usage:
#   ./cleanup.sh --resource-group rg-prism-dev
#   ./cleanup.sh --resource-group rg-prism-dev --force
###############################################################################

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

RESOURCE_GROUP_NAME=""
FORCE=false

# ═══════════════════════════════════════════════════════════════
# Parse Arguments
# ═══════════════════════════════════════════════════════════════

while [[ $# -gt 0 ]]; do
    case $1 in
        --resource-group)
            RESOURCE_GROUP_NAME="$2"
            shift 2
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help)
            echo "Usage: $0 --resource-group <name> [--force]"
            echo ""
            echo "Options:"
            echo "  --resource-group NAME      Azure resource group name (required)"
            echo "  --force                    Skip confirmation prompt"
            echo "  --help                     Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}✗ Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [ -z "$RESOURCE_GROUP_NAME" ]; then
    echo -e "${RED}✗ Resource group name is required${NC}"
    echo "Usage: $0 --resource-group <name>"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════
# Main Script
# ═══════════════════════════════════════════════════════════════

echo -e "\n${RED}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${RED}  PRism Azure Resource Cleanup${NC}"
echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}\n"

# Check if logged in
if ! az account show >/dev/null 2>&1; then
    echo -e "${RED}✗ Not logged in to Azure. Please run 'az login' first.${NC}"
    exit 1
fi

ACCOUNT_USER=$(az account show --query "user.name" -o tsv)
ACCOUNT_NAME=$(az account show --query "name" -o tsv)
echo "Logged in as: ${ACCOUNT_USER}"
echo "Subscription: ${ACCOUNT_NAME}"
echo ""

# Check if resource group exists
if ! az group exists --name "$RESOURCE_GROUP_NAME" | grep -q "true"; then
    echo -e "${RED}✗ Resource group '${RESOURCE_GROUP_NAME}' does not exist.${NC}"
    exit 1
fi

# List resources in the group
echo -e "${CYAN}Resources in '${RESOURCE_GROUP_NAME}':${NC}"
az resource list --resource-group "$RESOURCE_GROUP_NAME" --output table
echo ""

# Confirmation
if [ "$FORCE" = false ]; then
    echo -e "${YELLOW}⚠ This will DELETE the resource group '${RESOURCE_GROUP_NAME}' and ALL its resources!${NC}"
    echo -e "${YELLOW}⚠ This action is IRREVERSIBLE and cannot be undone!${NC}"
    echo ""
    read -p "Type 'DELETE' to confirm deletion: " confirmation
    
    if [ "$confirmation" != "DELETE" ]; then
        echo "Cleanup cancelled."
        exit 0
    fi
fi

# Delete resource group
echo -e "\n${RED}Deleting resource group '${RESOURCE_GROUP_NAME}'...${NC}"
echo "This may take several minutes..."
echo ""

az group delete \
    --name "$RESOURCE_GROUP_NAME" \
    --yes \
    --no-wait

echo -e "${GREEN}✓ Deletion initiated. Resources are being removed in the background.${NC}"
echo "You can check the status in the Azure Portal or by running:"
echo -e "  ${CYAN}az group show --name ${RESOURCE_GROUP_NAME}${NC}"

# Clean up local config files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
AZURE_ENV_FILE="${PROJECT_ROOT}/.env.azure"

if [ -f "$AZURE_ENV_FILE" ]; then
    echo ""
    read -p "Remove local configuration file (.env.azure)? (y/N): " remove_config
    if [ "$remove_config" = "y" ] || [ "$remove_config" = "Y" ]; then
        rm "$AZURE_ENV_FILE"
        echo -e "${GREEN}✓ Removed ${AZURE_ENV_FILE}${NC}"
    fi
fi

echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Cleanup Complete${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}\n"
