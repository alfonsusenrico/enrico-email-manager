# Assistant-First Email Manager v2

## Purpose

This document proposes how `email-manager` should evolve from a Telegram-centric AI notification bot into a Gmail ingestion backend for the main assistant.

The design is based on the current repository state as of 2026-04-13, not on the original proposal alone.

## Current Architecture Summary

### What exists today

The current service is a single Python process that wires together all major responsibilities:

- Gmail account bootstrap and watch renewal
- Cloud Pub/Sub pull consumption
- Gmail history sync and message fetch
- OpenAI summarization and classification
- suppression and digest logic
- Telegram delivery and Telegram callback handling

This happens directly in [`app/main.py`](/home/enr1c0/project/enrico/email-manager/app/main.py:15), which constructs `GmailClient`, `OpenAIClient`, `TelegramClient`, `GmailSyncService`, `WatchManager`, `PubSubWorker`, then starts the Telegram webhook server.

### Runtime flow today

The current hot path is:

1. Gmail watch publishes mailbox changes to Pub/Sub.
2. [`app/pubsub_worker.py`](/home/enr1c0/project/enrico/email-manager/app/pubsub_worker.py:55) parses `emailAddress` and `historyId`.
3. [`app/gmail_sync.py`](/home/enr1c0/project/enrico/email-manager/app/gmail_sync.py:78) calls `users.history.list`.
4. For each new Gmail message, the service fetches the message body from Gmail.
5. The service calls OpenAI inline to produce `category`, `confidence`, `summary`, and `importance`.
6. The service checks static suppression rules in Postgres.
7. If not suppressed, it formats and sends a Telegram message with inline buttons.
8. Telegram callbacks directly mutate Gmail state and local notification state.

### Current state model

The current schema is optimized for the Telegram product:

- `gmail_accounts`: watch state and history cursor
- `notifications`: one row per Gmail message plus Telegram delivery fields
- `suppressions`: sender/domain/category mute rules
- `usage_daily`: OpenAI usage and cost accounting
- `app_state`: includes the single global Telegram chat id

The schema can support low-volume personal use, but it is not yet a neutral ingestion model.

## Current Project Status Snapshot

### Built and reusable now

- Gmail account bootstrap and watch renewal
- Pub/Sub intake loop with restart backoff
- Gmail history sync
- per-account dedupe by `(account_id, gmail_message_id)`
- Gmail message fetch and body extraction
- basic Postgres persistence and SQL migration flow
- Gmail mailbox mutation helpers for archive and trash

### Built, but tightly coupled to the old product shape

- OpenAI-based summarization and categorization
- importance scoring inside the repo
- Telegram message formatting and sending
- Telegram callback-driven archive, trash, not-interested, and undo flows
- digest queue behavior
- suppression logic based on sender, domain, and category

### Partially built, but not yet sufficient for assistant-first operation

- auth backoff and watch renewal hardening
- low-confidence handling
- multi-account Gmail support
- usage tracking
- suppression scope expansion in migration `0002`

These exist, but they are still wired around the Telegram-era product model.

### Missing for v2

- canonical email store that preserves enough content for assistant evaluation and re-evaluation
- durable evaluation outbox between ingestion and assistant judgment
- explicit assistant bridge contract
- evaluation request and result ledger
- replay and dead-letter flow per stage
- context-change re-evaluation path
- thread-aware evaluation support
- assistant-facing mailbox action interface
- operational health model for separate ingestion, dispatch, and callback stages

### Should be deprecated next

- `notifications` as the primary domain object
- `suppressions` as the long-term preference system
- Telegram webhook runtime as a required service
- OpenAI dependency inside `email-manager` for first-line inbox judgment
- digest logic as a repo-owned delivery behavior

## Reusable Pieces

These parts are strong candidates to keep and evolve:

- Gmail OAuth and client construction in [`app/gmail_client.py`](/home/enr1c0/project/enrico/email-manager/app/gmail_client.py:24)
- Gmail watch renewal in [`app/watch_manager.py`](/home/enr1c0/project/enrico/email-manager/app/watch_manager.py:19)
- Pub/Sub pull worker with restart backoff in [`app/pubsub_worker.py`](/home/enr1c0/project/enrico/email-manager/app/pubsub_worker.py:15)
- account bootstrap and migration runner in [`app/db.py`](/home/enr1c0/project/enrico/email-manager/app/db.py:17) and [`scripts/apply_migrations.py`](/home/enr1c0/project/enrico/email-manager/scripts/apply_migrations.py:1)
- per-account sync locking and auth backoff in [`app/gmail_sync.py`](/home/enr1c0/project/enrico/email-manager/app/gmail_sync.py:43)
- Gmail archive and trash helpers in [`app/gmail_client.py`](/home/enr1c0/project/enrico/email-manager/app/gmail_client.py:52)

These pieces already represent the beginnings of a durable inbox ingestion service.

## Why The Current Design Does Not Fit v2

### 1. Product brain and transport are fused into one synchronous path

`GmailSyncService` currently performs fetch, LLM judgment, suppression, and Telegram delivery inline inside the Pub/Sub-triggered sync path. That means:

- downstream latency directly holds up event processing
- assistant evaluation cannot be retried independently
- delivery failure and classification failure are not isolated from ingestion

For v2, inbox ingestion must succeed even when the assistant is unavailable.

### 2. The repo is Telegram-shaped all the way down

The config requires Telegram settings and OpenAI settings up front. The `notifications` table stores Telegram chat/message ids as first-class fields. The runtime blocks on `run_webhook()` and keeps a visible Telegram bot UI inside the repo.

That is the opposite of the desired product direction: one coherent assistant, not multiple visible bots.

### 3. Preference learning is static and rule-based

The current suppression model is sender/domain/category oriented. It captures "mute rules", not revisable preferences grounded in broader user context.

That is too weak for cases like:

- a sender was historically unimportant, but becomes relevant because of a newly important product/topic/model
- a class of emails becomes relevant only under certain business or personal contexts
- a user decision should influence future evaluation without permanently hard-coding a sender

Ground context needs to live above sender/category rules.

### 4. The current schema does not preserve enough canonical email context

The repo stores summary, category, and a few metadata fields, but not a durable canonical message representation. The body is fetched from Gmail, sent to OpenAI, and then discarded.

That creates a major limitation:

- future re-evaluation becomes weak or impossible without re-fetching Gmail every time
- contextual preference learning has little evidence to work from
- auditability of why a message was or was not surfaced is poor

### 5. Reliability boundaries are underdeveloped

The current code has some good backoff behavior, but it still lacks durable stage separation:

- no ingestion event ledger
- no assistant evaluation queue or outbox
- no dead-letter or replay model per stage
- no explicit delivery contract between this repo and the assistant
- no health isolation between watcher, sync, evaluation trigger, and user-facing delivery

### 6. The current "notification" concept is the wrong domain center

In v2, the center of gravity should be the email and its evaluation lifecycle, not the Telegram notification.

Today the repo effectively treats "new email worth notifying in Telegram" as the canonical unit of work. That should become "new email ingested and available for assistant evaluation."

## v2 Design Principles

- `email-manager` becomes a Gmail watcher and ingestion backend.
- The assistant owns judgment, prioritization, and user-facing delivery.
- Ingestion must be durable even when the assistant is offline.
- Ground context is first-class and externalized to the assistant memory layer.
- Preference learning must remain contextual and revisable, not fixed mute rules.
- Re-evaluation must be supported for future and recent messages.
- Telegram should not remain a required runtime dependency of this repo.

## Proposed v2 Architecture

### Component model

#### 1. Gmail Watch Manager

Responsibility:

- maintain Gmail `watch()` registrations
- keep `last_history_id` and watch expiration healthy
- isolate auth failures per account

Reuse:

- adapt the current `WatchManager`

#### 2. Pub/Sub Intake Worker

Responsibility:

- receive Gmail watch events
- record intake events durably
- schedule history sync without embedding downstream judgment

Change from today:

- Pub/Sub should hand off to an ingestion job boundary, not directly to AI and delivery logic

#### 3. History Sync + Message Normalizer

Responsibility:

- call `users.history.list`
- identify new Gmail message ids
- fetch message metadata and body
- normalize sender, recipients, subject, labels, timestamps, thread ids, snippets, links, and body text
- persist canonical message records

This becomes the new core of the repo.

#### 4. Canonical Email Store

Responsibility:

- preserve the durable source record the assistant can evaluate
- support replay and re-evaluation
- separate ingestion truth from downstream decisions

Recommended storage shape:

- one canonical row per Gmail message
- optional thread table or thread summary table
- immutable-ish ingestion facts plus mutable sync/evaluation status fields

#### 5. Assistant Evaluation Outbox

Responsibility:

- create a durable evaluation request whenever a new message is ingested
- track dispatch attempts, acknowledgements, and retries
- allow replay without re-polling Gmail history

This is the critical missing reliability boundary in the current repo.

#### 6. Assistant Bridge

Responsibility:

- send normalized email events to OpenClaw
- expose idempotency keys
- accept assistant acknowledgements and optional evaluation result callbacks

Possible transport options:

- HTTP webhook from this repo to OpenClaw
- DB-polling contract
- queue/topic consumed by the assistant

Recommendation:

- start with a durable DB outbox plus HTTP delivery worker
- keep the payload schema explicit and versioned

#### 7. Evaluation Ledger

Responsibility:

- record what was asked of the assistant
- record whether the assistant chose `surface`, `ignore`, `digest`, `needs_more_context`, or `error`
- preserve timestamps, attempt counts, and correlation ids

This stores outcomes, not the product brain itself.

#### 8. Re-evaluation Trigger Path

Responsibility:

- requeue messages when the assistant's ground context changes
- support future contextual reversals without static suppressions

Important rule:

- the backend should support re-evaluation
- the assistant should decide when and why re-evaluation is needed

#### 9. Optional Mailbox Action Executor

Responsibility:

- archive, trash, label, or restore emails when the assistant requests it

This can remain in this repo as neutral Gmail capability, but it should no longer be exposed through a repo-owned Telegram UI.

## Proposed Data Flow

### New email ingestion

1. Gmail publishes watch event to Pub/Sub.
2. Intake worker stores a `watch_event` row and schedules or runs account sync.
3. Sync worker calls Gmail history API and extracts new message ids.
4. Each message is fetched and normalized into `email_messages`.
5. A durable `assistant_evaluation_request` or `outbox_event` row is created in the same logical unit of work.
6. A dispatcher sends the event to OpenClaw with an idempotency key.
7. OpenClaw evaluates the message against ground context and learned preferences.
8. OpenClaw decides whether to surface the email in chat, ignore it, batch it, or request more context.
9. The result is written back as an evaluation outcome.

### Re-evaluation

1. Ground context changes in OpenClaw.
2. OpenClaw identifies a reason to revisit messages or future emails matching a concept, sender, thread, product, company, or topic.
3. OpenClaw requests re-evaluation for selected recent messages or registers a new context rule for future events.
4. `email-manager` requeues evaluation requests without re-owning the preference logic itself.

## Proposed State Model

The exact table names can change, but the domain should move toward this shape.

### Keep and evolve

- `gmail_accounts`

Suggested additions:

- `sync_status`
- `last_successful_sync_at`
- `last_watch_error`
- `last_sync_error`

### Add

#### `gmail_watch_events`

Purpose:

- immutable ledger of inbound Pub/Sub notifications

Suggested fields:

- `id`
- `account_id`
- `pubsub_message_id`
- `gmail_history_id`
- `received_at`
- `processed_at`
- `status`
- `error`

#### `email_messages`

Purpose:

- canonical ingested Gmail message records

Suggested fields:

- `id`
- `account_id`
- `gmail_message_id`
- `gmail_thread_id`
- `history_id`
- `message_internal_at`
- `sender_name`
- `sender_email`
- `sender_domain`
- `to_recipients`
- `cc_recipients`
- `subject`
- `snippet`
- `normalized_body_text`
- `labels_json`
- `headers_json`
- `raw_size_bytes`
- `ingested_at`
- `updated_at`

Notes:

- `normalized_body_text` is important if we want reliable assistant-side reasoning and re-evaluation.
- if privacy or storage pressure makes full-body persistence undesirable, define a deliberate retention policy instead of silently discarding it

#### `assistant_evaluation_requests`

Purpose:

- durable evaluation queue and audit trail

Suggested fields:

- `id`
- `email_message_id`
- `trigger_type`
- `trigger_reference`
- `idempotency_key`
- `payload_version`
- `status`
- `attempt_count`
- `queued_at`
- `dispatched_at`
- `acked_at`
- `completed_at`
- `last_error`

#### `assistant_evaluation_results`

Purpose:

- durable record of assistant decisions

Suggested fields:

- `id`
- `request_id`
- `decision`
- `importance`
- `reason_summary`
- `assistant_trace_id`
- `surface_target`
- `created_at`

Example `decision` values:

- `surface_now`
- `ignore`
- `batch_for_later`
- `needs_more_context`
- `failed`

#### `mailbox_actions`

Purpose:

- neutral action log if the assistant later archives, trashes, or labels messages via this backend

### Deprecate over time

- `notifications`
- `suppressions`
- `app_state.telegram_chat_id`
- `usage_daily` as a core product table

`usage_daily` can remain temporarily if there is still any LLM usage during migration, but it should stop being part of the steady-state design once the assistant owns judgment.

## Trigger Model For Real-Time Assistant Evaluation

### Primary trigger

Trigger:

- `email.ingested`

Behavior:

- emitted as soon as the canonical message record is durable
- independent from chat delivery
- idempotent by `(account_id, gmail_message_id, trigger_type)`

### Retry trigger

Trigger:

- `assistant.delivery_retry`

Behavior:

- fired when OpenClaw is unavailable or does not acknowledge within SLA
- does not require re-fetching Gmail unless the canonical record is missing required fields

### Context-change trigger

Trigger:

- `assistant.context_changed`

Behavior:

- originated by the assistant, not by this repo
- requeues recent or affected messages for re-evaluation
- enables contextual reversals that static suppressions cannot model

### User-feedback trigger

Trigger:

- `assistant.feedback_recorded`

Behavior:

- if Enrico says "this kind of email matters" or "don’t show this kind unless it mentions X", the assistant updates its own memory
- the backend may receive a follow-up requeue request for recent related messages

## Ground Context and Preference Learning

This is the most important conceptual shift in v2.

### What should move out of this repo

The following should no longer be owned inside `email-manager`:

- category taxonomy as the main decision scaffold
- sender/category suppression as the main preference system
- direct importance judgment for whether Enrico should be interrupted
- user-visible notification policy

### What should stay in this repo

The backend should provide the assistant with enough evidence to reason well:

- canonical message content
- sender identity
- thread identity
- account identity
- timestamps
- label state
- linkable Gmail target ids
- replayable event history

### Why this matters

Contextual preference learning means the assistant can decide:

- "marketing from this sender is usually low priority"
- but also "messages about model Y are now important"
- therefore "future marketing from this sender mentioning model Y should be reconsidered"

That type of preference cannot be encoded safely as a permanent mute rule in this repo.

## What Stays, Changes, And Gets Deprecated

### Stays

- Gmail watch registration and renewal
- Gmail history sync
- Gmail message fetch and normalization
- Postgres as durable local state
- Dockerized self-hosted deployment pattern
- optional Gmail mailbox mutation helpers

### Changes

- `GmailSyncService` should be split into ingestion and dispatch stages
- config should stop requiring Telegram and OpenAI in the steady state
- the main runtime should no longer block on a Telegram webhook server
- the canonical data model should center on ingested emails and assistant evaluations
- replay and re-evaluation must become first-class behavior

### Deprecated or removed

- `app/openai_client.py`
- `app/telegram_client.py`
- `app/telegram_bot.py`
- digest behavior inside [`app/gmail_sync.py`](/home/enr1c0/project/enrico/email-manager/app/gmail_sync.py:50)
- Telegram-specific config fields in [`app/config.py`](/home/enr1c0/project/enrico/email-manager/app/config.py:31)
- Telegram columns inside `notifications`
- suppression tables as the long-term preference system

## Hidden Complexity And Risks

### 1. Message-level evaluation may be too weak

Many important inbox decisions are thread-aware, not single-message aware. A sender can be noisy overall while a single reply in an ongoing thread becomes important.

Recommendation:

- keep message-level ingestion
- plan for thread-aware assistant evaluation payloads

### 2. Storing too little content will cripple re-evaluation

If the backend stores only metadata and summary, the assistant cannot reliably revisit past decisions when context changes.

Recommendation:

- store normalized body text with an explicit retention and privacy policy

### 3. Storing too much content increases privacy and operational burden

The opposite failure also matters. Full message persistence creates privacy, storage, and compliance consequences.

Recommendation:

- make this an explicit product decision
- choose between full-body retention, encrypted retention, or bounded retention with Gmail refetch fallback

### 4. Delivery semantics between backend and assistant can silently rot

Without a clear contract, it is easy to end up with:

- duplicate evaluations
- lost evaluations
- assistant responses that cannot be matched to a message
- no way to replay after assistant outages

Recommendation:

- define a versioned payload schema
- define idempotency keys
- store request and result ledgers

### 5. Migration can create double-send behavior

If Telegram and assistant delivery run in parallel without careful gating, the user may receive duplicate notifications from multiple surfaces.

Recommendation:

- shadow-evaluate first
- keep only one user-visible surface at a time

### 6. Undo semantics are currently broader than they look

The current `undo not interested` path clears suppression rules matching sender/domain/category, which can remove more than the exact user intent if similar rules already existed.

That is another sign the repo should stop treating static suppression as the durable preference layer.

### 7. The current code lacks clear failure isolation

Today the same flow is responsible for inbox ingestion, reasoning, and notification. That is fragile.

Recommendation:

- isolate failure domains
- ingestion should not depend on assistant uptime
- assistant dispatch should not depend on user-facing chat transport inside this repo

## Recommended Incremental Migration Plan

### Phase 0: Architecture freeze and contract definition

Goal:

- stop adding Telegram-specific features
- define the assistant bridge contract and canonical email payload

Deliverables:

- this design doc
- payload schema for assistant evaluation
- migration checklist and acceptance criteria

### Phase 1: Extract neutral ingestion inside the current app

Goal:

- preserve Gmail watch and sync
- split ingestion from judgment and delivery

Steps:

- introduce canonical `email_messages`
- write normalized messages before any OpenAI or Telegram behavior
- create `assistant_evaluation_requests`
- keep current Telegram flow as a compatibility consumer if needed

Success criteria:

- every new Gmail message is durably ingested even if Telegram or OpenAI fails

### Phase 2: Add assistant bridge and run in shadow mode

Goal:

- let OpenClaw receive evaluation events without yet being the only visible surface

Steps:

- add outbox dispatcher
- deliver `email.ingested` events to the assistant
- record assistant decisions in `assistant_evaluation_results`
- compare assistant decisions with legacy Telegram behavior

Success criteria:

- assistant evaluations are durable, idempotent, and replayable

### Phase 3: Move judgment ownership to the assistant

Goal:

- remove repo-owned classification and suppression from the critical path

Steps:

- stop calling `OpenAIClient` for per-message judgment in `email-manager`
- stop using `suppressions` as the primary gate
- make the assistant the only arbiter of whether to surface Enrico in chat

Success criteria:

- `email-manager` can ingest and queue evaluation requests with no OpenAI dependency

### Phase 4: Remove Telegram as a required product surface

Goal:

- eliminate visible multi-bot behavior

Steps:

- disable Telegram webhook path in normal operation
- retire Telegram config from steady-state deployment
- optionally preserve mailbox action helpers behind an assistant-facing interface only

Success criteria:

- the repo can run without Telegram credentials

### Phase 5: Cleanup and deprecation

Goal:

- simplify the codebase to match the new product truth

Steps:

- remove Telegram modules
- remove digest worker
- remove legacy notification-first schema dependencies
- migrate docs, env templates, and operational runbooks

Success criteria:

- code, schema, docs, and deployment all describe the same assistant-first backend

## Suggested Near-Term Implementation Order

If implementation starts immediately, the safest order is:

1. Add canonical email persistence.
2. Add durable assistant evaluation requests.
3. Add assistant bridge worker.
4. Shadow-run the assistant.
5. Cut off repo-owned OpenAI judgment.
6. Cut off Telegram as the primary UI.
7. Remove legacy product code after stable soak time.

## Recommendation

The repo should not be rewritten from scratch first.

The strongest move is to preserve the Gmail watcher foundation, then refactor around a new domain center:

- from `notification`
- to `ingested email + assistant evaluation lifecycle`

That gives us a path that is:

- more reliable
- closer to the desired product direction
- compatible with assistant-first preference learning
- much easier to harden over time
