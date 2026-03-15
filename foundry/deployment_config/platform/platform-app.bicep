/*
  PRism Platform — Container App
  ================================
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

// — Secrets —

@description('PostgreSQL admin login')
param pgAdminLogin string = 'prismadmin'

@description('PostgreSQL admin password')
@secure()
param pgAdminPassword string

@description('Orchestrator URL')
param orchestratorUrl string

@description('Platform external origin for CORS')
param platformOrigin string = ''

@description('GitHub OAuth Client ID')
param githubOAuthClientId string = ''

@description('GitHub OAuth Client Secret')
@secure()
param githubOAuthClientSecret string = ''

@description('Azure AD Client ID')
param azureAdClientId string = ''

@description('Azure AD Client Secret')
@secure()
param azureAdClientSecret string = ''

@description('Azure AD Tenant ID')
param azureAdTenantId string = ''

@description('GitHub OAuth redirect URI (callback URL)')
param githubOAuthRedirectUri string = ''

@description('Azure AD redirect URI (callback URL)')
param azureAdRedirectUri string = ''

@description('JWT signing secret')
@secure()
param jwtSecret string

@description('Encryption key for sensitive data at rest')
@secure()
param encryptionKey string

@description('Tags')
param tags object = {
  project: 'PRism'
  component: 'platform'
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
var platformIdentityName = '${namingPrefix}-platform-identity'
var pgServerName = '${namingPrefix}-pg'
var pgDatabaseName = 'prism_platform'
var containerAppName = '${namingPrefix}-platform'
var platformFqdn = '${containerAppName}.${containerAppEnv.properties.defaultDomain}'

// ════════════════════════════════════════════════════════════════
// EXISTING RESOURCES (created by infra.bicep)
// ════════════════════════════════════════════════════════════════

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' existing = {
  name: containerAppEnvName
}

resource platformIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: platformIdentityName
}

resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' existing = {
  name: pgServerName
}

// ════════════════════════════════════════════════════════════════
// CONTAINER APP
// ════════════════════════════════════════════════════════════════

var databaseUrl = 'postgresql+asyncpg://${pgAdminLogin}:${uriComponent(pgAdminPassword)}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDatabaseName}'

resource platformApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${platformIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        corsPolicy: {
          allowedOrigins: empty(platformOrigin) ? [ 'https://${platformFqdn}' ] : [ platformOrigin, 'https://${platformFqdn}' ]
          allowedMethods: [ 'GET', 'POST', 'PUT', 'DELETE', 'OPTIONS' ]
          allowedHeaders: [ '*' ]
        }
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: platformIdentity.id
        }
      ]
      secrets: [
          { name: 'database-url', value: databaseUrl }
          { name: 'jwt-secret', value: jwtSecret }
          { name: 'encryption-key', value: encryptionKey }
          { name: 'github-oauth-client-secret', value: githubOAuthClientSecret }
          { name: 'azure-ad-client-secret', value: azureAdClientSecret }
        ]
    }
    template: {
      containers: [
        {
          name: 'platform'
          image: '${containerRegistry.properties.loginServer}/prism-platform:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'PRISM_ORCHESTRATOR_URL', value: orchestratorUrl }
            { name: 'PLATFORM_ORIGIN', value: platformOrigin }
            { name: 'GITHUB_OAUTH_CLIENT_ID', value: githubOAuthClientId }
            { name: 'GITHUB_OAUTH_CLIENT_SECRET', secretRef: 'github-oauth-client-secret' }
            { name: 'AZURE_AD_CLIENT_ID', value: azureAdClientId }
            { name: 'AZURE_AD_CLIENT_SECRET', secretRef: 'azure-ad-client-secret' }
            { name: 'AZURE_AD_TENANT_ID', value: azureAdTenantId }
            { name: 'GITHUB_OAUTH_REDIRECT_URI', value: githubOAuthRedirectUri }
            { name: 'AZURE_AD_REDIRECT_URI', value: azureAdRedirectUri }
            { name: 'JWT_SECRET', secretRef: 'jwt-secret' }
            { name: 'ENCRYPTION_KEY', secretRef: 'encryption-key' }
            { name: 'AZURE_CLIENT_ID', value: platformIdentity.properties.clientId }
            { name: 'PLATFORM_CONFIG_PATH', value: '/tmp/prism_workspace_config.json' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              periodSeconds: 10
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 5
        rules: [ { name: 'http-scaling', http: { metadata: { concurrentRequests: '50' } } } ]
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════
// OUTPUTS
// ════════════════════════════════════════════════════════════════

output platformAppName string = platformApp.name
output platformFqdn string = platformApp.properties.configuration.ingress.fqdn
output platformUrl string = 'https://${platformApp.properties.configuration.ingress.fqdn}'
