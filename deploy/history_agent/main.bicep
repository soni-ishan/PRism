targetScope = 'resourceGroup'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Unique name for Azure AI Search service (3-60 chars, lowercase letters/numbers/dashes)')
param searchServiceName string

@description('SKU for Azure AI Search')
@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
param searchSku string = 'basic'

@description('Replica count for Azure AI Search')
@minValue(1)
@maxValue(12)
param replicaCount int = 1

@description('Partition count for Azure AI Search')
@minValue(1)
@maxValue(12)
param partitionCount int = 1

@description('Set to true to disable API key auth and require Microsoft Entra ID (recommended)')
param disableLocalAuth bool = true

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  sku: {
    name: searchSku
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: disableLocalAuth
  }
}

output searchServiceId string = searchService.id
output searchEndpoint string = 'https://${searchService.name}.search.windows.net'
output localAuthDisabled bool = disableLocalAuth
