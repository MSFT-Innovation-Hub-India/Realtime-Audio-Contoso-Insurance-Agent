import os
from dotenv import load_dotenv

load_dotenv(override=True)


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default, cast=float):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return cast(value)


class DefaultConfig:
    """Application configuration, loaded from environment variables / .env"""

    # --- Azure Voice Live API (Microsoft Foundry resource) ---
    # e.g. https://<foundry-resource>.cognitiveservices.azure.com or https://<foundry-resource>.services.ai.azure.com
    voice_live_endpoint = os.getenv("AZURE_VOICE_LIVE_ENDPOINT")
    voice_live_api_version = os.getenv("AZURE_VOICE_LIVE_API_VERSION", "2026-04-10")
    voice_live_model = os.getenv("VOICE_LIVE_MODEL", "gpt-realtime-2.1")
    voice_name = os.getenv("AZURE_TTS_VOICE_NAME", "en-IN-Meera:DragonHDV2.3Neural")
    voice_temperature = _num("AZURE_TTS_VOICE_TEMPERATURE", 0.8)
    input_transcription_model = os.getenv("INPUT_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")

    # --- Turn detection / noise robustness (see README: "Handling coughs and brief noises") ---
    vad_type = os.getenv("VAD_TYPE", "azure_semantic_vad")
    vad_threshold = _num("VAD_THRESHOLD", 0.6)
    vad_prefix_padding_ms = _num("VAD_PREFIX_PADDING_MS", 300, int)
    vad_speech_duration_ms = _num("VAD_SPEECH_DURATION_MS", 300, int)
    vad_silence_duration_ms = _num("VAD_SILENCE_DURATION_MS", 700, int)
    vad_remove_filler_words = _bool("VAD_REMOVE_FILLER_WORDS", True)
    interrupt_debounce_ms = _num("INTERRUPT_DEBOUNCE_MS", 450, int)

    # --- Atlassian Jira (API token based) ---
    attlassian_api_key = os.getenv("attlassian_api_key")
    attlassian_user_name = os.getenv("attlassian_user_name")
    attlassian_url = os.getenv("attlassian_url")
    grievance_project_key = os.getenv("grievance_project_key")
    grievance_type = os.getenv("grievance_type", "Task")
    grievance_project_name = os.getenv("grievance_project_name")

    # --- Azure AI Search (Microsoft Entra ID / managed identity) ---
    ai_search_url = os.getenv("ai_search_url")
    ai_index_name = os.getenv("ai_index_name")
    ai_semantic_config = os.getenv("ai_semantic_config")

    # --- Azure SQL Database (Microsoft Entra ID / managed identity) ---
    az_db_server = os.getenv("az_db_server")
    az_db_database = os.getenv("az_db_database")
    az_db_driver = os.getenv("az_db_driver", "ODBC Driver 18 for SQL Server")
    # login timeout (seconds); generous so cold starts in Azure Container Apps don't fail the first query
    az_db_connection_timeout = _num("az_db_connection_timeout", 60, int)

    ai_assistant_organization_name = "Contoso Insurance"
    customer_name = os.getenv("customer_name", "Srikantan")
    # Gender of the assistant persona; must match the TTS voice (Meera is female). Drives first-person
    # grammatical gender in Hindi and other gendered languages ("मैं कर सकती हूँ" vs "मैं कर सकता हूँ").
    assistant_gender = (os.getenv("ASSISTANT_GENDER", "female").strip().lower() or "female")
    # Show the solution configuration side panel automatically when a chat starts
    show_config_panel_on_start = _bool("SHOW_CONFIG_PANEL_ON_START", False)
