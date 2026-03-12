/*
  PRism Azure Infrastructure - Step 1: Foundation Resources
  =========================================================
  Deploys all foundational Azure resources EXCEPT the Container App itself.
  Run this first, then build/push Docker image, then deploy app.bicep.

  Resources deployed:
  - Managed Identities
  - Key Vault + Secrets
  - Log Analytics & Application Insights
  - Azure OpenAI (GPT-4o)
  - Azure AI Search
  - Azure Content Safety
  - Container Registry
  - Container Apps Environment
  - Storage Account (conditional)
  - Azure Functions (conditional)
*/

targetScope = 'resourceGroup'

// ════════════════════════════════════════════════════════════════
// PARAMETERS
// ════════════════════════════════════════════════════════════════

@description('Project name prefix for all resources')
@minLength(3)
@maxLength(10)
param projectName string = 'prism'

@description('Environment name (dev, staging, prod)')
@allowed([
  'dev'
  'staging'
  'prod'
])
param environment string = 'prod'

@description('Azure region for resource deployment')
param location string = resourceGroup().location

@description('GitHub Personal Access Token (stored in Key Vault)')
@secure()
param githubToken string

@description('GitHub Webhook Secret (stored in Key Vault)')
@secure()
param githubWebhookSecret string

@description('GitHub repository owner')
param githubRepoOwner string = ''

@description('GitHub repository name')
param githubRepoName string = ''

@description('Azure OpenAI model deployment name')
param openAiModelDeployment string = 'gpt-4o'

@description('Azure OpenAI model version')
param openAiModelVersion string = '2024-11-20'

@description('Azure OpenAI model capacity (TPM in thousands)')
param openAiModelCapacity int = 30

@description('Deploy Azure Functions MCP server (requires Dynamic VM quota)')
param deployFunctionApp bool = false

@description('Tags to apply to all resources')
param tags object = {
  project: 'PRism'
  environment: environment
  managedBy: 'Bicep'
}

// ════════════════════════════════════════════════════════════════
// VARIABLES
// ════════════════════════════════════════════════════════════════

var uniqueSuffix = uniqueString(resourceGroup().id)
var namingPrefix = '${projectName}-${environment}'

// Resource names
var keyVaultName = '${projectName}-kv-${uniqueSuffix}'
var containerRegistryName = '${projectName}acr${uniqueSuffix}'
var logAnalyticsName = '${namingPrefix}-logs'
var appInsightsName = '${namingPrefix}-appins'
var openAiName = '${namingPrefix}-openai-${uniqueSuffix}'
var searchName = '${namingPrefix}-search-${uniqueSuffix}'
var contentSafetyName = '${namingPrefix}-cs-${uniqueSuffix}'
var storageAccountName = take('${projectName}${environment}st${uniqueSuffix}', 24)
var functionAppName = '${namingPrefix}-func'
var hostingPlanName = '${namingPrefix}-plan'
var containerAppEnvName = '${namingPrefix}-env'

// Managed Identity names
var orchestratorIdentityName = '${namingPrefix}-orchestrator-identity'
var functionIdentityName = '${namingPrefix}-function-identity'

// ════════════════════════════════════════════════════════════════
// MANAGED IDENTITIES
// ════════════════════════════════════════════════════════════════

resource orchestratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: orchestratorIdentityName
  location: location
  tags: tags
}

resource functionIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deployFunctionApp) {
  name: functionIdentityName
  location: location
  tags: tags
}

// ════════════════════════════════════════════════════════════════
// KEY VAULT
// ════════════════════════════════════════════════════════════════

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Store GitHub token in Key Vault
resource githubTokenSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  name: 'github-token'
  parent: keyVault
  properties: {
    value: githubToken
  }
}

// Store GitHub webhook secret in Key Vault
resource webhookSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  name: 'github-webhook-secret'
  parent: keyVault
  properties: {
    value: githubWebhookSecret
  }
}

// Grant orchestrator identity access to Key Vault
resource orchestratorKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, orchestratorIdentity.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant function identity access to Key Vault
resource functionKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(keyVault.id, functionIdentity!.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: functionIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// LOG ANALYTICS & APPLICATION INSIGHTS
// ════════════════════════════════════════════════════════════════

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

// ════════════════════════════════════════════════════════════════
// AZURE OPENAI
// ════════════════════════════════════════════════════════════════

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: openAiName
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Deploy GPT-4o model
resource openAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  name: openAiModelDeployment
  parent: openAi
  sku: {
    name: 'Standard'
    capacity: openAiModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: openAiModelVersion
    }
    raiPolicyName: 'Microsoft.Default'
  }
}

// Grant orchestrator identity access to OpenAI
resource orchestratorOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, orchestratorIdentity.id, 'CognitiveServicesOpenAIUser')
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd') // Cognitive Services OpenAI User
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant function identity access to OpenAI
resource functionOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(openAi.id, functionIdentity!.id, 'CognitiveServicesOpenAIUser')
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd') // Cognitive Services OpenAI User
    principalId: functionIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// AZURE AI SEARCH
// ════════════════════════════════════════════════════════════════

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: false
  }
}

// Grant orchestrator identity access to AI Search
resource orchestratorSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, orchestratorIdentity.id, 'SearchIndexDataContributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7') // Search Index Data Contributor
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant function identity access to AI Search
resource functionSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(search.id, functionIdentity!.id, 'SearchIndexDataContributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7') // Search Index Data Contributor
    principalId: functionIdentity!.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// AZURE CONTENT SAFETY
// ════════════════════════════════════════════════════════════════

resource contentSafety 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: contentSafetyName
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: contentSafetyName
    publicNetworkAccess: 'Enabled'
  }
}

// Grant orchestrator identity access to Content Safety
resource orchestratorContentSafetyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(contentSafety.id, orchestratorIdentity.id, 'CognitiveServicesUser')
  scope: contentSafety
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908') // Cognitive Services User
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// CONTAINER REGISTRY
// ════════════════════════════════════════════════════════════════

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

// Grant orchestrator identity pull access to ACR
resource orchestratorAcrAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, orchestratorIdentity.id, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// STORAGE ACCOUNT (for Azure Functions)
// ════════════════════════════════════════════════════════════════

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = if (deployFunctionApp) {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

// ════════════════════════════════════════════════════════════════
// AZURE FUNCTIONS (Azure MCP Server)
// ════════════════════════════════════════════════════════════════

resource hostingPlan 'Microsoft.Web/serverfarms@2023-01-01' = if (deployFunctionApp) {
  name: hostingPlanName
  location: location
  tags: tags
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
  properties: {
    reserved: true // Required for Linux
  }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = if (deployFunctionApp) {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${functionIdentity!.id}': {}
    }
  }
  properties: {
    serverFarmId: hostingPlan!.id
    reserved: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount!.name};AccountKey=${storageAccount!.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAi.properties.endpoint
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: openAiModelDeployment
        }
        {
          name: 'AZURE_OPENAI_API_KEY'
          value: openAi.listKeys().key1
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${search.name}.search.windows.net'
        }
        {
          name: 'AZURE_SEARCH_KEY'
          value: search.listAdminKeys().primaryKey
        }
        {
          name: 'AZURE_CONTENT_SAFETY_ENDPOINT'
          value: contentSafety.properties.endpoint
        }
        {
          name: 'AZURE_CONTENT_SAFETY_KEY'
          value: contentSafety.listKeys().key1
        }
        {
          name: 'AZURE_LOG_WORKSPACE_ID'
          value: logAnalytics.properties.customerId
        }
        {
          name: 'KEY_VAULT_URL'
          value: keyVault.properties.vaultUri
        }
        {
          name: 'AZURE_CLIENT_ID'
          value: functionIdentity!.properties.clientId
        }
      ]
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
    }
    httpsOnly: true
  }
}

// ════════════════════════════════════════════════════════════════
// CONTAINER APPS ENVIRONMENT
// ════════════════════════════════════════════════════════════════

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════
// OUTPUTS (consumed by Step 2: app.bicep and deploy scripts)
// ════════════════════════════════════════════════════════════════

output resourceGroupName string = resourceGroup().name
output location string = location

// Container Registry
output containerRegistryName string = containerRegistry.name
output containerRegistryLoginServer string = containerRegistry.properties.loginServer

// Container Apps Environment
output containerAppEnvId string = containerAppEnv.id
output containerAppEnvName string = containerAppEnv.name

// Azure OpenAI
output openAiEndpoint string = openAi.properties.endpoint
output openAiDeploymentName string = openAiModelDeployment

// AI Search
output aiSearchEndpoint string = 'https://${search.name}.search.windows.net'
output aiSearchName string = search.name

// Content Safety
output contentSafetyEndpoint string = contentSafety.properties.endpoint

// Application Insights
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey

// Log Analytics
output logAnalyticsWorkspaceId string = logAnalytics.properties.customerId
output logAnalyticsName string = logAnalytics.name

// Key Vault
output keyVaultName string = keyVault.name
output keyVaultUrl string = keyVault.properties.vaultUri

// Managed Identities
output orchestratorIdentityId string = orchestratorIdentity.id
output orchestratorIdentityClientId string = orchestratorIdentity.properties.clientId
output functionIdentityClientId string = deployFunctionApp ? functionIdentity!.properties.clientId : 'not-deployed'

// Storage
output storageAccountName string = deployFunctionApp ? storageAccount!.name : 'not-deployed'

// Function App
output functionAppName string = deployFunctionApp ? functionApp!.name : 'not-deployed'
output functionAppUrl string = deployFunctionApp ? 'https://${functionApp!.properties.defaultHostName}' : 'not-deployed'
