# Copilot instructions: Contoso Insurance Voice Agent

Speech-to-speech insurance assistant: **Chainlit 2.x UI → `voicelive_client.py` → Azure Voice Live API (`gpt-realtime-2.1`)**, with client-side function calling to Azure AI Search, Azure SQL and Jira. Deployed to **Azure Container Apps** (`contoso-insurance-voice-agent` in env `pcdotai-agent`, rg `pcdotai-agent`, South India). [readme.md](../readme.md) is the full reference, and [.azure/deployment-plan.md](../.azure/deployment-plan.md) records the deployment decisions and validation proof.

## Workflow

- Setup: `python -m venv .venv`, `pip install -r requirements.txt -r requirements-dev.txt`, `az login`, then create `.env` with `pwsh ./scripts/pull-env.ps1` (pulls it from the deployed app) or by copying `.env.example`.
- Run locally: `chainlit run app.py` (port 8000).
- Tests:
  - offline unit tests: `python -m pytest`
  - live, needs `az login` + `.env`: `python tests/live/e2e_voicelive.py`, `python tests/live/hindi_gender_check.py`, `python tests/live/ui_fake_mic_test.py --url <url>`
- Deploy: `pwsh ./scripts/deploy.ps1`. It's idempotent: it builds with `az acr build`, applies RBAC, secrets and probes, then rolls out. Add `-SkipBuild -ImageTag <tag>` to redeploy an existing image.
- After changing the app, run `python -m pytest`. If you touched `voicelive_client.py` or the prompt, also run the live e2e (and the Hindi check).

## Conventions and constraints

- **Auth**: every Azure call goes through `azure_auth.py` (one `DefaultAzureCredential`, token cache per scope). Never add API keys for Azure services. Jira is the only secret (`attlassian_api_key`); in Azure it's a Container App secret (`secretref:`).
- **Config**: all settings live in `envconfig.py` (`DefaultConfig`). When adding a setting, update `.env.example`, the readme configuration table and, if it's user-visible, `ui_config.py`. `scripts/deploy.ps1` pushes every `.env` key to the Container App automatically.
- **Config side panel** (`ui_config.py`): show Azure resources in full; never show secrets or personal identifiers (use `redact_secret` / `redact_email`). `tests/test_unit.py` asserts this.
- **Tools**: schemas in `tools.py`, implementations in `functions.py`. They run in a worker thread (blocking I/O is fine) and must return a string. Catch errors and return a user-safe message. `get_claim_status_summary` deliberately takes no arguments; the patient is always `DefaultConfig.customer_name`.
- **Voice Live event handling**:
  - `gpt-realtime-2.x` speaks a preamble *and* calls tools in the same response, so iterate over all `function_call` items in `response.done`.
  - The server auto-creates responses after VAD commits a turn. Only send `response.create` via `_request_response()`, which defers if a response is active or requested.
  - UI events go through the ordered dispatch queue (`dispatch`). Don't call Chainlit from the receive loop directly.
- **Cough and noise robustness** comes in layers: Azure noise suppression and echo cancellation, `azure_semantic_vad` (`speech_duration_ms`, `remove_filler_words`), a client interrupt debounce (`INTERRUPT_DEBOUNCE_MS`), and noise-transcript filtering. Tune with the `VAD_*` env vars rather than code.
- **Persona gender**: `ASSISTANT_GENDER` (default `female`, matching the Meera voice) drives the first-person grammar rules in the system prompt (`_persona_instructions` in `app.py`). If you change `AZURE_TTS_VOICE_NAME` to a voice of the other gender, change `ASSISTANT_GENDER` too.
- **Chainlit**: keep `.chainlit/translations/*.json` from the installed Chainlit version. Stale 1.x files render blank labels in 2.x. After a Chainlit upgrade, delete the folder and run `chainlit init`. `[features.audio] enabled = true` / `sample_rate = 24000` are required.

## Known pitfalls (already handled; don't regress)

- On Windows, `az acr build` can crash while streaming logs (`'charmap' codec can't encode`). `deploy.ps1` uses `--no-logs --no-wait` and polls `az acr task show-run`.
- `az` on Windows is a `.cmd` wrapper. Avoid parentheses in `--query` strings (e.g. `length(@)`), because cmd.exe mis-parses them.
- A new managed identity needs time for `AcrPull` to propagate. `deploy.ps1` waits before switching to the ACR image; otherwise the revision hangs for about 15 minutes.
- Azure SQL: Entra-only server. The connection uses an access token (`SQL_COPT_SS_ACCESS_TOKEN`), a 60 s login timeout and transient-error retries. The image includes ODBC Driver 18, and the build fails if it's missing.
- Container App probes are generous (startup allows ~3 min), and ingress uses sticky sessions because Chainlit session state is in memory.
