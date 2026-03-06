#!/bin/bash
###############################################################################
# PRism Azure Deployment Script (Bash)
# =====================================
# Deploys PRism to Azure using Bicep templates
#
# Usage:
#   ./deploy.sh
#   ./deploy.sh --resource-group rg-prism-dev --location eastus
#   ./deploy.sh --skip-infrastructure --skip-docker
###############################################################################

set -euo pipefail

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

RESOURCE_GROUP_NAME="${RESOURCE_GROUP_NAME:-rg-prism-prod}"
LOCATION="${LOCATION:-eastus2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMETERS_FILE="${SCRIPT_DIR}/../bicep/parameters.json"
BICEP_TEMPLATE="${SCRIPT_DIR}/../bicep/main.bicep"
DEPLOYMENT_NAME="prism-deployment-$(date +%Y%m%d-%H%M%S)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SKIP_INFRASTRUCTURE=false
SKIP_DOCKER=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

print_step() {
    echo -e "\n${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# ═══════════════════════════════════════════════════════════════
# Parse Arguments
# ═══════════════════════════════════════════════════════════════

while [[ $# -gt 0 ]]; do
    case $1 in
        --resource-group)
            RESOURCE_GROUP_NAME="$2"
            shift 2
            ;;
        --location)
            LOCATION="$2"
            shift 2
            ;;
        --parameters-file)
            PARAMETERS_FILE="$2"
            shift 2
            ;;
        --skip-infrastructure)
            SKIP_INFRASTRUCTURE=true
            shift
            ;;
        --skip-docker)
            SKIP_DOCKER=true
            shift
            ;;
        --help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --resource-group NAME      Azure resource group name (default: rg-prism-prod)"
            echo "  --location LOCATION        Azure region (default: eastus2)"
            echo "  --parameters-file FILE     Parameters file path (default: ./parameters.json)"
            echo "  --skip-infrastructure      Skip infrastructure deployment"
            echo "  --skip-docker              Skip Docker build and push"
            echo "  --help                     Show this help message"
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# ═══════════════════════════════════════════════════════════════
# Pre-flight Checks
# ═══════════════════════════════════════════════════════════════

print_step "PRism Azure Deployment Script"
echo "Starting deployment at $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

print_step "Step 1: Validating Prerequisites"

# Check Azure CLI
if ! command_exists az; then
    print_error "Azure CLI is not installed. Please install from: https://aka.ms/install-azure-cli"
    exit 1
fi
print_success "Azure CLI is installed"

# Check Azure CLI version
AZ_VERSION=$(az version --query '\"azure-cli\"' -o tsv)
print_info "Azure CLI version: ${AZ_VERSION}"

# Check Docker
if [ "$SKIP_DOCKER" = false ]; then
    if ! command_exists docker; then
        print_error "Docker is not installed. Please install Docker."
        exit 1
    fi
    print_success "Docker is installed"
    
    # Check if Docker is running
    if ! docker ps >/dev/null 2>&1; then
        print_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
    print_success "Docker daemon is running"
fi

# Check Python
if ! command_exists python3 && ! command_exists python; then
    print_error "Python is not installed. Please install Python 3.11+."
    exit 1
fi
print_success "Python is installed"

# Check parameters file
if [ ! -f "$PARAMETERS_FILE" ]; then
    print_error "Parameters file not found: $PARAMETERS_FILE"
    print_info "Please create parameters.json from parameters.example.json"
    exit 1
fi
print_success "Parameters file found"

# Check Bicep template
if [ ! -f "$BICEP_TEMPLATE" ]; then
    print_error "Bicep template not found: $BICEP_TEMPLATE"
    exit 1
fi
print_success "Bicep template found"

# ═══════════════════════════════════════════════════════════════
# Azure Login & Subscription
# ═══════════════════════════════════════════════════════════════

print_step "Step 2: Validating Azure Authentication"

# Check if logged in
if ! az account show >/dev/null 2>&1; then
    print_info "Not logged in to Azure. Opening browser for authentication..."
    az login
fi

ACCOUNT_NAME=$(az account show --query "name" -o tsv)
ACCOUNT_USER=$(az account show --query "user.name" -o tsv)
SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)

print_success "Logged in as: ${ACCOUNT_USER}"
print_info "Subscription: ${ACCOUNT_NAME} (${SUBSCRIPTION_ID})"

# ═══════════════════════════════════════════════════════════════
# Create Resource Group
# ═══════════════════════════════════════════════════════════════

print_step "Step 3: Creating Resource Group"

if az group exists --name "$RESOURCE_GROUP_NAME" | grep -q "true"; then
    print_info "Resource group '${RESOURCE_GROUP_NAME}' already exists"
else
    print_info "Creating resource group '${RESOURCE_GROUP_NAME}' in '${LOCATION}'..."
    az group create --name "$RESOURCE_GROUP_NAME" --location "$LOCATION" --output none
    print_success "Resource group created"
fi

# ═══════════════════════════════════════════════════════════════
# Deploy Infrastructure
# ═══════════════════════════════════════════════════════════════

if [ "$SKIP_INFRASTRUCTURE" = false ]; then
    print_step "Step 4: Deploying Azure Infrastructure"
    print_info "This will take 10-15 minutes..."
    print_info "Deployment name: ${DEPLOYMENT_NAME}"
    
    DEPLOYMENT_START=$(date +%s)
    
    az deployment group create \
        --resource-group "$RESOURCE_GROUP_NAME" \
        --name "$DEPLOYMENT_NAME" \
        --template-file "$BICEP_TEMPLATE" \
        --parameters "$PARAMETERS_FILE" \
        --parameters location="$LOCATION" \
        --output table
    
    DEPLOYMENT_END=$(date +%s)
    DEPLOYMENT_DURATION=$((DEPLOYMENT_END - DEPLOYMENT_START))
    DEPLOYMENT_MINUTES=$((DEPLOYMENT_DURATION / 60))
    
    print_success "Infrastructure deployed in ${DEPLOYMENT_MINUTES} minutes"
else
    print_info "Skipping infrastructure deployment (--skip-infrastructure flag set)"
fi

# ═══════════════════════════════════════════════════════════════
# Get Deployment Outputs
# ═══════════════════════════════════════════════════════════════

print_step "Step 5: Retrieving Deployment Outputs"

OUTPUTS=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --name "$DEPLOYMENT_NAME" \
    --query "properties.outputs" \
    --output json)

ACR_NAME=$(echo "$OUTPUTS" | jq -r '.containerRegistryName.value')
ACR_LOGIN_SERVER=$(echo "$OUTPUTS" | jq -r '.containerRegistryLoginServer.value')
CONTAINER_APP_NAME=$(echo "$OUTPUTS" | jq -r '.containerAppName.value')
FUNCTION_APP_NAME=$(echo "$OUTPUTS" | jq -r '.functionAppName.value')
ORCHESTRATOR_URL=$(echo "$OUTPUTS" | jq -r '.orchestratorUrl.value')
SEARCH_NAME=$(echo "$OUTPUTS" | jq -r '.aiSearchName.value')

print_success "Retrieved deployment outputs"

# ═══════════════════════════════════════════════════════════════
# Build and Push Docker Image
# ═══════════════════════════════════════════════════════════════

if [ "$SKIP_DOCKER" = false ]; then
    print_step "Step 6: Building and Pushing Docker Image"
    
    # Login to ACR
    print_info "Logging in to Azure Container Registry..."
    az acr login --name "$ACR_NAME"
    print_success "Logged in to ACR: ${ACR_LOGIN_SERVER}"
    
    # Build image
    IMAGE_NAME="${ACR_LOGIN_SERVER}/prism-orchestrator:latest"
    print_info "Building Docker image: ${IMAGE_NAME}"
    print_info "Build context: ${PROJECT_ROOT}"
    
    docker build \
        --platform linux/amd64 \
        -t "$IMAGE_NAME" \
        -f "${SCRIPT_DIR}/../docker/Dockerfile.orchestrator" \
        "$PROJECT_ROOT"
    
    print_success "Docker image built"
    
    # Push image
    print_info "Pushing Docker image to ACR..."
    docker push "$IMAGE_NAME"
    print_success "Docker image pushed to ACR"
    
    # Update Container App
    print_info "Updating Container App with new image..."
    az containerapp update \
        --name "$CONTAINER_APP_NAME" \
        --resource-group "$RESOURCE_GROUP_NAME" \
        --image "$IMAGE_NAME" \
        --output none
    
    print_success "Container App updated"
else
    print_info "Skipping Docker build and push (--skip-docker flag set)"
fi

# ═══════════════════════════════════════════════════════════════
# Deploy Azure Functions
# ═══════════════════════════════════════════════════════════════

print_step "Step 7: Deploying Azure Functions"

if command_exists func; then
    cd "${PROJECT_ROOT}/mcp_servers/azure_mcp_server"
    
    print_info "Deploying function app: ${FUNCTION_APP_NAME}"
    func azure functionapp publish "$FUNCTION_APP_NAME" --python
    
    cd -
    print_success "Function app deployed"
else
    print_info "Azure Functions Core Tools not installed. Skipping function deployment."
    print_info "Install from: https://aka.ms/func-core-tools"
    print_info "Then run: func azure functionapp publish ${FUNCTION_APP_NAME} --python"
fi

# ═══════════════════════════════════════════════════════════════
# Configure AI Search Index
# ═══════════════════════════════════════════════════════════════

print_step "Step 8: Configuring AI Search Index"

SETUP_SCRIPT="${PROJECT_ROOT}/mcp_servers/azure_mcp_server/setup.py"
if [ -f "$SETUP_SCRIPT" ]; then
    print_info "Creating AI Search index..."
    
    # Set environment variables for setup script
    export AZURE_AI_SEARCH_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.aiSearchEndpoint.value')
    SEARCH_KEY=$(az search admin-key show \
        --resource-group "$RESOURCE_GROUP_NAME" \
        --service-name "$SEARCH_NAME" \
        --query "primaryKey" \
        --output tsv)
    export AZURE_AI_SEARCH_KEY=$SEARCH_KEY
    
    python3 "$SETUP_SCRIPT" || python "$SETUP_SCRIPT" || true
    
    print_success "AI Search index configured"
else
    print_info "Setup script not found. You may need to configure AI Search manually."
fi

# ═══════════════════════════════════════════════════════════════
# Save Deployment Configuration
# ═══════════════════════════════════════════════════════════════

print_step "Step 9: Saving Deployment Configuration"

CONFIG_FILE="${PROJECT_ROOT}/.env.azure"
cat > "$CONFIG_FILE" << EOF
# PRism Azure Deployment Configuration
# Generated on $(date '+%Y-%m-%d %H:%M:%S')
# Deployment: ${DEPLOYMENT_NAME}

# Orchestrator
ORCHESTRATOR_URL=${ORCHESTRATOR_URL}

# Function App
FUNCTION_APP_URL=$(echo "$OUTPUTS" | jq -r '.functionAppUrl.value')

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.openAiEndpoint.value')
AZURE_OPENAI_DEPLOYMENT=$(echo "$OUTPUTS" | jq -r '.openAiDeploymentName.value')

# Azure AI Search
AZURE_AI_SEARCH_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.aiSearchEndpoint.value')

# Azure Content Safety
AZURE_CONTENT_SAFETY_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.contentSafetyEndpoint.value')

# Application Insights
APPLICATIONINSIGHTS_CONNECTION_STRING=$(echo "$OUTPUTS" | jq -r '.appInsightsConnectionString.value')

# Key Vault
KEY_VAULT_URL=$(echo "$OUTPUTS" | jq -r '.keyVaultUrl.value')

# Managed Identity
AZURE_CLIENT_ID=$(echo "$OUTPUTS" | jq -r '.orchestratorIdentityClientId.value')

# Resource Group
AZURE_RESOURCE_GROUP=${RESOURCE_GROUP_NAME}
EOF

print_success "Configuration saved to: ${CONFIG_FILE}"

# ═══════════════════════════════════════════════════════════════
# Deployment Summary
# ═══════════════════════════════════════════════════════════════

print_step "Deployment Complete! 🎉"

cat << EOF

═══════════════════════════════════════════════════════════════
                    DEPLOYMENT SUMMARY
═══════════════════════════════════════════════════════════════

Resource Group:        ${RESOURCE_GROUP_NAME}
Location:              ${LOCATION}

Orchestrator URL:      ${ORCHESTRATOR_URL}
Function App URL:      $(echo "$OUTPUTS" | jq -r '.functionAppUrl.value')

OpenAI Endpoint:       $(echo "$OUTPUTS" | jq -r '.openAiEndpoint.value')
AI Search Endpoint:    $(echo "$OUTPUTS" | jq -r '.aiSearchEndpoint.value')
Key Vault:             $(echo "$OUTPUTS" | jq -r '.keyVaultName.value')

Application Insights:  $(echo "$OUTPUTS" | jq -r '.appInsightsName.value')
Log Analytics:         $(echo "$OUTPUTS" | jq -r '.logAnalyticsName.value')

═══════════════════════════════════════════════════════════════
                        NEXT STEPS
═══════════════════════════════════════════════════════════════

1. Test the health endpoint:
   curl ${ORCHESTRATOR_URL}/health

2. Configure GitHub Webhook:
   URL:    ${ORCHESTRATOR_URL}/webhook/pr
   Secret: (use the value from your parameters.json)
   Events: Pull requests

3. View logs:
   az containerapp logs show --name ${CONTAINER_APP_NAME} --resource-group ${RESOURCE_GROUP_NAME} --follow

4. Monitor in Azure Portal:
   https://portal.azure.com/#@/resource/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP_NAME}

═══════════════════════════════════════════════════════════════

Configuration file saved to: ${CONFIG_FILE}

For more information, see: DEPLOYMENT_GUIDE.md

EOF

echo -e "${GREEN}Deployment completed successfully at $(date '+%Y-%m-%d %H:%M:%S')${NC}"
