# Message Sequencing

## Problem

The Voice Live / Realtime API emits events for the user's input transcript
(`conversation.item.input_audio_transcription.completed`) and for the assistant's response transcript
(`response.audio_transcript.delta`) asynchronously. The user transcript often arrives *after* the assistant has
started responding, so naively appending messages puts the answer above the question.

## Approach

### 1. Placeholder per user turn, keyed by the server `item_id`

| Server event (voicelive_client.py) | UI event | Chat window (app.py) |
| --- | --- | --- |
| `input_audio_buffer.speech_started` plus speech lasting > `INTERRUPT_DEBOUNCE_MS` | `user.turn.started` | Placeholder `🎤 Listening...` created with id `user-<item_id>` |
| `input_audio_buffer.speech_stopped` | `user.speech.stopped` | Placeholder updated to `🔄 Processing...` |
| `input_audio_buffer.committed` (if no placeholder yet, e.g. a short "yes") | `user.turn.started` | Placeholder `🔄 Processing...` created |
| `conversation.item.input_audio_transcription.completed` | `conversation.input.text.done` | Placeholder replaced by the transcript, or **removed** if the transcript is empty/noise such as `(coughs)` |
| `response.audio_transcript.delta` | `conversation.text.delta` | Assistant message (id = assistant `item_id`) created, then streamed |

The placeholder is always created before the server creates the response (`committed` precedes `response.created`),
so the user message is rendered above the assistant message. Because the server's `item_id` is the key, a late
transcript always lands in the right bubble, even if several turns overlap.

### 2. Single ordered dispatch queue

`VoiceLiveModelClient.dispatch()` puts UI events on an `asyncio.Queue` that a single worker drains. Previously each
event was handled in its own `asyncio.create_task`, so a message `update()` could race ahead of its `send()`, or
audio chunks could be reordered. The queue applies every send, update and audio chunk in arrival order.

### 3. Typed messages

Typed messages are already visible in the chat. The client stops playback and cancels any in-flight response
(`response.cancel`), waits for it to finish, then sends the new message and `response.create`.

### 4. Stale audio after barge-in

When a user turn is confirmed during an assistant response, that response's id is added to a suppression set.
Its remaining `response.audio.delta` chunks are dropped instead of playing after the interruption.

## Testing recommendations

1. Ask a question by voice and check the user bubble appears above the assistant's answer.
2. Cough or tap the mic while the assistant is speaking: playback should continue, with no stray user bubble.
3. Talk over the assistant: playback should stop within about half a second, and the new answer should follow your question.
4. Mix voice and typed questions in the same session.
