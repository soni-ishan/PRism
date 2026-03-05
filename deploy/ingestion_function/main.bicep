targetScope = 'resourceGroup'

@description('Deployment location')
param location string = resourceGroup().location

@description('Function App name (globally unique)')
param functionAppName string

@description('Storage account name for Functions runtime (lowercase, 3-24 chars)')
param storageAccountName string

@description('App Service plan name for Function App')
param appServicePlanName string = '${functionAppName}-plan'

@description('Application Insights component name')
param appInsightsName string = '${functionAppName}-appi'

@description('Python runtime version for Azure Functions')
@allowed([
  '3.10'
  '3.11'
])
param pythonVersion string = '3.11'

@description('Workspace ID for Log Analytics queries used by ingestion')
param azureLogWorkspaceId string

@description('Default cloud_RoleName for timer/http ingestion runs')
param azureResourceName string

@description('Azure AI Search endpoint (for incidents index)')
param azureSearchEndpoint string

@description('Optional Azure AI Search admin key. Leave empty when using Managed Identity / Entra auth')
@secure()
param azureSearchKey string = ''

@description('Ingest lookback window in minutes')
param azureIngestWindowMinutes int = 30

@description('Optional Azure OpenAI endpoint')
param azureOpenAIEndpoint string = ''

@description('Optional Azure OpenAI deployment name')
param azureOpenAIDeployment string = ''

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource serverFarm 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: serverFarm.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|${pythonVersion}'
      alwaysOn: false
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${listKeys(storage.id, storage.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
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
          name: 'WEBSITE_RUN_FROM_PACKAGE'
          value: '1'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'AZURE_LOG_WORKSPACE_ID'
          value: azureLogWorkspaceId
        }
        {
          name: 'AZURE_RESOURCE_NAME'
          value: azureResourceName
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: azureSearchEndpoint
        }
        {
          name: 'AZURE_SEARCH_KEY'
          value: azureSearchKey
        }
        {
          name: 'AZURE_INGEST_WINDOW_MINUTES'
          value: string(azureIngestWindowMinutes)
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: azureOpenAIEndpoint
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: azureOpenAIDeployment
        }
      ]
    }
  }
  dependsOn: [
    storage
    serverFarm
    appInsights
  ]
}

output functionAppName string = functionApp.name
output functionAppPrincipalId string = functionApp.identity.principalId
output functionAppResourceId string = functionApp.id
output defaultHostName string = functionApp.properties.defaultHostName
output storageAccountId string = storage.id
output appServicePlanId string = serverFarm.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
