# OpenClaw Integration

This document describes how OpenClaw should consume the assistant-first `email-manager` runtime.

## Purpose

`email-manager` is the Gmail watcher and durable ingestion backend.

OpenClaw is responsible for:

- deciding whether an email matters now
- learning user preference over time
- revisiting past assumptions when ground context changes
- choosing whether to surface, batch, ignore, or revisit messages

`email-manager` should not own those product decisions anymore.

## Integration Model

Recommended contract:

1. `email-manager` sends `email.ingested` events to OpenClaw over HTTP.
2. OpenClaw acknowledges receipt quickly.
3. OpenClaw evaluates the message against its memory, ground context, and preference model.
4. OpenClaw sends the evaluation result back to `email-manager`.
5. If OpenClaw later changes context, it can ask `email-manager` to requeue existing messages.

This is intentionally not:

- Gmail Pub/Sub direct to OpenClaw
- heartbeat-driven primary ingestion
- inline LLM classification inside `email-manager`

## Outbound Event

`email-manager` POSTs each queued evaluation request to:

```text
ASSISTANT_BRIDGE_URL
```

Headers:

```text
Content-Type: application/json
Idempotency-Key: <request.idempotency_key>
User-Agent: email-manager/assistant-bridge
Authorization: Bearer <ASSISTANT_SHARED_SECRET>   # if configured
```

Payload shape:

```json
{
  "event_type": "email.ingested",
  "payload_version": "v1",
  "request": {
    "request_id": 42,
    "email_message_id": 1201,
    "trigger_type": "email.ingested",
    "trigger_reference": "gmail_message_ingest_job:998",
    "idempotency_key": "email.ingested:7:1908cbe3f54"
  },
  "account": {
    "account_id": 7,
    "email": "user@example.com"
  },
  "email": {
    "gmail_message_id": "1908cbe3f54",
    "gmail_thread_id": "1908cbcdb12",
    "history_id": 884221,
    "message_internal_at": "2026-04-13T11:52:03+00:00",
    "sender_name": "Example Sender",
    "sender_email": "sender@example.com",
    "sender_domain": "example.com",
    "to_recipients": [
      {
        "name": "User",
        "email": "user@example.com"
      }
    ],
    "cc_recipients": [],
    "subject": "Example subject",
    "snippet": "Short Gmail snippet",
    "normalized_body_text": "Canonical normalized body text",
    "labels": ["INBOX", "CATEGORY_UPDATES"],
    "headers": {
      "subject": "Example subject",
      "from": "Example Sender <sender@example.com>"
    },
    "raw_size_bytes": 18342,
    "gmail_open_url": "https://mail.google.com/mail/u/0/?authuser=user%40example.com#inbox/1908cbcdb12"
  },
  "callbacks": {
    "evaluation_result_url": "https://email-manager.example/assistant/evaluations",
    "requeue_url": "https://email-manager.example/assistant/requeue"
  }
}
```

## Delivery Semantics

OpenClaw should treat `request.idempotency_key` as the stable dedupe key.

Important rules:

- The same event may be delivered more than once.
- OpenClaw should make evaluation idempotent by `idempotency_key`.
- Acknowledgement means receipt, not necessarily final evaluation.
- `email-manager` may retry if the HTTP call fails or returns non-2xx.

## Expected Response Modes

OpenClaw can use either of these modes.

### Mode 1: Fast Ack, Async Callback

Recommended for most cases.

OpenClaw responds quickly with `202` or `200`:

```json
{
  "acknowledged": true
}
```

Then, after evaluation, OpenClaw POSTs the result to `callbacks.evaluation_result_url`.

### Mode 2: Inline Result

Allowed when evaluation is available immediately.

OpenClaw responds with `200` and includes the final decision:

```json
{
  "decision": "surface_now",
  "importance": "high",
  "reason_summary": "Payment due tomorrow and historically important to the user.",
  "surface_target": "primary_chat",
  "assistant_trace_id": "trace_abc123"
}
```

If a `decision` field is present in the immediate response, `email-manager` will treat the request as completed without waiting for a callback.

## Evaluation Result Callback

OpenClaw should POST evaluation results to:

```text
POST /assistant/evaluations
```

Headers:

```text
Content-Type: application/json
Authorization: Bearer <ASSISTANT_SHARED_SECRET>   # if configured
```

Body:

```json
{
  "request_id": 42,
  "idempotency_key": "email.ingested:7:1908cbe3f54",
  "decision": "surface_now",
  "importance": "high",
  "reason_summary": "Time-sensitive billing email with required action.",
  "surface_target": "primary_chat",
  "assistant_trace_id": "trace_abc123"
}
```

Rules:

- `request_id` or `idempotency_key` must be present.
- `decision` is required.
- `assistant_trace_id` is optional but strongly recommended.
- Additional fields are preserved in the raw result ledger payload.

Suggested `decision` values:

- `surface_now`
- `batch_for_later`
- `ignore`
- `needs_more_context`
- `failed`

Suggested `importance` values:

- `high`
- `medium`
- `low`

## Context Change / Re-evaluation

This is the critical part for OpenClaw’s learning model.

If OpenClaw’s ground context changes, it can requeue previously ingested emails instead of relying on static suppression rules.

POST to:

```text
POST /assistant/requeue
```

Headers:

```text
Content-Type: application/json
Authorization: Bearer <ASSISTANT_SHARED_SECRET>   # if configured
```

By internal email IDs:

```json
{
  "trigger_type": "assistant.context_changed",
  "trigger_reference": "context-shift:model-y-important",
  "email_message_ids": [1201, 1202, 1203]
}
```

By Gmail message IDs:

```json
{
  "trigger_type": "assistant.context_changed",
  "trigger_reference": "context-shift:model-y-important",
  "gmail_message_ids": ["1908cbe3f54", "1908cbff122"]
}
```

`trigger_reference` is required so the requeue action is auditable and idempotent.

This is the backend primitive that allows OpenClaw to do the behavior discussed in the design:

- previously ignore marketing X
- later learn that marketing Y is now important
- re-evaluate future or recent marketing X messages when they resemble marketing Y

## Reliability Expectations

OpenClaw should assume:

- `email-manager` ingestion is durable before it calls OpenClaw
- retries can happen
- message order is usually near-realtime but not guaranteed under retry or backlog recovery
- requeue requests may cause the same email to be evaluated again under a different trigger

OpenClaw should provide:

- idempotent event handling
- short acknowledgement latency
- durable internal handling after ack
- a stable mapping between `assistant_trace_id` and internal reasoning/logging

## What OpenClaw Should Not Assume

OpenClaw should not assume:

- every decision is message-only forever; thread-aware reasoning may evolve later
- old ignore decisions are permanent
- `email-manager` owns preference learning
- Gmail Pub/Sub events are the ground truth; canonical `email_messages` are

## Recommended OpenClaw Behavior

When an `email.ingested` event arrives:

1. Read the canonical email content and metadata.
2. Evaluate it against current ground context and learned preferences.
3. Decide whether it should:
   - surface now
   - be queued for later
   - be ignored silently
   - request more context
4. Send the result back to `email-manager`.
5. Surface user-facing output through the OpenClaw chat experience, not through `email-manager`.

That keeps the system aligned with the intended architecture:

- `email-manager` = watcher + ingestion + durable bridge
- OpenClaw = judgment + learning + delivery to the user
