# OpenClaw Implementation Brief

This document is the direct handoff for the OpenClaw side of the assistant-first `email-manager` integration.

Use this as the implementation target for OpenClaw.

## Current State

`email-manager` is already deployed as a hardened Gmail watcher and ingestion backend.

What is live now:

- Gmail `watch()` renewal
- Pub/Sub intake
- durable history sync
- canonical email storage
- durable assistant evaluation outbox
- callback API:
  - `POST /assistant/evaluations`
  - `POST /assistant/requeue`

What is intentionally not live yet:

- outbound dispatch from `email-manager` to OpenClaw

Current runtime mode:

- `ASSISTANT_DISPATCH_ENABLED=false`

That means `email-manager` is ready to hand emails to OpenClaw, but OpenClaw still needs its intake adapter before dispatch can be enabled safely.

## Goal

OpenClaw must become the product brain for incoming email.

That means OpenClaw should own:

- importance judgment
- timing judgment
- personal preference learning
- re-evaluation when ground context changes
- user-facing surfacing through OpenClaw chat

`email-manager` should remain:

- watcher
- ingestion backend
- durable message store
- dispatch/callback bridge

## Required Deliverable

OpenClaw needs a small inbound HTTP adapter for `email-manager` events.

Recommended endpoint:

```text
POST /internal/email-manager/events
```

The exact path can differ, but it must be stable and reachable from `email-manager`.

## Why This Adapter Is Needed

OpenClaw already has a generic `/hooks` system, including `/hooks/agent`.

That is useful, but it is not a drop-in target for the current `email-manager` payload because:

- `email-manager` sends a rich `email.ingested` event envelope
- the payload includes account metadata, Gmail metadata, canonical body text, idempotency data, and callback URLs
- OpenClaw must preserve those fields and not reduce them to a shallow text-only webhook

So OpenClaw should do one of these:

1. build a dedicated adapter endpoint for `email.ingested`, then route internally to agent evaluation
2. build a mapping layer that accepts the event envelope and translates it into the internal hook/agent model

Either approach is fine.

What should not happen:

- forcing `email-manager` to directly speak a reduced `/hooks/agent` payload
- moving judgment back into `email-manager`
- making OpenClaw listen directly to Gmail Pub/Sub

## Required Behavior

### 1. Receive the event

OpenClaw must accept:

- `Content-Type: application/json`
- `Idempotency-Key: <request.idempotency_key>`
- optional `Authorization: Bearer <shared secret>`

Payload shape is defined in [openclaw-integration.md](/home/enr1c0/project/enrico/email-manager/docs/openclaw-integration.md).

The event type is:

```json
{
  "event_type": "email.ingested"
}
```

### 2. Acknowledge fast

OpenClaw should respond quickly with:

```json
{
  "acknowledged": true
}
```

Recommended status:

- `202 Accepted`
- or `200 OK`

Do not block the HTTP response on full evaluation unless the result is immediately available with negligible latency.

### 3. Make handling idempotent

Use:

- `request.idempotency_key`

as the primary dedupe key.

OpenClaw must treat duplicate deliveries as normal.

The same email event may be sent again if:

- the bridge call times out
- the bridge returns non-2xx
- `email-manager` is replaying a failed dispatch

### 4. Evaluate with OpenClaw’s own memory and context

OpenClaw should evaluate the message using:

- the current user preference model
- ground context
- prior sender/domain/topic memory
- thread/message content
- current product rules about what deserves interruption

The important rule is this:

evaluation is not static.

If OpenClaw later learns that a previously ignored class of email now matters, it should be able to re-evaluate related messages.

### 5. Return the result to `email-manager`

After evaluation, OpenClaw must POST back to:

- `callbacks.evaluation_result_url`

with the shape documented in [openclaw-integration.md](/home/enr1c0/project/enrico/email-manager/docs/openclaw-integration.md).

Minimum required fields:

- `decision`
- `request_id` or `idempotency_key`

Recommended fields:

- `importance`
- `reason_summary`
- `surface_target`
- `assistant_trace_id`

### 6. Support future context-driven requeue

When OpenClaw’s understanding changes, it should call:

- `POST /assistant/requeue`

on `email-manager`.

This is the backend primitive for:

- ground context changes
- new preference learning
- sender/topic reinterpretation
- revisiting recent emails that were previously ignored

## Recommended OpenClaw-Side Flow

Recommended flow:

1. Receive `email.ingested`.
2. Authenticate the request.
3. Check idempotency by `request.idempotency_key`.
4. Persist or enqueue the event internally.
5. Return fast ack.
6. Run evaluation asynchronously.
7. Write internal trace/log against `assistant_trace_id`.
8. POST the final evaluation result back to `email-manager`.
9. Surface user-facing output only through OpenClaw chat.

This is the preferred separation:

- `email-manager` owns durable intake
- OpenClaw owns judgment and surfacing

## Suggested Decision Vocabulary

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

These already match the bridge contract on the `email-manager` side.

## Suggested Internal Mapping In OpenClaw

The adapter should turn the incoming event into an internal evaluation task roughly like this:

- source: `email-manager`
- source_event_type: `email.ingested`
- external_idempotency_key: `request.idempotency_key`
- account_email: `account.email`
- sender_email: `email.sender_email`
- sender_domain: `email.sender_domain`
- subject: `email.subject`
- body: `email.normalized_body_text`
- snippet: `email.snippet`
- labels: `email.labels`
- gmail_thread_id: `email.gmail_thread_id`
- gmail_message_id: `email.gmail_message_id`
- open_url: `email.gmail_open_url`
- callback_target: `callbacks.evaluation_result_url`

This gives OpenClaw enough structure to:

- reason on the email
- connect it to preference memory
- surface a meaningful explanation

## What OpenClaw Should Not Do

Do not:

- consume Gmail Pub/Sub directly
- require `email-manager` to batch/group emails before delivery by default
- rebuild Gmail fetch logic inside OpenClaw
- treat this as a Telegram-style notification bot flow
- collapse the event into only `subject + body` and discard metadata

The metadata matters for future preference learning.

## Acceptance Criteria

The OpenClaw side is ready when all of these are true:

1. OpenClaw exposes a stable HTTP endpoint for `email-manager` ingestion.
2. It authenticates the request.
3. It deduplicates by `request.idempotency_key`.
4. It responds quickly with `200` or `202`.
5. It can asynchronously evaluate the email.
6. It POSTs a valid result to `callbacks.evaluation_result_url`.
7. It keeps enough internal traceability to debug why a message was surfaced or ignored.
8. It can later trigger `POST /assistant/requeue` when context changes.

## Activation Checklist

Once OpenClaw has this adapter:

1. Choose the production bridge URL.
2. Ensure OpenClaw and `email-manager` agree on the shared bearer secret.
3. Set runtime env in `email-manager`:
   - `ASSISTANT_DISPATCH_ENABLED=true`
   - `ASSISTANT_BRIDGE_URL=<real OpenClaw endpoint>`
   - `PUBLIC_BASE_URL=<real callback base URL for email-manager>`
4. Restart `email-manager`.
5. Confirm:
   - outbound bridge requests succeed
   - callbacks reach `/assistant/evaluations`
   - decisions are stored in `assistant_evaluation_results`

## Reference Docs

- [docs/openclaw-integration.md](/home/enr1c0/project/enrico/email-manager/docs/openclaw-integration.md)
- [docs/assistant-first-v2.md](/home/enr1c0/project/enrico/email-manager/docs/assistant-first-v2.md)
- [README.md](/home/enr1c0/project/enrico/email-manager/README.md)

