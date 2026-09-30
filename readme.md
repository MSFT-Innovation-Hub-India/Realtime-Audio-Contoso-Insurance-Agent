# Contoso Insurance Voice Agent — Azure Voice Live API + GPT-Realtime

A speech-to-speech AI assistant for **Contoso Insurance**, built on the **[Azure Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live)** of a Microsoft Foundry resource and the `gpt-realtime-2.1` model. Customers talk to the assistant, or type, and it answers in natural speech with an Azure HD voice. A live transcript of both sides appears in a [Chainlit](https://docs.chainlit.io) web UI.

The assistant answers policy questions from a knowledge base, looks up the customer's insurance claims, and registers and tracks grievances. It does this through **client-side function calling** against Azure AI Search, Azure SQL Database and Atlassian Jira.

---

## Contents

- [Features](#features)
- [What you can ask](#what-you-can-ask)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Azure setup](#azure-setup)
- [Local setup and run](#local-setup-and-run)
- [Configuration reference](#configuration-reference)
- [Using the app](#using-the-app)
- [How it works](#how-it-works)
- [Handling coughs and brief noises](#handling-coughs-and-brief-noises)
- [Deploying to Azure](#deploying-to-azure)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Changes from the previous version](#changes-from-the-previous-version)
- [References](#references)

---

## Features

- **Speech-to-speech conversation** over the Azure Voice Live API with `gpt-realtime-2.1`. It uses native audio understanding, and responses are spoken by an Azure HD neural voice (`en-IN-Meera:DragonHDV2.3Neural` by default).
- **Voice or text input**: the user can talk (server-side voice activity detection) or type in the chat box, in the same session and with the same conversation context.
- **Barge-in**: talk over the assistant to interrupt it. Playback stops, and the model's memory is truncated to what you actually heard.
- **Robust to coughs and background noise**: noise suppression, echo cancellation, Azure semantic VAD, a client-side interrupt debounce and noise-transcript filtering. Brief sounds don't break the response. See [Handling coughs and brief noises](#handling-coughs-and-brief-noises).
- **Keyless Azure authentication**: Voice Live, Azure AI Search and Azure SQL all use Microsoft Entra ID through `DefaultAzureCredential` (managed identity in Azure, `az login` locally). Only Jira uses an API token.
- **Correctly ordered live transcript**: user and assistant messages appear in conversational order, even though the server emits their transcripts asynchronously.
- **Tools**:

  | Tool | What it does | Backend | Authentication |
  | --- | --- | --- | --- |
  | `perform_search_based_qna` | Answers questions on Contoso insurance products and policies | Azure AI Search, semantic ranking over the Contoso insurance FAQ index | Entra ID |
  | `get_claim_status_summary` | Summarizes the current customer's health insurance claims | Azure SQL Database, table `Health_Insurance_Claims` | Entra ID |
  | `register_user_grievance_def` | Registers a complaint as a Jira issue | Atlassian Jira Cloud | API token |
  | `get_grievance_status_def` | Gets the status, priority and due date of a grievance | Atlassian Jira Cloud | API token |

## What you can ask

- *"What types of health insurance plans does Contoso offer?"* → knowledge base (AI Search)
- *"How do I file a claim, and what documents do I need?"* → knowledge base
- *"What's the status of my claims?"* → Azure SQL
- *"I was charged twice for my premium this month, I want to raise a complaint."* → the assistant confirms the category and description, then creates a Jira issue and reads back the grievance id
- *"What's the status of grievance 10015?"* → Jira

## Architecture

```
┌──────────────────────────────┐
│ Browser — Chainlit UI        │  mic in / speaker out: pcm16 @ 24 kHz
└──────────────┬───────────────┘
               │ Chainlit websocket
┌──────────────▼───────────────┐
│ app.py                       │  system prompt, UI event handlers, audio/text entry points
└──────────────┬───────────────┘
               │ ordered UI event queue
┌──────────────▼───────────────┐      wss://<foundry>.cognitiveservices.azure.com/voice-live/realtime
│ voicelive_client.py          │ ───► ?api-version=2026-04-10&model=gpt-realtime-2.1
│  session config · events     │      Authorization: Bearer <Entra token, scope https://ai.azure.com/.default>
│  function calling · guards   │
└──────────────┬───────────────┘      Voice Live session:
               │                        azure_semantic_vad · azure_deep_noise_suppression
┌──────────────▼───────────────┐        server_echo_cancellation · gpt-4o-transcribe
│ tools.py / functions.py      │        Azure HD voice · tools
└──┬───────────┬───────────┬───┘
   │           │           │
Azure AI    Azure SQL    Jira Cloud
Search      Database     (API token)
(Entra ID)  (Entra ID)
```

A spoken question that needs a tool call:

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant A as app.py (Chainlit)
    participant C as voicelive_client.py
    participant V as Voice Live (gpt-realtime-2.1)
    participant T as Tool (SQL / Search / Jira)
    U->>A: mic audio chunks
    A->>C: append_input_audio
    C->>V: input_audio_buffer.append
    V-->>C: speech_started (debounced 450 ms)
    C-->>A: user.turn.started → "🎤 Listening..."
    V-->>C: speech_stopped, committed
    V-->>C: response.created (created by the server automatically)
    V-->>C: input_audio_transcription.completed
    C-->>A: placeholder replaced with the transcript
    V-->>C: audio + transcript deltas (spoken preamble)
    V-->>C: response.done [message, function_call]
    C->>T: run tool in a worker thread
    T-->>C: result
    C->>V: conversation.item.create (function_call_output) + response.create
    V-->>C: audio + transcript deltas (answer)
    C-->>A: play audio, stream transcript
```

## Project structure

| Path | Purpose |
| --- | --- |
| [app.py](app.py) | Chainlit app: system prompt, UI event handlers (placeholders, transcripts, playback, interrupts), mic/text entry points, session lifecycle |
| [voicelive_client.py](voicelive_client.py) | Voice Live WebSocket client: Entra-authenticated connection, `session.update` config, server event handling, response lifecycle tracking, function calling, interrupt debounce and stale-audio suppression |
| [tools.py](tools.py) | Tool (function) JSON schemas sent to the model, and the name → function dispatch table |
| [functions.py](functions.py) | Tool implementations: Azure AI Search, Azure SQL (Entra access token through pyodbc), Jira |
| [azure_auth.py](azure_auth.py) | Shared `DefaultAzureCredential`, per-scope token cache, `CachedTokenCredential` for Azure SDK clients, and token warm-up |
| [envconfig.py](envconfig.py) | All configuration, read from `.env` or environment variables, with defaults |
| [.chainlit/config.toml](.chainlit/config.toml) | Chainlit settings. `[features.audio] enabled = true` and `sample_rate = 24000` are required. |
| [.env.example](.env.example) | Template for `.env` |
| [MESSAGE_SEQUENCING_IMPROVEMENTS.md](MESSAGE_SEQUENCING_IMPROVEMENTS.md) | Detailed notes on how transcript ordering is kept correct |
| [requirements.txt](requirements.txt) | Python dependencies |

## Prerequisites

**Local tools**
- Python 3.10 or later (tested with 3.13)
- [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), for `az login` during local development
- A browser with microphone access. Browsers only allow the microphone on `https://` or `http://localhost`.

**Azure resources**
- A **Microsoft Foundry** resource (kind `AIServices`) in a [region that supports Voice Live](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live#supported-models-and-regions). `gpt-realtime-2.1` is one of the Voice Live managed models, so you don't need your own model deployment for it.
- **Azure AI Search** with an index over the Contoso insurance FAQ documents and a semantic configuration. Role-based access must be enabled (see [Azure setup](#azure-setup)).
- **Azure SQL Database** with Microsoft Entra authentication and the `Health_Insurance_Claims` table.

**Atlassian**
- A Jira Cloud site, a project for grievances, and an [API token](https://id.atlassian.com/manage-profile/security/api-tokens) for the account the app uses.

## Azure setup

### 1. Choose the identity

| Environment | Identity used by `DefaultAzureCredential` |
| --- | --- |
| Local development | Your `az login` (or VS Code Azure) account |
| Azure App Service / Container Apps / VM / AKS | The resource's **managed identity**. For a user-assigned identity, also set `AZURE_CLIENT_ID`. |

### 2. Assign roles to that identity

| Resource | Role / permission |
| --- | --- |
| Foundry resource | `Cognitive Services User` **and** `Foundry User`. `Foundry User` was previously named *Azure AI User*, and you may still see the old name. |
| Azure AI Search service | `Search Index Data Reader` |
| Azure SQL Database | A contained database user with `db_datareader` (step 4) |

```powershell
$principalId = "<object-id-of-user-or-managed-identity>"
az role assignment create --assignee $principalId --role "Cognitive Services User" --scope <foundry-resource-id>
az role assignment create --assignee $principalId --role "Foundry User"            --scope <foundry-resource-id>
az role assignment create --assignee $principalId --role "Search Index Data Reader" --scope <search-service-resource-id>
```

Role assignments can take a few minutes to take effect.

### 3. Enable role-based access on Azure AI Search

```powershell
az search service update --name <search-service> --resource-group <rg> `
  --auth-options aadOrApiKey --aad-auth-failure-mode http401WithBearerChallenge
```

### 4. Create the database user for the identity

Connect to the database as the server's **Microsoft Entra admin**, then run:

```sql
CREATE USER [<managed-identity-name or user@domain>] FROM EXTERNAL PROVIDER;
ALTER ROLE db_datareader ADD MEMBER [<managed-identity-name or user@domain>];
```

The server's firewall must allow the client: your IP for local runs, and *Allow Azure services* or a private endpoint or VNet for the hosted app.

### 5. Data the tools expect

**Azure SQL table `Health_Insurance_Claims`**

| Column | Type | Nullable |
| --- | --- | --- |
| `Claim_ID` | nvarchar(50) | no |
| `Patient_ID` | nvarchar(50) | no |
| `Provider_ID` | nvarchar(50) | no |
| `Date_of_Service` | date | no |
| `Date_Submitted` | date | no |
| `Service_Type` | nvarchar(50) | no |
| `Claim_Amount` | smallint | no |
| `Paid_Amount` | smallint | no |
| `Claim_Status` | nvarchar(50) | no |
| `Reason_for_Denial` | nvarchar(100) | yes |
| `Patient_Name` | nvarchar(50) | no |

`get_claim_status_summary` returns `Claim_ID`, `Service_Type`, `Claim_Amount`, `Claim_Status`, `Reason_for_Denial` and `Patient_Name` for rows `WHERE Patient_Name = <customer_name>`.

**Azure AI Search index**: the tool queries with `query_type="semantic"` and the configured semantic configuration, and reads the top 2 documents' `content` field. An index built by the Blob Storage indexer (fields `content`, `metadata_storage_name`, …) over the Contoso FAQ documents works as-is.

**Jira project**: grievances are created in the project `grievance_project_key` with issue type `grievance_type` (for example `Task`, which must exist in the project). The grievance id given to the customer is the numeric Jira issue **id** (for example `10015`), not the key (for example `DSS-12`).

## Local setup and run

```powershell
git clone <this-repo>
cd Realtime-Audio-Contoso-Insurance-Agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

az login                              # the identity from "Azure setup"
Copy-Item .env.example .env           # then edit .env
chainlit run app.py
```

Open http://localhost:8000. Use `chainlit run app.py -w` to reload automatically on code changes.

## Configuration reference

All settings are read from `.env` (loaded by [envconfig.py](envconfig.py)) or from environment variables, such as App Service app settings.

**Azure Voice Live**

| Variable | Default | Description |
| --- | --- | --- |
| `AZURE_VOICE_LIVE_ENDPOINT` | *(required)* | Foundry resource endpoint: `https://<resource>.cognitiveservices.azure.com` or `https://<resource>.services.ai.azure.com`. The client builds the `wss://…/voice-live/realtime` URL from it. |
| `AZURE_VOICE_LIVE_API_VERSION` | `2026-04-10` | Voice Live API version |
| `VOICE_LIVE_MODEL` | `gpt-realtime-2.1` | Model name, for example `gpt-realtime-2.1-mini` for lower cost |
| `AZURE_TTS_VOICE_NAME` | `en-IN-Meera:DragonHDV2.3Neural` | Azure neural or HD voice for responses |
| `AZURE_TTS_VOICE_TEMPERATURE` | `0.8` | Variation in HD voice delivery |
| `INPUT_TRANSCRIPTION_MODEL` | `gpt-4o-transcribe` | Transcription of the user's audio: `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`, `mai-transcribe` |

**Turn detection and noise robustness** (see [Handling coughs and brief noises](#handling-coughs-and-brief-noises))

| Variable | Default | Description |
| --- | --- | --- |
| `VAD_TYPE` | `azure_semantic_vad` | `azure_semantic_vad`, `azure_semantic_vad_multilingual` or `server_vad` |
| `VAD_THRESHOLD` | `0.6` | Speech confidence needed to start a turn (0.01–1.0; the service default is 0.5) |
| `VAD_PREFIX_PADDING_MS` | `300` | Audio kept before the detected start of speech |
| `VAD_SPEECH_DURATION_MS` | `300` | Minimum speech duration before a user turn starts (semantic VAD only) |
| `VAD_SILENCE_DURATION_MS` | `700` | Silence that ends the user's turn |
| `VAD_REMOVE_FILLER_WORDS` | `true` | Ignore "uh", "hmm", "yeah" and similar while the assistant is speaking (semantic VAD only) |
| `INTERRUPT_DEBOUNCE_MS` | `450` | How long speech must continue before playback is stopped on the client |

**Tools and identity**

| Variable | Default | Description |
| --- | --- | --- |
| `ai_search_url` | *(required)* | `https://<search>.search.windows.net` |
| `ai_index_name` | *(required)* | Index name, for example `contoso-insurance-faq-index` |
| `ai_semantic_config` | *(required)* | Semantic configuration name, for example `contoso-insurance-faq-config` |
| `az_db_server` | *(required)* | `<server>.database.windows.net` |
| `az_db_database` | *(required)* | Database name |
| `az_db_driver` | `ODBC Driver 18 for SQL Server` | ODBC driver name |
| `attlassian_url` | *(required)* | `https://<site>.atlassian.net/` |
| `attlassian_user_name` | *(required)* | Atlassian account email |
| `attlassian_api_key` | *(required)* | Atlassian API token (secret) |
| `grievance_project_key` | *(required)* | Jira project key for grievances |
| `grievance_type` | `Task` | Jira issue type for new grievances |
| `grievance_project_name` | — | Informational only; the code uses the project key |
| `customer_name` | `Srikantan` | The customer the assistant is talking to. Claim lookups are restricted to this patient, and the name is included in the system prompt. |
| `AZURE_CLIENT_ID` | — | Client id of a **user-assigned** managed identity, if you use one |

## Using the app

- **Talk**: click the microphone icon (or press `P`), then speak. A `🎤 Listening...` bubble appears, changes to `🔄 Processing...`, and is then replaced by your transcript. The assistant's reply plays and streams as text.
- **Interrupt**: speak over the assistant for about half a second, or type a message. Playback stops and the assistant responds to the new input.
- **Type**: typed questions get a streamed transcript reply. They are also **spoken** if the microphone is on. Chainlit only sets up browser audio playback after the microphone has been started.
- **Session lifetime**: the Voice Live session opens on the first mic start or typed message. It stays open while you toggle the mic, so context is kept, and it closes when the chat ends (**New Chat** or closing the tab). If the service closes the session, for example after a long idle time, the app shows a notice and opens a new session on your next input. The previous conversation context isn't carried over.
- **Stop**: the Chainlit stop button cancels the response being generated.

## How it works

### Voice Live session configuration

After connecting, [voicelive_client.py](voicelive_client.py) sends a single `session.update` (values shown are the defaults):

```json
{
  "instructions": "<system prompt from app.py>",
  "modalities": ["text", "audio"],
  "input_audio_sampling_rate": 24000,
  "input_audio_format": "pcm16",
  "output_audio_format": "pcm16",
  "turn_detection": {
    "type": "azure_semantic_vad",
    "threshold": 0.6,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 700,
    "speech_duration_ms": 300,
    "remove_filler_words": true,
    "interrupt_response": true,
    "auto_truncate": true
  },
  "input_audio_noise_reduction": { "type": "azure_deep_noise_suppression" },
  "input_audio_echo_cancellation": { "type": "server_echo_cancellation" },
  "input_audio_transcription": { "model": "gpt-4o-transcribe" },
  "voice": { "name": "en-IN-Meera:DragonHDV2.3Neural", "type": "azure-standard", "temperature": 0.8 },
  "tools": ["<see tools.py>"],
  "tool_choice": "auto"
}
```

### Authentication and token caching

- [azure_auth.py](azure_auth.py) creates one `DefaultAzureCredential` and caches an access token per scope until 5 minutes before it expires:
  - Voice Live: `https://ai.azure.com/.default`, sent as a `Bearer` header on the WebSocket handshake
  - Azure SQL: `https://database.windows.net/.default`, passed to pyodbc through `SQL_COPT_SS_ACCESS_TOKEN` (attribute 1256), with no password in the connection string
  - Azure AI Search: `https://search.azure.com/.default`, supplied to `SearchClient` through `CachedTokenCredential`
- Tokens are fetched ahead of time when a chat starts, so the first tool call doesn't wait for credential discovery.

### Function calling

- `gpt-realtime-2.1` often speaks a short preamble ("Let me check that for you…") **in the same response** as the function call. The function call is therefore not necessarily `response.output[0]`, so the client processes **every** `function_call` item in `response.done`.
- Tool functions do blocking I/O, so they run in a worker thread (`asyncio.to_thread`) and audio streaming isn't blocked.
- Each result is sent back as a `function_call_output` item, followed by one `response.create`.
- Tool errors are returned to the model as text, so it can tell the user rather than failing silently.

### Response lifecycle

- When VAD commits a user turn, the **server creates the response automatically** (`turn_detection.create_response` defaults to `true`). The client only sends `response.create` for typed messages and after function results.
- The client tracks each response through three states: *requested* (`response.create` sent, or audio committed), *active* (`response.created`), and *done* (`response.done`).
  - If a tool result is ready while another response is requested or active, for example because the user spoke during the tool call, the follow-up `response.create` is **deferred** until that response finishes. This way, a result never goes unanswered and there is no `conversation_already_has_active_response` error.
  - A watchdog resets the *requested* state if the service never acknowledges a request, so the session can't get stuck.
- Typed messages first cancel any in-flight response (`response.cancel`), wait for it to end, and then send the new message.

### Message sequencing

The server emits the user's transcript and the assistant's transcript asynchronously. To keep the chat in order:
- UI events go through a **single ordered queue**, so message sends, updates and audio chunks are applied in arrival order.
- A user placeholder is created as soon as a real user turn is detected, before the response begins. It's keyed by the server's user `item_id` and later replaced with the transcript.
- Assistant transcripts stream into a message keyed by the assistant `item_id`.
- After an interruption, remaining audio chunks from the interrupted response are dropped instead of playing late.

Details are in [MESSAGE_SEQUENCING_IMPROVEMENTS.md](MESSAGE_SEQUENCING_IMPROVEMENTS.md).

## Handling coughs and brief noises

In the earlier version, a cough or other short noise could be taken as user input. It would stop the assistant's audio mid-sentence or trigger a spurious new response. The app now protects against this in layers:

1. **Server-side noise suppression and echo cancellation**: `azure_deep_noise_suppression` removes non-speech background sound before VAD sees it. `server_echo_cancellation` stops the assistant's own voice (from speakers) from counting as user speech.
2. **Azure semantic VAD** (`azure_semantic_vad`) with:
   - `speech_duration_ms=300`: speech must last this long before a user turn starts, so short bursts are ignored.
   - `threshold=0.6`: needs a higher-confidence speech signal than the default of 0.5.
   - `remove_filler_words=true`: "uh", "hmm", "yeah" and similar don't barge in on an ongoing response.
   - `interrupt_response=true` and `auto_truncate=true`: real barge-in still works, and the model's memory is truncated to what the user heard.
3. **Client-side interrupt debounce** (ported from the [VoiceLiveAPI-Retail-Agent-Service-Demo](https://github.com/MSFT-Innovation-Hub-India/VoiceLiveAPI-Retail-Agent-Service-Demo) repo): on `input_audio_buffer.speech_started`, playback is **not** stopped immediately. It's stopped only if speech continues past `INTERRUPT_DEBOUNCE_MS` (450 ms), or if the server commits the audio as a real user turn (`input_audio_buffer.committed`). If `speech_stopped` arrives first, the pending interrupt is cancelled and nothing appears in the chat.
4. **Noise transcripts are dropped from the chat**: if a committed turn transcribes to nothing, or to a non-speech annotation such as `(coughs)` or `[noise]`, the user placeholder is removed. The system prompt also tells the model to ask the user to repeat instead of guessing.

**Tuning guide**

| Symptom | Adjust |
| --- | --- |
| Coughs or clicks still interrupt the assistant | Increase `VAD_SPEECH_DURATION_MS` (for example 400–500), `VAD_THRESHOLD` (for example 0.7) or `INTERRUPT_DEBOUNCE_MS` (for example 600) |
| Short answers like "yes" or "no" are missed | Decrease `VAD_SPEECH_DURATION_MS` (for example 200) or `VAD_THRESHOLD` (for example 0.5) |
| The assistant replies before the user has finished speaking | Increase `VAD_SILENCE_DURATION_MS` (for example 900–1200) |
| Replies feel slow to start | Decrease `VAD_SILENCE_DURATION_MS` (for example 500) |
| Barge-in feels sluggish | Decrease `INTERRUPT_DEBOUNCE_MS` (for example 300) |
| Speakers without headphones cause self-interruption | Keep `server_echo_cancellation` on, and use a headset or lower the speaker volume |
| Non-English or mixed-language users | `VAD_TYPE=azure_semantic_vad_multilingual`, and an appropriate `AZURE_TTS_VOICE_NAME` |

## Deploying to Azure

The app is a standard Python web app, so it runs on **Azure App Service (Linux)**, **Azure Container Apps**, or any host with a managed identity. For App Service:

1. Create a Linux Python web app and enable a **system-assigned managed identity**, or attach a user-assigned identity and set `AZURE_CLIENT_ID`.
2. Give that identity the roles and database user from [Azure setup](#azure-setup).
3. **Enable WebSockets**, which both Chainlit and the audio stream need:
   `az webapp config set -g <rg> -n <app> --web-sockets-enabled true`
4. Add every `.env` setting as an **app setting**. Keep `attlassian_api_key` in **Azure Key Vault** and reference it as `@Microsoft.KeyVault(SecretUri=...)`. Don't deploy the `.env` file.
5. Set the startup command:
   `python -m chainlit run app.py --host 0.0.0.0 --port 8000 --headless`
   Then set `WEBSITES_PORT=8000`, or use the port App Service provides.
6. Make sure the SQL server firewall or network allows the app (for example *Allow Azure services*, or a private endpoint with VNet integration).
7. The ODBC driver must be present at runtime. For custom containers (Container Apps / AKS), install `msodbcsql18` in the image.
8. Browse to the site over **HTTPS**, since the microphone requires a secure context.

The app keeps per-user session state in memory (Chainlit sessions and Voice Live WebSockets). When scaling out, enable **session affinity** (ARR affinity on App Service).

## Security notes

- **No Azure secrets**: Voice Live, Search and SQL use Entra ID tokens. The only secret is the Jira API token, which should be stored in Key Vault when hosted.
- `.env` is git-ignored. Never commit it.
- **Least privilege**: the app only needs read access to Search and SQL (`Search Index Data Reader`, `db_datareader`). The Jira account only needs permission to create and browse issues in the grievance project.
- **Patient data isolation**: `get_claim_status_summary` has no arguments. The patient is always the configured `customer_name`, never a name chosen by the model, so a user can't talk the assistant into disclosing another patient's claims. In a multi-user deployment, replace `customer_name` with the authenticated Chainlit user (see [Chainlit authentication](https://docs.chainlit.io/authentication/overview)).
- **Input validation**: the grievance id is validated as an integer before it's used in JQL. SQL queries are parameterized.
- **Authentication**: the Chainlit UI has no sign-in by default. Add authentication, such as Entra ID OAuth through Chainlit, or App Service Easy Auth, before exposing it beyond a demo.
- `allow_origins = ["*"]` in `.chainlit/config.toml` is fine locally. Restrict it to your site's origin in production.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `Failed to connect to Voice Live API` with **401/403** | The identity lacks `Cognitive Services User` / `Foundry User` on the Foundry resource, or role assignment is still propagating (wait a few minutes). Check `az account show` locally. |
| `Failed to connect …` with **404** or a DNS error | Wrong `AZURE_VOICE_LIVE_ENDPOINT`. Use the resource endpoint (`https://<resource>.cognitiveservices.azure.com`), not the `openai.azure.com` or `/openai/v1/realtime` URL. |
| Voice Live error about an unsupported model or region | The resource's region doesn't support Voice Live or that model. Check the [supported regions](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live#supported-models-and-regions). |
| Knowledge-base answers say *"We had an issue searching…"* and the logs show 403 | Missing `Search Index Data Reader`, or role-based access isn't enabled on the search service ([step 3](#3-enable-role-based-access-on-azure-ai-search)). |
| Claims answer *"We had an issue retrieving your claim status"* and the logs show `Login failed for user '<token-identified principal>'` | No database user for the identity ([step 4](#4-create-the-database-user-for-the-identity)). |
| `Data source name not found and no default driver specified` | ODBC Driver 18 isn't installed, or `az_db_driver` has the wrong name. |
| SQL timeout or connection refused | The SQL firewall blocks your IP, or a serverless database is resuming. Retry after a few seconds. |
| Jira errors / 401 | The API token expired or was revoked, the wrong `attlassian_user_name` (it must be the token owner's email), or no permission on the project. |
| Jira *"issue type … not valid"* | `grievance_type` doesn't exist in the project. Use one of the project's issue types. |
| The first connection takes several seconds locally | `DefaultAzureCredential` probes for a managed identity before falling back to the Azure CLI. This only happens once per process. To skip the probe locally, set `AZURE_TOKEN_CREDENTIALS=dev` in your shell (not in hosted app settings). |
| The microphone button does nothing | The page isn't on `https://` or `localhost`, or the browser denied mic permission. |
| Typed questions produce text but no audio | This is expected while the mic is off. Start the microphone to hear replies. |
| No audio at all, or a `features.audio` error | Check `.chainlit/config.toml` has `[features.audio] enabled = true` and `sample_rate = 24000`. |
| A notice says *"The voice session was closed by the service"* | The service closed an idle or long session. The next mic input or message reconnects automatically, without the previous context. |
| Verbose logs | Azure SDK HTTP logging is reduced to warnings in `app.py`. Change `logging.getLogger("azure").setLevel(...)` to see more. |

## Known limitations

- Spoken replies to typed messages need the microphone to be on, because Chainlit only sets up browser audio playback after the mic starts.
- If the Voice Live session is closed by the service, the app reconnects with a fresh session and earlier context is lost.
- The demo serves a single configured customer (`customer_name`). Multi-user use needs Chainlit authentication, with the signed-in user mapped to the patient.
- Knowledge-base answers use the top 2 search results only. Increase `top` in `perform_search_based_qna` for broader grounding, at the cost of more tokens.
- The cough and noise defaults were tuned with a typical laptop microphone. Different rooms and microphones may need the [tuning](#handling-coughs-and-brief-noises) above.

## Changes from the previous version

The original sample used the Azure OpenAI Realtime API (`gpt-4o-realtime-preview`) with API keys. This version:

- **Moves to the Azure Voice Live API** with `gpt-realtime-2.1` (API version `2026-04-10`), an Azure HD voice, Azure semantic VAD, deep noise suppression, server echo cancellation and `gpt-4o-transcribe`. `realtime_client.py` and `utils.py` are replaced by `voicelive_client.py`.
- **Goes keyless on Azure**: Voice Live, Azure AI Search and Azure SQL use Microsoft Entra ID / managed identity instead of the OpenAI key, Search key and SQL username/password. Tokens are cached and fetched ahead of time.
- **Fixes function calling for gpt-realtime-2.x**: every `function_call` item in a response is handled, not just `output[0]`. Tools run off the event loop.
- **Removes the duplicate `response.create`** that used to be sent on `input_audio_buffer.committed`, which could cause double responses. The server creates it automatically. Adds full response lifecycle tracking, with deferred follow-ups and a watchdog.
- **Adds cough and noise robustness**: layered server and client guards, including the interrupt debounce from the VoiceLiveAPI-Retail-Agent-Service-Demo repo.
- **Improves transcript ordering**: an ordered UI event queue, placeholders keyed by the server `item_id`, noise placeholders removed, and stale audio suppressed after barge-in.
- **Keeps the session open across mic toggles**, supports text-only turns without starting the mic, and reconnects after a server-side close.
- **Jira fixes**: JQL uses the quoted project key (the old unquoted project-name query broke on names with spaces). Descriptions in Atlassian Document Format (from the v3 enhanced JQL search) are converted to text. Grievance ids are validated. A connection error no longer calls `exit(1)`, which used to stop the server.
- **Security**: the claims lookup is restricted to the configured customer.
- **Upgraded to Chainlit 2.x**: regenerated `.chainlit/config.toml` with audio enabled, and updated dependencies (numpy is no longer needed).

## References

- [Voice Live API overview (models and regions)](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live)
- [How to use the Voice Live API (session properties, turn detection, noise suppression, voices)](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live-how-to)
- [Realtime API events reference (used by Voice Live)](https://learn.microsoft.com/azure/ai-foundry/openai/realtime-audio-reference)
- [DefaultAzureCredential](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential)
- [Connect to Azure SQL with Microsoft Entra authentication (Python)](https://learn.microsoft.com/azure/azure-sql/database/azure-sql-passwordless-migration-python)
- [Azure AI Search role-based access](https://learn.microsoft.com/azure/search/search-security-enable-roles)
- [Chainlit documentation](https://docs.chainlit.io)
- [VoiceLiveAPI-Retail-Agent-Service-Demo (reference implementation)](https://github.com/MSFT-Innovation-Hub-India/VoiceLiveAPI-Retail-Agent-Service-Demo)
