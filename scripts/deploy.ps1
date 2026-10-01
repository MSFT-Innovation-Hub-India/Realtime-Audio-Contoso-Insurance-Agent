<#
.SYNOPSIS
  Builds the Contoso Insurance voice agent image with ACR Tasks and deploys it to Azure Container Apps
  with a system-assigned managed identity and the RBAC it needs. Safe to re-run (idempotent).

.DESCRIPTION
  1. az acr build                     -> <acr>.azurecr.io/<app>:<tag>
  2. Create the Container App (first run) in the existing environment with a system-assigned identity
  3. Grant the identity: AcrPull (ACR), Cognitive Services User + Foundry User (Foundry / Voice Live),
     Search Index Data Reader (AI Search), contained DB user + db_datareader (Azure SQL)
  4. Configure registry (managed identity), Jira API token as a Container App secret, app settings from .env,
     ingress (port 8000, sticky sessions), scale and resources, then roll out the new image

  Prerequisites: Azure CLI with the containerapp extension, `az login` as a user who can create role
  assignments (Owner / User Access Administrator) and who is the Entra admin of the SQL server.
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = "pcdotai-agent",
    [string]$Environment = "pcdotai-agent",
    [string]$AcrName = "pcdotaiagentd10b5a",
    [string]$AppName = "contoso-insurance-voice-agent",
    [string]$ImageTag = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env"),
    [string]$FoundryResourceGroup = "rg-foundry-common",
    [string]$FoundryName = "ai-foundry-common-ihub",
    [string]$SearchResourceGroup = "ai-search-common-rg",
    [string]$SearchName = "ai-search-vector-001",
    [int]$MinReplicas = 1,
    [int]$MaxReplicas = 3,
    [string]$Cpu = "1.0",
    [string]$Memory = "2Gi",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
# az acr build streams build logs with non-ASCII characters; avoid 'charmap' encode errors on Windows consoles
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Az {
    $output = & az @args
    if ($LASTEXITCODE -ne 0) { throw "az $($args -join ' ') failed (exit $LASTEXITCODE)" }
    return $output
}

function Read-DotEnv([string]$Path) {
    $values = [ordered]@{}
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $key, $value = $line -split '=', 2
        $key = $key.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($key -and $value) { $values[$key] = $value }
    }
    return $values
}

function Set-RoleAssignment([string]$PrincipalId, [string]$RoleId, [string]$Scope, [string]$Label) {
    # (avoid parentheses in --query: az.cmd on Windows passes args through cmd.exe, which mis-parses them)
    $existing = @(Invoke-Az role assignment list --assignee $PrincipalId --role $RoleId --scope $Scope --query "[].id" -o tsv) | Where-Object { $_ }
    if ($existing.Count -gt 0) {
        Write-Host "  = $Label already assigned"
        return
    }
    Invoke-Az role assignment create --assignee-object-id $PrincipalId --assignee-principal-type ServicePrincipal `
        --role $RoleId --scope $Scope -o none | Out-Null
    Write-Host "  + $Label assigned"
}

# Built-in role definition IDs
$ContainerAppsApiVersion = "2024-03-01"
$ROLE_ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
$ROLE_COGNITIVE_SERVICES_USER = "a97b65f3-24c7-4388-baec-2e87135dc908"
$ROLE_FOUNDRY_USER = "53ca6127-db72-4b80-b1b0-d745d6d5456d"           # formerly "Azure AI User"
$ROLE_SEARCH_INDEX_DATA_READER = "1407120a-92aa-4202-b7e9-c0e197c71c8f"

Write-Host "==> Reading configuration from $EnvFile"
$settings = Read-DotEnv $EnvFile
foreach ($required in "AZURE_VOICE_LIVE_ENDPOINT", "ai_search_url", "az_db_server", "az_db_database", "attlassian_api_key") {
    if (-not $settings.Contains($required)) { throw "$required is missing in $EnvFile" }
}
$jiraApiKey = $settings["attlassian_api_key"]
$settings.Remove("attlassian_api_key")
$settings["AZURE_TOKEN_CREDENTIALS"] = "ManagedIdentityCredential"

$acrId = Invoke-Az acr show -n $AcrName --query id -o tsv
$acrServer = Invoke-Az acr show -n $AcrName --query loginServer -o tsv
$envId = Invoke-Az containerapp env show -n $Environment -g $ResourceGroup --query id -o tsv
$foundryId = Invoke-Az cognitiveservices account show -n $FoundryName -g $FoundryResourceGroup --query id -o tsv
$searchId = Invoke-Az search service show -n $SearchName -g $SearchResourceGroup --query id -o tsv
$image = "$acrServer/${AppName}:$ImageTag"

# ---------------------------------------------------------------- 1. build image in ACR
if (-not $SkipBuild) {
    Write-Host "==> Building $image with ACR Tasks"
    Push-Location $ProjectRoot
    try {
        # --no-logs: the Windows az CLI can crash while streaming build logs ('charmap' encode errors),
        # so queue the build and poll its status instead. The run id is only printed on stderr.
        $queued = & az acr build --registry $AcrName --image "${AppName}:$ImageTag" --image "${AppName}:latest" `
            --platform linux/amd64 --file Dockerfile --no-logs --no-wait . 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { $queued | Out-Host; throw "az acr build failed to queue" }
    } finally {
        Pop-Location
    }
    $runId = ($queued | Select-String -Pattern "Queued a build with ID: (\S+)").Matches[0].Groups[1].Value
    if (-not $runId) { $queued | Out-Host; throw "Could not determine the ACR run id" }
    Write-Host "    ACR run $runId queued"
    do {
        Start-Sleep -Seconds 15
        $status = Invoke-Az acr task show-run -r $AcrName --run-id $runId --query status -o tsv
        Write-Host "    $status"
    } while ($status -in @("Queued", "Started", "Running"))
    if ($status -ne "Succeeded") {
        & az acr task logs -r $AcrName --run-id $runId 2>&1 | Select-Object -Last 60 | Out-Host
        throw "ACR build $runId finished with status $status"
    }
}

# ---------------------------------------------------------------- 2. container app + identity
$exists = (& az containerapp show -n $AppName -g $ResourceGroup --query name -o tsv 2>$null)
$newIdentity = $false
if (-not $exists) {
    $newIdentity = $true
    Write-Host "==> Creating Container App $AppName (bootstrap image, system-assigned identity)"
    # A public bootstrap image is used first, because the identity must exist (and hold AcrPull)
    # before the app can pull from the private registry.
    Invoke-Az containerapp create -n $AppName -g $ResourceGroup --environment $envId `
        --image "mcr.microsoft.com/k8se/quickstart:latest" --system-assigned `
        --ingress external --target-port 80 --min-replicas 0 --max-replicas 1 -o none | Out-Null
} else {
    Write-Host "==> Container App $AppName exists; ensuring system-assigned identity"
    Invoke-Az containerapp identity assign -n $AppName -g $ResourceGroup --system-assigned -o none | Out-Null
}
$principalId = Invoke-Az containerapp identity show -n $AppName -g $ResourceGroup --query principalId -o tsv
Write-Host "    principalId: $principalId"

# ---------------------------------------------------------------- 3. RBAC
Write-Host "==> Granting RBAC to the managed identity"
Set-RoleAssignment $principalId $ROLE_ACR_PULL $acrId "AcrPull on $AcrName"
Set-RoleAssignment $principalId $ROLE_COGNITIVE_SERVICES_USER $foundryId "Cognitive Services User on $FoundryName"
Set-RoleAssignment $principalId $ROLE_FOUNDRY_USER $foundryId "Foundry User on $FoundryName"
Set-RoleAssignment $principalId $ROLE_SEARCH_INDEX_DATA_READER $searchId "Search Index Data Reader on $SearchName"

Write-Host "==> Granting Azure SQL access (contained user + db_datareader)"
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python (Join-Path $PSScriptRoot "grant_sql_access.py") $settings["az_db_server"] $settings["az_db_database"] $AppName $principalId
if ($LASTEXITCODE -ne 0) { throw "Granting SQL access failed" }

# ---------------------------------------------------------------- 4. configure + roll out
Write-Host "==> Waiting for AcrPull to propagate (avoids a ~15 min revision timeout on image pull)"
$acrPullVisible = $false
for ($attempt = 1; $attempt -le 10 -and -not $acrPullVisible; $attempt++) {
    $roles = Invoke-Az role assignment list --assignee $principalId --scope $acrId --query "[?roleDefinitionName=='AcrPull'].roleDefinitionName" -o tsv
    if ($roles -contains "AcrPull") { $acrPullVisible = $true } else {
        Write-Host "    AcrPull not visible yet (attempt $attempt/10), waiting 30s..."
        Start-Sleep -Seconds 30
    }
}
if (-not $acrPullVisible) { throw "AcrPull did not propagate for principal $principalId" }
if ($newIdentity) {
    # data-plane propagation lags the ARM view of a brand-new assignment
    Start-Sleep -Seconds 60
}

Write-Host "==> Configuring registry (managed identity) and secrets"
Invoke-Az containerapp registry set -n $AppName -g $ResourceGroup --server $acrServer --identity system -o none | Out-Null
Invoke-Az containerapp secret set -n $AppName -g $ResourceGroup --secrets "attlassian-api-key=$jiraApiKey" -o none | Out-Null

Write-Host "==> Deploying $image"
$envVars = @($settings.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) + "attlassian_api_key=secretref:attlassian-api-key"
$deployed = $false
for ($attempt = 1; $attempt -le 5 -and -not $deployed; $attempt++) {
    # AcrPull can take a few minutes to propagate on a new identity
    & az containerapp update -n $AppName -g $ResourceGroup --image $image `
        --cpu $Cpu --memory $Memory --min-replicas $MinReplicas --max-replicas $MaxReplicas `
        --set-env-vars @envVars -o none
    if ($LASTEXITCODE -eq 0) { $deployed = $true } else {
        Write-Host "    update failed (attempt $attempt); waiting 30s for role propagation..."
        Start-Sleep -Seconds 30
    }
}
if (-not $deployed) { throw "Failed to deploy $image" }

Invoke-Az containerapp ingress update -n $AppName -g $ResourceGroup --type external --target-port 8000 --transport auto -o none | Out-Null
# Chainlit keeps per-user session state in memory, so pin each browser to one replica
Invoke-Az containerapp ingress sticky-sessions set -n $AppName -g $ResourceGroup --affinity sticky -o none | Out-Null

# Health probes with generous timings: Chainlit takes ~15-30 s to import and start listening on a
# cold container, so tight default probes could restart the replica before it is ready.
Write-Host "==> Configuring health probes"
$appId = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query id -o tsv
$app = (Invoke-Az rest --method get --url "https://management.azure.com${appId}?api-version=$ContainerAppsApiVersion") | ConvertFrom-Json
$app.properties.template.containers[0] | Add-Member -NotePropertyName probes -Force -NotePropertyValue @(
    @{ type = "Startup";   httpGet = @{ path = "/"; port = 8000 }; initialDelaySeconds = 5;  periodSeconds = 5;  timeoutSeconds = 5;  failureThreshold = 36 },
    @{ type = "Readiness"; httpGet = @{ path = "/"; port = 8000 }; initialDelaySeconds = 5;  periodSeconds = 10; timeoutSeconds = 5;  failureThreshold = 6; successThreshold = 1 },
    @{ type = "Liveness";  httpGet = @{ path = "/"; port = 8000 }; initialDelaySeconds = 30; periodSeconds = 30; timeoutSeconds = 10; failureThreshold = 3 }
)
$patchFile = New-TemporaryFile
try {
    @{ properties = @{ template = $app.properties.template } } | ConvertTo-Json -Depth 20 | Set-Content $patchFile -Encoding utf8
    Invoke-Az rest --method patch --url "https://management.azure.com${appId}?api-version=$ContainerAppsApiVersion" `
        --body "@$patchFile" --headers "Content-Type=application/json" -o none | Out-Null
} finally {
    Remove-Item $patchFile -Force
}

Write-Host "==> Waiting for the new revision to become healthy"
for ($i = 0; $i -lt 40; $i++) {
    $state = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query "{p:properties.provisioningState,r:properties.latestRevisionName,ready:properties.latestReadyRevisionName}" -o json | ConvertFrom-Json
    if ($state.p -eq "Succeeded" -and $state.r -eq $state.ready) { break }
    Start-Sleep -Seconds 10
}

$fqdn = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query properties.configuration.ingress.fqdn -o tsv
$revision = Invoke-Az containerapp show -n $AppName -g $ResourceGroup --query properties.latestRevisionName -o tsv
Write-Host ""
Write-Host "Deployed revision $revision"
Write-Host "URL: https://$fqdn"
