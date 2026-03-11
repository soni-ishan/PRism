/*
  PRism Azure Infrastructure - Step 2: Container App Deployment
  =============================================================
  Deploys the Container App (Orchestrator) AFTER:
  1. Step 1 (infra.bicep) has provisioned all foundation resources
  2. Docker image has been built and pushed to ACR

  This template references existing resources by name and deploys
  only the Container App into the pre-created environment.
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

@description('GitHub Personal Access Token')
@secure()
param githubToken string

@description('GitHub Webhook Secret')
@secure()
param githubWebhookSecret string

@description('GitHub repository owner/organization')
param githubRepoOwner string

@description('GitHub repository name')
param githubRepoName string

@description('Azure OpenAI model deployment name')
param openAiModelDeployment string = 'gpt-4o'

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

// Resource names (must match infra.bicep)
var containerRegistryName = '${projectName}acr${uniqueSuffix}'
var openAiName = '${namingPrefix}-openai'
var searchName = '${namingPrefix}-search-${uniqueSuffix}'
var contentSafetyName = '${namingPrefix}-contentsafety'
var appInsightsName = '${namingPrefix}-appins'
var keyVaultName = '${projectName}-kv-${uniqueSuffix}'
var containerAppEnvName = '${namingPrefix}-env'
var containerAppName = '${namingPrefix}-orchestrator'
var orchestratorIdentityName = '${namingPrefix}-orchestrator-identity'

// Optional webhook secret — only included when a non-empty value is provided
var webhookSecretArray = empty(githubWebhookSecret) ? [] : [
  {
    name: 'github-webhook-secret'
    value: githubWebhookSecret
  }
]
var webhookEnvArray = empty(githubWebhookSecret) ? [] : [
  {
    name: 'GITHUB_WEBHOOK_SECRET'
    secretRef: 'github-webhook-secret'
  }
]

// ════════════════════════════════════════════════════════════════
// EXISTING RESOURCES (created by infra.bicep in Step 1)
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
// CONTAINER APP (Orchestrator)
// ════════════════════════════════════════════════════════════════

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${orchestratorIdentity.id}': {}
    }
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
          {
            name: 'appinsights-connection-string'
            value: appInsights.properties.ConnectionString
          }
          {
            name: 'github-token'
            value: githubToken
          }
          {
            name: 'openai-api-key'
            value: openAi.listKeys().key1
          }
          {
            name: 'content-safety-key'
            value: contentSafety.listKeys().key1
          }
          {
            name: 'search-admin-key'
            value: search.listAdminKeys().primaryKey
          }
        ],
        webhookSecretArray
      )
    }
    template: {
      containers: [
        {
          name: 'orchestrator'
          image: '${containerRegistry.properties.loginServer}/prism-orchestrator:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat([
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
              secretRef: 'openai-api-key'
            }
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${search.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_KEY'
              secretRef: 'search-admin-key'
            }
            {
              name: 'AZURE_CONTENT_SAFETY_ENDPOINT'
              value: contentSafety.properties.endpoint
            }
            {
              name: 'AZURE_CONTENT_SAFETY_KEY'
              secretRef: 'content-safety-key'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            {
              name: 'GH_PAT'
              secretRef: 'github-token'
            }
            {
              name: 'KEY_VAULT_URL'
              value: keyVault.properties.vaultUri
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: orchestratorIdentity.properties.clientId
            }
            {
              name: 'GITHUB_REPO_OWNER'
              value: githubRepoOwner
            }
            {
              name: 'GITHUB_REPO_NAME'
              value: githubRepoName
            }
          ], webhookEnvArray)
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [
          {
            name: 'http-scale'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════
// OUTPUTS
// ════════════════════════════════════════════════════════════════

output containerAppName string = containerApp.name
output orchestratorUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
