# Email Manager

Assistant-first Gmail ingestion backend for OpenClaw.

This project is no longer a Telegram triage bot. It now exists to:

- maintain Gmail `watch()` registrations
- consume Gmail Pub/Sub events
- durably queue and fetch new Gmail messages
- normalize canonical email records into Postgres
- dispatch evaluation requests to OpenClaw in near-realtime
- accept evaluation results and re-evaluation requests back from OpenClaw

The product brain now lives in OpenClaw. `email-manager` is the durable watcher, ingestion, and bridge layer.

## Current Flow

1. Gmail publishes mailbox changes to Pub/Sub.
2. `email-manager` records the inbound watch event in `gmail_watch_events`.
3. A history sync discovers Gmail message IDs and enqueues durable ingest jobs in `gmail_message_ingest_jobs`.
4. Background ingest workers fetch Gmail message content and upsert canonical rows in `email_messages`.
5. Each newly ingested email creates an `assistant_evaluation_requests` outbox row.
6. The assistant bridge worker POSTs the normalized payload to OpenClaw.
7. OpenClaw either:
   - acknowledges the event and later POSTs the evaluation result back, or
   - returns an inline evaluation result immediately.
8. `email-manager` stores durable outcomes in `assistant_evaluation_results`.

This separation is the key hardening change:

- Pub/Sub sync no longer performs message fetch, AI judgment, and user delivery inline.
- One broken Gmail message no longer poison-loops the entire watch event.
- Canonical email state survives assistant outages.

## Key Runtime Components

- [app/watch_manager.py](app/watch_manager.py): Renews Gmail `watch()` registrations and records watch errors per account.
- [app/pubsub_worker.py](app/pubsub_worker.py): Consumes Pub/Sub events and hands them to the sync service with explicit `ack` / `nack` behavior.
- [app/gmail_sync.py](app/gmail_sync.py): Runs history sync, durable ingest queue processing, and assistant dispatch.
- [app/gmail_client.py](app/gmail_client.py): Gmail API client plus message normalization and Gmail error classification.
- [app/assistant_bridge.py](app/assistant_bridge.py): Outbound HTTP bridge from `assistant_evaluation_requests` to OpenClaw.
- [app/assistant_api.py](app/assistant_api.py): Inbound HTTP API for OpenClaw result callbacks and requeue requests.
- [app/db.py](app/db.py): Postgres persistence for watch events, sync runs, ingest jobs, canonical emails, and assistant ledgers.

## Data Model

The assistant-first schema is introduced in [migrations/0003_assistant_first_ingestion.sql](migrations/0003_assistant_first_ingestion.sql).

Core tables:

- `gmail_watch_events`: immutable ledger of inbound Pub/Sub notices
- `gmail_sync_runs`: durable sync attempts per account/watch event
- `gmail_message_ingest_jobs`: retryable queue for fetching Gmail messages outside the Pub/Sub hot path
- `gmail_message_failures`: failure ledger for per-message ingest issues
- `email_messages`: canonical normalized Gmail messages
- `assistant_evaluation_requests`: assistant delivery outbox with idempotency and retry state
- `assistant_evaluation_results`: durable assistant decisions

Legacy tables from the Telegram era are left in place for migration safety, but they are no longer part of the active runtime path.

## Hardening Changes

The current runtime now hardens the system in a few important ways:

- Per-message failures are isolated behind `gmail_message_ingest_jobs`.
- Gmail `messages.get()` `404` becomes a durable skipped message, not a Pub/Sub poison loop.
- Retryable Gmail fetch failures are retried from Postgres instead of re-running the whole watch event.
- Invalid Gmail history cursors are recorded as `history_recovered_with_gap` instead of silently failing.
- Assistant delivery is durable and replayable because it is driven by `assistant_evaluation_requests`, not inline during ingestion.
- Stale `processing` / `dispatching` rows can be re-leased after worker interruption.

## OpenClaw Integration

OpenClaw should integrate with this project through the assistant bridge contract, not by listening directly to Pub/Sub.

Read [docs/openclaw-integration.md](docs/openclaw-integration.md) for:

- outbound event payloads
- idempotency behavior
- callback URLs
- evaluation result format
- requeue contract for context changes

Read [docs/openclaw-implementation-brief.md](docs/openclaw-implementation-brief.md) for:

- the concrete OpenClaw-side build brief
- the adapter requirements
- acceptance criteria before enabling live dispatch

The design rationale and migration framing are documented in [docs/assistant-first-v2.md](docs/assistant-first-v2.md).

## Configuration

### Required Environment Variables

| Name | Example | Purpose |
|------|---------|---------|
| `APP_HOST` | `0.0.0.0` | Bind host for the local assistant callback API |
| `APP_PORT` | `8080` | Bind port for the local assistant callback API |
| `PUBLIC_BASE_URL` | `https://email-manager.example` | Public base URL that OpenClaw can call back into. Can stay empty while dispatch is disabled. |
| `GMAIL_WATCH_TOPIC` | `projects/<project-id>/topics/gmail-watch-topic` | Gmail watch topic |
| `PUBSUB_SUBSCRIPTION` | `projects/<project-id>/subscriptions/gmail-watch-sub` | Pull subscription consumed by the worker |
| `GMAIL_WATCH_LABEL_IDS` | `INBOX` | Comma-separated Gmail label IDs to watch |
| `GOOGLE_APPLICATION_CREDENTIALS` | `secrets/service_account.json` | Service account credentials for Pub/Sub |
| `GMAIL_OAUTH_CLIENT_SECRET_JSON` | `secrets/client_secret.json` | Gmail OAuth client JSON |
| `GMAIL_ACCOUNTS_JSON` | `[{"email":"user@gmail.com","refresh_token":"..."}]` | Gmail accounts and refresh tokens |
| `ASSISTANT_DISPATCH_ENABLED` | `false` | When `false`, keep ingesting and queueing emails without attempting outbound assistant delivery |
| `ASSISTANT_BRIDGE_URL` | `https://openclaw.example/internal/email-manager/events` | OpenClaw ingest endpoint. Required only when `ASSISTANT_DISPATCH_ENABLED=true` |
| `ASSISTANT_SHARED_SECRET` | `replace_me_shared_secret` | Shared bearer token for outbound and inbound bridge auth |
| `ASSISTANT_DISPATCH_TIMEOUT_SECONDS` | `15` | HTTP timeout for bridge delivery |
| `ASSISTANT_DISPATCH_BATCH_SIZE` | `10` | Max queued evaluation requests leased per dispatch poll |
| `ASSISTANT_DISPATCH_POLL_SECONDS` | `3` | Sleep interval when no evaluation requests are pending |
| `ASSISTANT_DISPATCH_MAX_ATTEMPTS` | `20` | Max delivery attempts before marking a request failed |
| `ASSISTANT_MAX_EMAIL_AGE_SECONDS` | `86400` | Freshness gate for auto-surfacing newly ingested emails. Older backlog emails are still stored canonically, but they are not auto-queued as real-time assistant interruptions. |
| `ASSISTANT_MIN_MESSAGE_INTERNAL_AT` | empty | Optional hard cutoff. If set, only emails with Gmail `internalDate` at or after this ISO timestamp are auto-queued for assistant evaluation. Older emails are still stored, but not surfaced. |
| `INGEST_WORKER_BATCH_SIZE` | `25` | Max Gmail ingest jobs leased per poll |
| `INGEST_WORKER_POLL_SECONDS` | `2` | Sleep interval when no ingest jobs are pending |
| `INGEST_WORKER_MAX_ATTEMPTS` | `20` | Max Gmail fetch/persist attempts before marking a job failed |
| `DATABASE_URL` | `postgres://postgres:postgres@db:5432/email_manager?sslmode=disable` | Postgres connection string |

See [.env.example](.env.example) for a full example.

## Setup

1. Put Google credentials under `secrets/`.
2. Fill `.env` from `.env.example`.
3. Configure Gmail watch / Pub/Sub infrastructure.
4. If you already have an OpenClaw bridge endpoint, set `ASSISTANT_DISPATCH_ENABLED=true` and configure the contract in [docs/openclaw-integration.md](docs/openclaw-integration.md).
5. Start the stack:

```bash
docker compose up --build -d
```

## Local Endpoints

- `GET /healthz`: local health endpoint
- `POST /assistant/evaluations`: OpenClaw posts completed evaluation results here
- `POST /assistant/requeue`: OpenClaw requests contextual re-evaluation here

If `ASSISTANT_SHARED_SECRET` is set, callbacks must use:

```text
Authorization: Bearer <ASSISTANT_SHARED_SECRET>
```

## Bridge Readiness Modes

- `ASSISTANT_DISPATCH_ENABLED=false`: safe bootstrap mode. Gmail watch, Pub/Sub sync, canonical email storage, and assistant request queueing stay active, but no outbound dispatch is attempted yet.
- `ASSISTANT_MAX_EMAIL_AGE_SECONDS`: protects against historical backlog being surfaced as if it were a brand-new email. Older messages still land in `email_messages`, but they do not create real-time assistant interruptions during ingest.
- `ASSISTANT_MIN_MESSAGE_INTERNAL_AT`: optional stronger cutoff for rollout moments like this one. Set it to “now” when you want a clean break so only genuinely new mail after that point is surfaced.
- `ASSISTANT_DISPATCH_ENABLED=true`: full bridge mode. `email-manager` POSTs queued evaluation requests to OpenClaw and expects acknowledgement or callbacks.

## Running Migrations

Migrations run automatically on container start.

Manual apply:

```bash
docker compose run --rm app python3 scripts/apply_migrations.py
```

## Verification

Lightweight regression tests:

```bash
python3 -m unittest discover -s tests
```

Syntax check:

```bash
python3 -m compileall app tests
```
