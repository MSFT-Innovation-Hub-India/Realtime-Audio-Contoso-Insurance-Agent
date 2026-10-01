"""Builds the configuration side panel shown in the UI.

Azure resources are accessed with managed identity, so their endpoints and names are shown in full.
Credentials and personal identifiers (Atlassian API token, Atlassian user name) are redacted.
"""

import os
from urllib.parse import urlparse

from envconfig import DefaultConfig

POWERED_BY = "Powered by Azure Voice Live in Microsoft Foundry"
VOICE_LIVE_DOCS = "https://learn.microsoft.com/azure/ai-services/speech-service/voice-live"


def redact_secret(value: str | None) -> str:
    """Never reveals any part of a secret; only whether it is configured."""
    return "•••••••• (configured)" if value else "not configured"


def redact_email(value: str | None) -> str:
    """jane.doe@contoso.com -> ja••••••@c••••••.com"""
    if not value:
        return "not configured"
    if "@" not in value:
        return value[:2] + "•" * max(len(value) - 2, 3)
    local, _, domain = value.partition("@")
    domain_name, dot, tld = domain.rpartition(".")
    masked_local = local[:2] + "•" * max(len(local) - 2, 3)
    masked_domain = (domain_name[:1] + "•" * max(len(domain_name) - 1, 3) + dot + tld) if dot else "•••"
    return f"{masked_local}@{masked_domain}"


def _host(url: str | None) -> str:
    if not url:
        return "not configured"
    return urlparse(url).netloc or url


def _identity_label() -> str:
    if os.getenv("CONTAINER_APP_NAME"):
        client_id = os.getenv("AZURE_CLIENT_ID")
        kind = "user-assigned" if client_id else "system-assigned"
        return f"Managed identity ({kind}) of Container App {os.getenv('CONTAINER_APP_NAME')}"
    return "Developer sign-in (Azure CLI / VS Code) via DefaultAzureCredential"


def _row(label: str, value) -> str:
    text = str(value) if value not in (None, "") else "—"
    # escape ':' so values like "en-IN-Meera:DragonHDV2.3Neural" aren't parsed as markdown directives
    text = text.replace("|", "\\|").replace(":", "\\:")
    return f"| {label} | {text} |"


def build_config_markdown() -> str:
    c = DefaultConfig
    vad_extras = (
        f"speech ≥ {c.vad_speech_duration_ms} ms · filler words removed: {c.vad_remove_filler_words}"
        if c.vad_type.startswith("azure_semantic_vad")
        else "—"
    )
    sections = [
        f"### 🎙️ {POWERED_BY}",
        f"Speech-to-speech with [Azure Voice Live API]({VOICE_LIVE_DOCS}) and a GPT-Realtime model, "
        "with client-side function calling over keyless (Microsoft Entra ID) Azure access.",
        "",
        "#### Azure Voice Live · Microsoft Foundry",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Foundry resource", _host(c.voice_live_endpoint)),
        _row("Endpoint", (c.voice_live_endpoint or "").rstrip("/") + "/voice-live/realtime"),
        _row("API version", c.voice_live_api_version),
        _row("Voice Live model", c.voice_live_model),
        _row("Transcription model", c.input_transcription_model),
        _row("TTS voice", c.voice_name),
        _row("Persona gender", f"{c.assistant_gender} (first-person grammar in Hindi and other gendered languages)"),
        _row("Voice temperature", c.voice_temperature),
        _row("Authentication", "Microsoft Entra ID (bearer token)"),
        "",
        "#### Turn detection & audio processing",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Turn detection", c.vad_type),
        _row("Threshold", c.vad_threshold),
        _row("Silence to end turn", f"{c.vad_silence_duration_ms} ms"),
        _row("Prefix padding", f"{c.vad_prefix_padding_ms} ms"),
        _row("Noise guards", vad_extras),
        _row("Barge-in debounce", f"{c.interrupt_debounce_ms} ms"),
        _row("Noise suppression", "azure_deep_noise_suppression"),
        _row("Echo cancellation", "server_echo_cancellation"),
        _row("Audio format", "PCM16 · 24 kHz"),
        "",
        "#### Knowledge base · Azure AI Search",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Service", c.ai_search_url),
        _row("Index", c.ai_index_name),
        _row("Semantic configuration", c.ai_semantic_config),
        _row("Authentication", "Managed identity · Search Index Data Reader"),
        "",
        "#### Claims · Azure SQL Database",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Server", c.az_db_server),
        _row("Database", c.az_db_database),
        _row("Table", "Health_Insurance_Claims"),
        _row("Authentication", "Managed identity · Entra access token (db_datareader)"),
        "",
        "#### Grievances · Atlassian Jira Cloud",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Site", c.attlassian_url),
        _row("Project", f"{c.grievance_project_name or ''} ({c.grievance_project_key})".strip()),
        _row("Issue type", c.grievance_type),
        _row("User name", redact_email(c.attlassian_user_name)),
        _row("API token", redact_secret(c.attlassian_api_key)),
        "",
        "#### Runtime",
        "| Setting | Value |",
        "| --- | --- |",
        _row("Azure identity", _identity_label()),
        _row("Customer (claims scope)", c.customer_name),
    ]
    return "\n".join(sections)
