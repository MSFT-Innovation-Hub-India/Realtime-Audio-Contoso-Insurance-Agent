"""Offline unit tests (no Azure access needed): python -m pytest tests"""

import asyncio
import json

import pyodbc
import pytest

import app
import functions
import ui_config
from voicelive_client import VoiceLiveModelClient


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message)["type"])

    async def close(self):
        pass


class ClosingWS(FakeWS):
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def make_client():
    client = VoiceLiveModelClient("test prompt")
    client.ws = FakeWS()
    dispatched = []
    client.dispatch = lambda name, event: dispatched.append(name)
    return client, dispatched


def test_cough_does_not_interrupt_but_sustained_speech_does():
    async def run():
        client, dispatched = make_client()
        client._active_response_id = "resp_1"
        client._update_response_idle()
        ev = client._handle_server_event

        # short burst (cough): speech_stopped arrives before the debounce window
        await ev({"type": "input_audio_buffer.speech_started", "item_id": "i1"})
        await asyncio.sleep(0.2)
        await ev({"type": "input_audio_buffer.speech_stopped", "item_id": "i1"})
        await asyncio.sleep(client.interrupt_debounce_ms / 1000 + 0.1)
        assert dispatched == []

        # sustained speech: barge-in after the debounce, and stale audio of the old response is dropped
        await ev({"type": "input_audio_buffer.speech_started", "item_id": "i2"})
        await asyncio.sleep(client.interrupt_debounce_ms / 1000 + 0.15)
        assert dispatched == ["conversation.interrupted", "user.turn.started"]
        await ev({"type": "response.audio.delta", "response_id": "resp_1", "delta": "AAAA"})
        assert "conversation.updated" not in dispatched

    asyncio.run(run())


def test_short_committed_utterance_is_confirmed_on_commit():
    async def run():
        client, dispatched = make_client()
        ev = client._handle_server_event
        await ev({"type": "input_audio_buffer.speech_started", "item_id": "i3"})
        await ev({"type": "input_audio_buffer.speech_stopped", "item_id": "i3"})
        await ev({"type": "input_audio_buffer.committed", "item_id": "i3"})
        assert dispatched == ["conversation.interrupted", "user.turn.started"]
        assert client._response_requested

    asyncio.run(run())


def test_tool_result_is_deferred_while_a_server_response_is_pending():
    async def run():
        client, _ = make_client()
        ev = client._handle_server_event
        await ev({"type": "input_audio_buffer.committed", "item_id": "i9"})  # server will create a response
        call = {"name": "get_grievance_status_def", "call_id": "c1", "arguments": json.dumps({"grievance_id": "abc"})}
        await client._run_function_calls([call])
        assert client.ws.sent == ["conversation.item.create"]  # output sent, response.create deferred
        assert client._followup_pending

        await ev({"type": "response.created", "response": {"id": "resp_9"}})
        await ev({"type": "response.done", "response": {"id": "resp_9", "status": "completed", "output": [{"type": "message"}]}})
        await asyncio.sleep(0.05)
        assert client.ws.sent == ["conversation.item.create", "response.create"]
        assert not client._followup_pending

    asyncio.run(run())


def test_function_calls_after_a_spoken_preamble_are_executed():
    async def run():
        client, dispatched = make_client()
        await client._handle_server_event(
            {
                "type": "response.done",
                "response": {
                    "id": "r1",
                    "status": "completed",
                    "output": [
                        {"type": "message"},
                        {"type": "function_call", "name": "get_grievance_status_def", "call_id": "c1", "arguments": '{"grievance_id": "x"}'},
                    ],
                },
            }
        )
        await asyncio.sleep(0.2)
        assert "tool.call" in dispatched
        assert client.ws.sent[:2] == ["conversation.item.create", "response.create"]

    asyncio.run(run())


def test_active_response_race_error_is_not_shown_and_schedules_retry():
    async def run():
        client, dispatched = make_client()
        await client._handle_server_event(
            {"type": "error", "error": {"code": "conversation_already_has_active_response", "message": "x"}}
        )
        assert "error" not in dispatched
        assert client._followup_pending and not client._response_requested

    asyncio.run(run())


def test_watchdog_clears_an_unacknowledged_response_request():
    async def run():
        client, _ = make_client()
        client._mark_response_requested()
        assert not client._response_idle.is_set()
        await client._response_request_watchdog(client._request_seq, timeout_s=0.1)
        assert client._response_idle.is_set() and not client._response_requested

    asyncio.run(run())


def test_unexpected_close_notifies_ui_but_explicit_disconnect_does_not():
    async def run():
        client, dispatched = make_client()
        client.ws = ClosingWS()
        await client.receive()
        assert dispatched == ["error"] and client.ws is None

        client2, dispatched2 = make_client()
        client2.ws = ClosingWS()
        client2._closing = True
        await client2.receive()
        assert dispatched2 == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "transcript, is_noise",
    [("", True), ("  ", True), ("(coughs)", True), ("[noise]", True), ("*cough*", True), ("...", True),
     ("(coughs) [background noise]", True),
     ("Yes", False), ("What is my claim status?", False), ("(1) first claim", False), ("हाँ", False),
     ("(coughs) Hello, what is my claim status? (coughs)", False), ("[noise] हाँ, बताइए [noise]", False)],
)
def test_noise_transcript_filter(transcript, is_noise):
    assert app._is_noise_transcript(transcript) is is_noise


def test_secrets_are_redacted_in_the_config_panel(monkeypatch):
    monkeypatch.setattr(ui_config.DefaultConfig, "attlassian_api_key", "FAKE-jira-token-super-secret=")
    monkeypatch.setattr(ui_config.DefaultConfig, "attlassian_user_name", "someone.private@example.com")
    markdown = ui_config.build_config_markdown()
    assert "super-secret" not in markdown and "FAKE-jira" not in markdown
    assert "someone.private" not in markdown and "example.com" not in markdown
    assert ui_config.redact_email("jane.doe@contoso.com") == "ja••••••@c••••••.com"
    assert ui_config.redact_secret(None) == "not configured"


def test_persona_prompt_enforces_first_person_gender():
    female = app._persona_instructions("female")
    assert "सकती" in female and "female voice" in female
    male = app._persona_instructions("male")
    assert "सकता" in male and "male voice" in male
    assert app._persona_instructions(app.DefaultConfig.assistant_gender) in app.system_prompt


@pytest.mark.parametrize(
    "error, transient",
    [
        (pyodbc.OperationalError("HYT00", "[HYT00] Login timeout expired"), True),
        (pyodbc.OperationalError("08001", "TCP Provider: connection failed"), True),
        (pyodbc.Error("42000", "Database 'x' on server 'y' is not currently available. (40613)"), True),
        (pyodbc.ProgrammingError("42S02", "Invalid object name 'foo'"), False),
        (pyodbc.InterfaceError("28000", "Login failed for user '<token-identified principal>'"), False),
    ],
)
def test_sql_transient_error_classification(error, transient):
    assert functions._is_transient_sql_error(error) is transient
