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

@description('GitHub Personal Access Token')
@secure()
param githubToken string

@description('GitHub Webhook Secret')
@secure()
param githubWebhookSecret string = ''

@description('GitHub repository owner')
param githubRepoOwner string = ''

@description('GitHub repository name')
param githubRepoName string = ''

@description('Azure OpenAI model deployment name')
param openAiModelDeployment string = 'gpt-4o'

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

var webhookSecretArray = empty(githubWebhookSecret) ? [] : [
  { name: 'github-webhook-secret', value: githubWebhookSecret }
]
var webhookEnvArray = empty(githubWebhookSecret) ? [] : [
  { name: 'GITHUB_WEBHOOK_SECRET', secretRef: 'github-webhook-secret' }
]
var ghTokenSecretArray = empty(githubToken) ? [] : [
  { name: 'github-token', value: githubToken }
]
var ghTokenEnvArray = empty(githubToken) ? [] : [
  { name: 'GH_PAT', secretRef: 'github-token' }
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
        ghTokenSecretArray,
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
            { name: 'GITHUB_REPO_OWNER', value: githubRepoOwner }
            { name: 'GITHUB_REPO_NAME', value: githubRepoName }
          ], ghTokenEnvArray, webhookEnvArray)
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
