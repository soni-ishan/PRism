/*
  PRism Orchestrator — Container App
  ====================================
  Deployed AFTER infra.bicep + Docker push.
  References existing shared resources by naming convention.
*/

targetScope = 'resourceGroup'

// ════════════════════════════════════════════════════════════════
// PARAMETERS
// ════════════════════════════════════════════════════════════════

@description('Project name prefix (must match infra)')
param projectName string = 'prism'

@description('Environment (must match infra)')
@allowed([ 'dev', 'staging', 'prod' ])
param environment string = 'dev'

@description('Azure region')
param location string = resourceGroup().location

@description('Container image tag')
param imageTag string = 'latest'

@description('GitHub Webhook Secret')
@secure()
param githubWebhookSecret string = ''

@description('Azure OpenAI model deployment name')
param openAiModelDeployment string = 'gpt-4o'

@description('PostgreSQL admin login')
param pgAdminLogin string = 'prismadmin'

@description('PostgreSQL admin password')
@secure()
param pgAdminPassword string = ''

@description('Encryption key for decrypting PATs from platform DB')
@secure()
param encryptionKey string = ''

@description('Agent timeout in seconds (0 = default 60s)')
param agentTimeoutSeconds int = 60

@description('Tags')
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

var containerRegistryName = '${projectName}acr${uniqueSuffix}'
var containerAppEnvName = '${namingPrefix}-env'
var orchestratorIdentityName = '${namingPrefix}-orchestrator-identity'
var openAiName = '${namingPrefix}-openai-${uniqueSuffix}'
var searchName = '${namingPrefix}-search-${uniqueSuffix}'
var contentSafetyName = '${namingPrefix}-cs-${uniqueSuffix}'
var appInsightsName = '${namingPrefix}-appins'
var keyVaultName = '${projectName}-kv-${uniqueSuffix}'
var containerAppName = '${namingPrefix}-orchestrator'
var pgServerName = '${namingPrefix}-pg'
var pgDatabaseName = 'prism_platform'

var webhookSecretArray = empty(githubWebhookSecret) ? [] : [
  { name: 'github-webhook-secret', value: githubWebhookSecret }
]
var webhookEnvArray = empty(githubWebhookSecret) ? [] : [
  { name: 'GITHUB_WEBHOOK_SECRET', secretRef: 'github-webhook-secret' }
]

// ════════════════════════════════════════════════════════════════
// EXISTING RESOURCES (created by infra.bicep)
// ════════════════════════════════════════════════════════════════

resource orchestratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: orchestratorIdentityName
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: containerAppEnvName
}

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: openAiName
}

resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchName
}

resource contentSafety 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: contentSafetyName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' existing = {
  name: pgServerName
}

// Construct the database URL for platform registration lookups
var databaseUrl = empty(pgAdminPassword) ? '' : 'postgresql+asyncpg://${pgAdminLogin}:${uriComponent(pgAdminPassword)}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDatabaseName}'

// ════════════════════════════════════════════════════════════════
// CONTAINER APP
// ════════════════════════════════════════════════════════════════

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${orchestratorIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: orchestratorIdentity.id
        }
      ]
      secrets: concat(
        [
          { name: 'appinsights-connection-string', value: appInsights.properties.ConnectionString }
          { name: 'openai-api-key', value: openAi.listKeys().key1 }
          { name: 'content-safety-key', value: contentSafety.listKeys().key1 }
          { name: 'search-admin-key', value: search.listAdminKeys().primaryKey }
        ],
        empty(databaseUrl) ? [] : [ { name: 'database-url', value: databaseUrl } ],
        empty(encryptionKey) ? [] : [ { name: 'encryption-key', value: encryptionKey } ],
        webhookSecretArray
      )
    }
    template: {
      containers: [
        {
          name: 'orchestrator'
          image: '${containerRegistry.properties.loginServer}/prism-orchestrator:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat([
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: openAiModelDeployment }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-api-key' }
            { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
            { name: 'AZURE_SEARCH_KEY', secretRef: 'search-admin-key' }
            { name: 'AZURE_CONTENT_SAFETY_ENDPOINT', value: contentSafety.properties.endpoint }
            { name: 'AZURE_CONTENT_SAFETY_KEY', secretRef: 'content-safety-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
            { name: 'KEY_VAULT_URL', value: keyVault.properties.vaultUri }
            { name: 'AZURE_CLIENT_ID', value: orchestratorIdentity.properties.clientId }
            { name: 'PRISM_AGENT_TIMEOUT', value: string(agentTimeoutSeconds) }
          ],
          empty(databaseUrl) ? [] : [ { name: 'DATABASE_URL', secretRef: 'database-url' } ],
          empty(encryptionKey) ? [] : [ { name: 'ENCRYPTION_KEY', secretRef: 'encryption-key' } ],
          webhookEnvArray)
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [ { name: 'http-scaling', http: { metadata: { concurrentRequests: '10' } } } ]
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════
// OUTPUTS
// ════════════════════════════════════════════════════════════════

output containerAppName string = containerApp.name
output orchestratorFqdn string = containerApp.properties.configuration.ingress.fqdn
output orchestratorUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
