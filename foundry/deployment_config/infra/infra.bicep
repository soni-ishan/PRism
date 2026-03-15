/*
  PRism — Unified Foundation Infrastructure
  ==========================================
  Single resource group for EVERYTHING:
    - Managed Identities (orchestrator + platform)
    - Key Vault + Secrets
    - Log Analytics & Application Insights
    - Azure OpenAI (GPT-4o)
    - Azure AI Search
    - Azure Content Safety
    - Container Registry (shared by both apps)
    - Container Apps Environment (shared by both apps)
    - PostgreSQL Flexible Server (for platform)
    - Storage Account + Azure Functions (conditional)

  Deploy order:
    1. infra.bicep          (this file)   → deploy-infra.ps1
    2. orchestrator.bicep                 → deploy-orchestrator.ps1
    3. platform-app.bicep                 → deploy-platform.ps1
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
@allowed([ 'dev', 'staging', 'prod' ])
param environment string = 'dev'

@description('Azure region for resource deployment')
param location string = resourceGroup().location

// — GitHub secrets —

@description('GitHub Webhook Secret (stored in Key Vault)')
@secure()
param githubWebhookSecret string = ''

// — OpenAI config —

@description('Azure OpenAI model deployment name')
param openAiModelDeployment string = 'gpt-4o'

@description('Azure OpenAI model version')
param openAiModelVersion string = '2024-11-20'

@description('Azure OpenAI model capacity (TPM in thousands)')
param openAiModelCapacity int = 30

// — PostgreSQL config (for platform) —

@description('PostgreSQL administrator login')
param pgAdminLogin string = 'prismadmin'

@description('PostgreSQL administrator password')
@secure()
param pgAdminPassword string

@description('PostgreSQL SKU tier')
@allowed([ 'Burstable', 'GeneralPurpose', 'MemoryOptimized' ])
param pgSkuTier string = 'Burstable'

@description('PostgreSQL SKU name')
param pgSkuName string = 'Standard_B1ms'

@description('PostgreSQL storage size in GB')
param pgStorageSizeGB int = 32

// — Optional features —

@description('Deploy Azure Functions MCP server')
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

// Shared resources
var containerRegistryName = '${projectName}acr${uniqueSuffix}'
var logAnalyticsName = '${namingPrefix}-logs'
var appInsightsName = '${namingPrefix}-appins'
var containerAppEnvName = '${namingPrefix}-env'
var keyVaultName = '${projectName}-kv-${uniqueSuffix}'

// AI resources
var openAiName = '${namingPrefix}-openai-${uniqueSuffix}'
var searchName = '${namingPrefix}-search-${uniqueSuffix}'
var contentSafetyName = '${namingPrefix}-cs-${uniqueSuffix}'

// Identities
var orchestratorIdentityName = '${namingPrefix}-orchestrator-identity'
var platformIdentityName = '${namingPrefix}-platform-identity'
var functionIdentityName = '${namingPrefix}-function-identity'

// Functions
var storageAccountName = take('${projectName}${environment}st${uniqueSuffix}', 24)
var functionAppName = '${namingPrefix}-func'
var hostingPlanName = '${namingPrefix}-plan'

// PostgreSQL
var pgServerName = '${namingPrefix}-pg'
var pgDatabaseName = 'prism_platform'

// ════════════════════════════════════════════════════════════════
// MANAGED IDENTITIES
// ════════════════════════════════════════════════════════════════

resource orchestratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: orchestratorIdentityName
  location: location
  tags: tags
}

resource platformIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: platformIdentityName
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
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}

resource webhookSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(githubWebhookSecret)) {
  name: 'github-webhook-secret'
  parent: keyVault
  properties: { value: githubWebhookSecret }
}

resource orchestratorKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, orchestratorIdentity.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(keyVault.id, functionIdentity!.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
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
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
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
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow' }
  }
}

resource openAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  name: openAiModelDeployment
  parent: openAi
  sku: { name: 'Standard', capacity: openAiModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4o', version: openAiModelVersion }
    raiPolicyName: 'Microsoft.Default'
  }
}

resource orchestratorOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, orchestratorIdentity.id, 'CognitiveServicesOpenAIUser')
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionOpenAiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(openAi.id, functionIdentity!.id, 'CognitiveServicesOpenAIUser')
  scope: openAi
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
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
  sku: { name: 'basic' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: false
  }
}

resource orchestratorSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, orchestratorIdentity.id, 'SearchIndexDataContributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionSearchAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctionApp) {
  name: guid(search.id, functionIdentity!.id, 'SearchIndexDataContributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
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
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: contentSafetyName
    publicNetworkAccess: 'Enabled'
  }
}

resource orchestratorContentSafetyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(contentSafety.id, orchestratorIdentity.id, 'CognitiveServicesUser')
  scope: contentSafety
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// CONTAINER REGISTRY (shared — both apps push here)
// ════════════════════════════════════════════════════════════════

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true, publicNetworkAccess: 'Enabled' }
}

resource orchestratorAcrAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, orchestratorIdentity.id, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource platformAcrAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, platformIdentity.id, 'AcrPull')
  scope: containerRegistry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: platformIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ════════════════════════════════════════════════════════════════
// CONTAINER APPS ENVIRONMENT (shared — both apps run here)
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
// POSTGRESQL FLEXIBLE SERVER (for platform)
// ════════════════════════════════════════════════════════════════

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: pgServerName
  location: location
  tags: tags
  sku: { name: pgSkuName, tier: pgSkuTier }
  properties: {
    version: '16'
    administratorLogin: pgAdminLogin
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: pgStorageSizeGB }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource pgFirewallAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  name: 'AllowAzureServices'
  parent: pgServer
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// Allow all Azure Container Apps outbound IPs (Container Apps use dynamic IPs)
resource pgFirewallAllowAll 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  name: 'AllowContainerApps'
  parent: pgServer
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '255.255.255.255' }
}

resource pgDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  name: pgDatabaseName
  parent: pgServer
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// ════════════════════════════════════════════════════════════════
// STORAGE ACCOUNT + AZURE FUNCTIONS (conditional)
// ════════════════════════════════════════════════════════════════

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = if (deployFunctionApp) {
  name: storageAccountName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { supportsHttpsTrafficOnly: true, minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}

resource hostingPlan 'Microsoft.Web/serverfarms@2023-01-01' = if (deployFunctionApp) {
  name: hostingPlanName
  location: location
  tags: tags
  sku: { name: 'Y1', tier: 'Dynamic' }
  kind: 'functionapp'
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-01-01' = if (deployFunctionApp) {
  name: functionAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${functionIdentity!.id}': {} }
  }
  properties: {
    serverFarmId: hostingPlan!.id
    reserved: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storageAccount!.name};AccountKey=${storageAccount!.listKeys().keys[0].value};EndpointSuffix=core.windows.net' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
        { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiModelDeployment }
        { name: 'AZURE_OPENAI_API_KEY', value: openAi.listKeys().key1 }
        { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
        { name: 'AZURE_SEARCH_KEY', value: search.listAdminKeys().primaryKey }
        { name: 'AZURE_CONTENT_SAFETY_ENDPOINT', value: contentSafety.properties.endpoint }
        { name: 'AZURE_CONTENT_SAFETY_KEY', value: contentSafety.listKeys().key1 }
        { name: 'AZURE_LOG_WORKSPACE_ID', value: logAnalytics.properties.customerId }
        { name: 'KEY_VAULT_URL', value: keyVault.properties.vaultUri }
        { name: 'AZURE_CLIENT_ID', value: functionIdentity!.properties.clientId }
      ]
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
    }
    httpsOnly: true
  }
}

// ════════════════════════════════════════════════════════════════
// OUTPUTS
// ════════════════════════════════════════════════════════════════

// — Shared —
output resourceGroupName string = resourceGroup().name
output location string = location
output containerRegistryName string = containerRegistry.name
output containerRegistryLoginServer string = containerRegistry.properties.loginServer
output containerAppEnvId string = containerAppEnv.id
output containerAppEnvName string = containerAppEnv.name

// — Orchestrator identity —
output orchestratorIdentityId string = orchestratorIdentity.id
output orchestratorIdentityClientId string = orchestratorIdentity.properties.clientId

// — Platform identity —
output platformIdentityId string = platformIdentity.id
output platformIdentityClientId string = platformIdentity.properties.clientId

// — AI services —
output openAiEndpoint string = openAi.properties.endpoint
output openAiDeploymentName string = openAiModelDeployment
output aiSearchEndpoint string = 'https://${search.name}.search.windows.net'
output aiSearchName string = search.name
output contentSafetyEndpoint string = contentSafety.properties.endpoint

// — Observability —
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
output logAnalyticsWorkspaceId string = logAnalytics.properties.customerId
output logAnalyticsName string = logAnalytics.name

// — Key Vault —
output keyVaultName string = keyVault.name
output keyVaultUrl string = keyVault.properties.vaultUri

// — PostgreSQL —
output pgServerName string = pgServer.name
output pgServerFqdn string = pgServer.properties.fullyQualifiedDomainName
output pgDatabaseName string = pgDatabase.name

// — Functions (conditional) —
output functionIdentityClientId string = deployFunctionApp ? functionIdentity!.properties.clientId : 'not-deployed'
output storageAccountName string = deployFunctionApp ? storageAccount!.name : 'not-deployed'
output functionAppName string = deployFunctionApp ? functionApp!.name : 'not-deployed'
output functionAppUrl string = deployFunctionApp ? 'https://${functionApp!.properties.defaultHostName}' : 'not-deployed'
