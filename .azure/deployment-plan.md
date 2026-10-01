# Azure Deployment Plan

> **Status:** Deployed

Generated: 2026-10-01T08:40+05:30

---

## 1. Project Overview

**Goal:** Containerize the Contoso Insurance voice agent (Chainlit + Azure Voice Live API), build the image with `az acr build`, and deploy it as an Azure Container App into the existing Container Apps environment `pcdotai-agent` (South India). The app uses a system-assigned managed identity with RBAC to Azure AI Search, Azure SQL Database and the Foundry (Voice Live) resource. Also add a UI side panel showing the solution configuration (with sensitive values redacted) and a "Powered by Azure Voice Live in Microsoft Foundry" display.

**Path:** Add Components (containerize and deploy an existing app into existing infrastructure)

---

## 2. Requirements

| Attribute | Value |
|-----------|-------|
| Classification | POC / Demo |
| Scale | Small (single demo user population) |
| Budget | Cost-Optimized (Consumption workload profile) |
| **Subscription** | MCAPS-Hybrid-REQ-55300-2023-sansri (bc2e2415-164d-45a5-9a4a-29d9264a343e). Taken as given: the user named the existing environment, which lives in this subscription. |
| **Location** | South India (`southindia`), which is the existing environment's region and was specified by the user |

---

## 3. Components Detected

| Component | Type | Technology | Path |
|-----------|------|------------|------|
| contoso-insurance-voice-agent | Web app (UI + realtime voice backend) | Python 3.12, Chainlit 2.12, websockets, azure-identity, pyodbc (ODBC Driver 18) | `/` (app.py) |

External dependencies (existing, not deployed by this plan):
- Foundry resource `ai-foundry-common-ihub` (rg-foundry-common, swedencentral): Voice Live API, `gpt-realtime-2.1`
- Azure AI Search `ai-search-vector-001` (ai-search-common-rg): index `contoso-insurance-faq-index`
- Azure SQL `az-sqldb-common` / `cdcsampledb` (az-sqldb-common-rg): Entra-only auth, firewall `AllowAllWindowsAzureIps` already present
- Jira Cloud `ssrikantan.atlassian.net`: API token

---

## 4. Recipe Selection

**Selected:** AZCLI

**Rationale:** The user explicitly asked for `az acr` image commands and deployment into an existing environment. All infrastructure (environment, Log Analytics, ACR) already exists, so only one Container App plus role assignments are added. An idempotent PowerShell script (`scripts/deploy.ps1`) running Azure CLI commands is the simplest way to do that.

---

## 5. Architecture

**Stack:** Containers (Azure Container Apps, Consumption)

### Service Mapping

| Component | Azure Service | SKU / Config |
|-----------|---------------|-----|
| contoso-insurance-voice-agent | Azure Container App in existing env `pcdotai-agent` (rg `pcdotai-agent`) | Consumption; 1 vCPU / 2 GiB; min 1 / max 3 replicas; external ingress on port 8000, transport auto (WebSockets); sticky sessions |
| Container image | Existing ACR `pcdotaiagentd10b5a` (Basic, South India) | Built with `az acr build`, repo `contoso-insurance-voice-agent` |

### Supporting Services

| Service | Purpose |
|---------|---------|
| Log Analytics (existing, attached to env) | Container console/system logs |
| System-assigned Managed Identity | ACR pull (AcrPull), Voice Live, AI Search, Azure SQL |
| Container App secret | Jira API token (`attlassian-api-key`), referenced with `secretref:` and not baked into the image |

### Identity & RBAC (system-assigned managed identity of the container app)

| Target | Role / permission | Why |
|--------|-------------------|-----|
| ACR `pcdotaiagentd10b5a` | AcrPull | Pull image without admin credentials |
| AI Search `ai-search-vector-001` | Search Index Data Reader | `perform_search_based_qna` (requested) |
| Azure SQL DB `cdcsampledb` | Contained DB user `FROM EXTERNAL PROVIDER` + `db_datareader` | `get_claim_status_summary` (requested) |
| Foundry `ai-foundry-common-ihub` | Cognitive Services User + Foundry User | Required for the Voice Live WebSocket. Without it the app cannot connect, so it is added on top of the requested roles. |

### Application changes

- Side panel (Chainlit `ElementSidebar`) showing the configuration: Voice Live endpoint, API version, model, transcription model, TTS voice, turn-detection settings, AI Search, SQL and Jira details. The Atlassian API token is fully masked and the Atlassian user name is partially masked.
- A "Powered by Azure Voice Live in Microsoft Foundry" header badge, welcome message text and sidebar heading.
- `Dockerfile` (python:3.12-slim-bookworm + msodbcsql18) and `.dockerignore` (excludes `.env`, `.venv`, `.git`, `.azure`)
- `scripts/deploy.ps1`: idempotent build + deploy + RBAC script

---

## 6. Provisioning Limit Checklist

### Phase 1: Prepare Resource Inventory

| Resource Type | Number to Deploy | Total After Deployment | Limit/Quota | Notes |
|---------------|------------------|------------------------|-------------|-------|
| Microsoft.App/managedEnvironments | 0 (reuse existing) | 1 | 50 | Fetched from: azure-quotas (`az quota list/usage`, ManagedEnvironmentCount, southindia) |
| Microsoft.App/containerApps (in env `pcdotai-agent`) | 1 | 3 | 100 apps per environment | Current count from `az containerapp list` (2 apps); limit from official Container Apps quota docs |
| Microsoft.ContainerRegistry/registries | 0 (reuse existing) | 1 | n/a | Existing Basic ACR, 1 new repository only |
| Microsoft.Authorization/roleAssignments | 5 | +5 | 4000 per subscription | Well within limits (official docs) |

### Phase 2: Fetch Quotas and Validate Capacity

**Status:** ✅ All resources within limits

---

## 7. Execution Checklist

### Phase 1: Planning
- [x] Analyze workspace
- [x] Gather requirements
- [x] Confirm subscription and location (from the user's named environment)
- [x] Prepare resource inventory
- [x] Fetch quotas and validate capacity
- [x] Scan codebase
- [x] Select recipe
- [x] Plan architecture
- [x] **User approved this plan** (explicit request for this deployment; autopilot)

### Phase 2: Execution
- [x] Implement UI side panel + "Powered by" display
- [x] Generate Dockerfile + .dockerignore
- [x] Generate scripts/deploy.ps1 (+ scripts/grant_sql_access.py)
- [x] Functional verification locally (side panel, redaction, header badge, reopen action; 0 console errors)
- [x] **⛔ Update plan status to "Ready for Validation"**

### Phase 3: Validation
- [x] Invoke azure-validate skill
- [x] All validation checks pass
  - [x] 1. Core Validation (CLI installed + authenticated; target resources exist; deployer has rights for role assignments + SQL Entra admin). No Bicep: imperative AZCLI deploy into existing infra, so the Bicep build/what-if is N/A.
  - [x] 2. Docker Build (via `az acr build`, including a build-time ODBC Driver 18 check)
  - [x] 3. Azure Policy Validation
- [x] Update plan status to "Validated"
- [x] Record validation proof below

### Phase 4: Deployment
- [x] Invoke azure-deploy skill
- [x] Deployment successful (revision contoso-insurance-voice-agent--0000002, Healthy)
- [x] Report deployed endpoint URLs: https://contoso-insurance-voice-agent.whitemoss-1ee70859.southindia.azurecontainerapps.io
- [x] Live role verification: AcrPull, Cognitive Services User, Foundry User, Search Index Data Reader (resource scopes) + SQL db_datareader (EXTERNAL_USER)
- [x] End-to-end: Voice Live + SQL + AI Search + Jira tool calls verified on the deployed app
- [x] Update plan status to "Deployed"

---

## Role Assignment Verification
- Status: Verified (static review of scripts/deploy.ps1)
- Identities checked: system-assigned managed identity of Container App `contoso-insurance-voice-agent`
- Roles confirmed: AcrPull (ACR scope); Cognitive Services User + Foundry User (Foundry account scope); Search Index Data Reader (search service scope); Azure SQL contained user + db_datareader (database)
- Issues: none. All are data-plane, read-only and resource-scoped, and match the code's operations (image pull, Voice Live session, search query, SELECT).

---

## 8. Validation Proof

| Check | Command Run | Result | Timestamp |
|-------|-------------|--------|-----------|
| Azure CLI installed | `az version` (2.80.0), `az extension show -n containerapp` (1.3.0b4) | ✅ Pass | 2026-10-01T08:52+05:30 |
| Authenticated | `az account show` → MCAPS-Hybrid-REQ-55300-2023-sansri | ✅ Pass | 2026-10-01T08:52+05:30 |
| Target env exists | `az containerapp env show -n pcdotai-agent -g pcdotai-agent` → Succeeded (South India, Log Analytics attached) | ✅ Pass | 2026-10-01T08:52+05:30 |
| ACR / Foundry / Search exist | `az acr show`, `az cognitiveservices account show`, `az search service show` → Succeeded / running, Search `aadOrApiKey` | ✅ Pass | 2026-10-01T08:52+05:30 |
| SQL reachable from Azure, Entra-only | `az sql server show` (azureADOnlyAuthentication=true), firewall `AllowAllWindowsAzureIps` | ✅ Pass | 2026-10-01T08:52+05:30 |
| Deployer can grant RBAC + SQL user | `az role assignment list` → Owner + User Access Administrator on all 3 RGs; deployer is SQL Entra admin | ✅ Pass | 2026-10-01T08:52+05:30 |
| Bicep build / what-if | N/A: imperative AZCLI deploy into existing infra, no IaC templates | ➖ N/A | — |
| Docker build (ACR Tasks) | `az acr build -r pcdotaiagentd10b5a --image contoso-insurance-voice-agent:validate` → run `ct2s` Succeeded; msodbcsql18 18.7.1.1 installed; build-time `pyodbc.drivers()` assertion passed | ✅ Pass | 2026-10-01T08:58+05:30 |
| Azure Policy | `az policy assignment list` (3 Defender/ASC assignments, no deny on Container Apps or role assignments); `az policy state list -g pcdotai-agent` → only audit findings on the existing ACR | ✅ Pass | 2026-10-01T09:00+05:30 |
| Build verification | `py_compile` all modules, `import app`, PowerShell parser on deploy.ps1 → 0 errors | ✅ Pass | 2026-10-01T09:01+05:30 |
| Static role verification | Review of scripts/deploy.ps1 role assignments vs code operations | ✅ Pass | 2026-10-01T09:02+05:30 |
| SQL timeout / retry | Local `get_claim_status_summary()` with 60 s login timeout + transient retry → 2 rows (8.2 s cold) | ✅ Pass | 2026-10-01T08:51+05:30 |

**Validated by:** azure-validate skill
**Validation timestamp:** 2026-10-01T09:03+05:30

---

## 9. Files to Generate

| File | Purpose | Status |
|------|---------|--------|
| `.azure/deployment-plan.md` | This plan | ✅ |
| `Dockerfile` | Container build | ⏳ |
| `.dockerignore` | Keep secrets/venv out of the build context | ⏳ |
| `scripts/deploy.ps1` | ACR build + Container App deploy + RBAC | ⏳ |
| `app.py`, `ui_config.py`, `.chainlit/config.toml`, `public/` | Side panel + Powered-by display | ⏳ |

---

## 10. Next Steps

> Current: Phase 2 (Execution)

1. Implement the UI changes and container artifacts
2. Validate (azure-validate), then deploy (azure-deploy)
