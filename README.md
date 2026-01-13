# Email Manager

![Status](https://img.shields.io/badge/status-production-brightgreen)

## Summary
Gmail inboxes receive high volume and frequent checks waste time. This project summarizes new emails with an LLM and delivers them to a Telegram DM with inline action buttons to enable near-real-time triage and reduce notification noise.

## Success Criteria
- Deliver Telegram notifications for relevant emails within seconds (typical <30s).
- Each notification contains sender name, assistant-style AI summary, category, and inline action buttons (Open, Archive, Trash, Not-Interested).
- Enable user-trained suppression (mute by sender_key + category) with idempotent actions and no duplicate active notifications.

## Outcome and Demo
**Outcome:** Production-ready email triage in Telegram with AI summaries, confidence-aware classification, inline mailbox actions, and user-trained suppression to reduce notification flood over time.

**Demo:** Production deployment (self-hosted).

## Tech Stack
- Frontend: Telegram DM frontend (webhook) with inline buttons and message edits.
- Backend: Python service running Pub/Sub pull consumer, Gmail sync, OpenAI Responses API calls, and Telegram handlers (python-telegram-bot webhooks).
- Data: Postgres for durable state, dedupe and preferences; no Redis in the initial scope.
- Infra: Docker Compose stack; Telegram webhook exposed via Cloudflared; Pub/Sub pull subscription.

## Architecture
A Gmail watch publishes mailbox change notices to Cloud Pub/Sub. A pull-based worker consumes events, reads historyId, calls users.history.list to discover new message IDs, and fetches message headers/snippets/bodies for LLM summarization and classification. The AI returns category, confidence, and a short summary; suppression is computed using user preferences (sender_key + category). Non-suppressed messages are pushed to the configured Telegram DM with inline buttons (Open, Archive, Trash with confirm, Not-Interested). User actions are applied to Gmail and the Telegram message is edited to reflect final state. Low-confidence results trigger a category-picker flow and are always pushed to allow manual correction.

## Service Scaffold
- `app/main.py` bootstraps settings, DB, Pub/Sub worker, and the Telegram webhook server.
- `app/telegram_bot.py` registers `/start` and callback handlers; chat ID is stored in `app_state`.
- `app/pubsub_worker.py` subscribes to Pub/Sub and routes Gmail push payloads to the sync service.
- `app/gmail_client.py` and `app/openai_client.py` provide the client scaffolding for Gmail + Responses API.
- `app/gmail_sync.py` handles history sync, message fetch, summarization, and notification send.
- `app/telegram_client.py` sends Telegram messages with inline buttons.
- `app/watch_manager.py` renews Gmail watch registrations.
- `scripts/container_start.py` waits for Postgres, applies migrations, and starts the app in Docker.

## Data Model (SQL)
- `app_state` - key/value store for shared app state (ex: Telegram chat ID).
- `gmail_accounts` - email, watch_label_ids, last_history_id, watch_expiration, timestamps.
- `notifications` - account_id, gmail_message_id, thread_id, sender/subject, summary, category, confidence, telegram_chat_id, telegram_message_id, status, timestamps.
- `suppressions` - account_id, sender_key, category, timestamps.
- `usage_daily` - account_id, model, date, input/cached/output token counts, cost totals.
Defined in `migrations/0001_init.sql`.

## Workflow
- Bootstrap: load `GMAIL_ACCOUNTS_JSON`, ensure each account exists in the DB, and schedule watch renewal.
- Pub/Sub pull: parse historyId + emailAddress, fetch deltas via users.history.list, and dedupe by (account_id, gmail_message_id).
- Fetch + summarize: pull sender, snippet, and body text; compute sender_key; call OpenAI Responses API for summary/category/confidence.
- Suppress + notify: apply suppression rules; send Telegram message with inline actions and store notification record + usage metrics.
- Actions: archive (remove INBOX), trash with confirm, and not-interested suppression; update Telegram message status and reduce buttons to Open-only.
LLM input is capped by token count (default 12k tokens).

## Configuration

### GCP Setup
1) Select or create a GCP project.
2) Enable APIs: Gmail API, Pub/Sub API.
3) OAuth consent screen:
   - User type: External (or Internal if Workspace).
   - Add scope: `https://www.googleapis.com/auth/gmail.modify`.
   - Add your Gmail address as a test user.
4) OAuth client (Web application):
   - Authorized redirect URIs:
     - `http://localhost:8080/` (used by the refresh token helper).
5) Pub/Sub topic:
   - `projects/<project-id>/topics/gmail-watch-topic`
6) Grant Gmail permission to publish:
   - Principal: `gmail-api-push@system.gserviceaccount.com`
   - Role: `Pub/Sub Publisher`
7) Pub/Sub subscription (pull):
   - `projects/<project-id>/subscriptions/gmail-watch-sub`
8) Service account for the app:
   - Role: `Pub/Sub Subscriber`
   - Download JSON key for local use.

### Secrets Layout
Store credentials in `secrets/` (git-ignored).
- `secrets/client_secret.json` (OAuth client)
- `secrets/service_account.json` (service account key)

### OAuth Refresh Token Helper
Install the helper dependencies and run the script:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install google-auth-oauthlib google-auth-httplib2
python3 scripts/get_gmail_refresh_token.py \
  --client-secrets secrets/client_secret.json
```

The script prints a refresh token. Copy it into `.env` and add it to `GMAIL_ACCOUNTS_JSON`.

### Telegram Setup
Set `TELEGRAM_WEBHOOK_BASE_URL` to your Cloudflared base URL (no path). The service appends `/telegram/webhook` when registering the webhook and listens on `APP_HOST`/`APP_PORT` (defaults `0.0.0.0:8080`). The first inbound message from your account (send `/start`) is used to register the chat ID.

### Gmail Accounts
The app supports multiple Gmail accounts and posts them to the same Telegram chat. Run the refresh token helper once per account and add them to `.env` using `GMAIL_ACCOUNTS_JSON`:
```bash
GMAIL_ACCOUNTS_JSON='[{"email":"user1@gmail.com","refresh_token":"replace_me"},{"email":"user2@gmail.com","refresh_token":"replace_me"}]'
```

### Category Enum
- Security - login alerts, suspicious activity, password reset, 2FA
- Finance - bank transactions, invoices, receipts, payment confirmations
- Account - account changes, subscription status, plan upgrades/downgrades
- Work/Action Required - needs your reply/approval, deadlines, tasks, invitations (non-calendar)
- Transactional - order confirmations, shipping, tickets, booking confirmations (non-finance)
- Updates - product updates, release notes, service announcements (not urgent)
- Support - support tickets, customer service replies, case updates
- Marketing - promotions, new product offers, cross-sell
- Newsletter - recurring content digests, blogs, weekly summaries
- Other - fallback bucket

### AI Output Schema
Strict JSON output:
```json
{
  "category": "Security",
  "confidence": 0.92,
  "summary": "Concise assistant summary in up to 4 sentences."
}
```

Rules:
- `category` must match one of the Category Enum values above.
- `confidence` is 0.0 to 1.0.
- `summary` should be concise (max 4 sentences), assistant-style, address the user directly, include important details when they appear in the body, and avoid email metadata unless it appears in the body.
- Low-confidence classifications trigger a manual category picker and are always delivered.

### Telegram Actions
- Open - open Gmail message in a browser.
- Archive - remove INBOX label and update message state in Telegram.
- Trash (confirm) - confirm first, then move to Gmail Trash and update message state.
- Not-Interested - suppress future messages by sender_key + category and update message state.
- Low-confidence picker - manual category selection updates the category/confidence and removes picker buttons.
Message format note: sender name only (no email), no subject line, no confidence shown, and primary buttons are on a single inline row.

## Setup
1) Clone
```bash
git clone https://github.com/alfonsusenrico/enrico-email-manager
cd enrico-email-manager
```

2) Place secrets
```bash
mkdir -p secrets
mv client_secret.json secrets/
mv service_account.json secrets/
```

3) Generate Gmail refresh token
- Run the helper in the Configuration section above for each account and update `GMAIL_ACCOUNTS_JSON`.

4) Configure
```bash
cp .env.example .env && edit .env
```

5) Run (Docker Compose)
```bash
docker compose up --build -d
```

## Environment Variables
| Name | Required | Example | Notes |
|------|----------|---------|-------|
| APP_HOST | No | 0.0.0.0 | Webhook server bind host. |
| APP_PORT | No | 8080 | Webhook server port. |
| GOOGLE_APPLICATION_CREDENTIALS | Yes | secrets/service_account.json | Service account key for Pub/Sub pull. |
| GMAIL_OAUTH_CLIENT_SECRET_JSON | Yes | secrets/client_secret.json | OAuth client JSON. |
| GMAIL_ACCOUNTS_JSON | Yes | `[{"email":"user@gmail.com","refresh_token":"..."}]` | JSON array of Gmail accounts and refresh tokens. |
| GMAIL_WATCH_TOPIC | Yes | projects/<project-id>/topics/gmail-watch-topic | Topic used by users.watch. |
| PUBSUB_SUBSCRIPTION | Yes | projects/<project-id>/subscriptions/gmail-watch-sub | Pull subscription to consume. |
| GMAIL_WATCH_LABEL_IDS | No | INBOX | Label IDs to watch (INBOX only). |
| TELEGRAM_BOT_TOKEN | Yes | TBD | Bot token. |
| TELEGRAM_WEBHOOK_BASE_URL | Yes | https://<cloudflared> | Cloudflared base URL; `/telegram/webhook` is appended by the service. |
| OPENAI_API_KEY | Yes | TBD | OpenAI API key. |
| OPENAI_MODEL | No | gpt-5-mini | Responses API model name. |
| OPENAI_PRICE_INPUT_PER_1M | Yes | 0.25 | USD per 1M input tokens. |
| OPENAI_PRICE_CACHED_INPUT_PER_1M | Yes | 0.025 | USD per 1M cached input tokens. |
| OPENAI_PRICE_OUTPUT_PER_1M | Yes | 2.00 | USD per 1M output tokens. |
| LLM_MAX_INPUT_TOKENS | No | 12000 | Max tokens for email content sent to the LLM. |
| LLM_LOW_CONFIDENCE_THRESHOLD | No | 0.8 | Low-confidence cutoff (still notify). |
| DATABASE_URL | Yes | postgres://postgres:postgres@db:5432/email_manager?sslmode=disable | Postgres connection string. |

## Usage
```bash
# Start services
docker compose up --build -d

# View logs (example)
docker compose logs -f app
```

## Database Migrations
- SQL files live in `migrations/` and are applied in filename order.
- Migrations run automatically on container start.
- Manual apply (optional):
```bash
docker compose run --rm app python3 scripts/apply_migrations.py
```

## Deployment
Deploy as a self-hosted Docker Compose stack. Expose the Telegram webhook endpoint via Cloudflared. Ensure Gmail API credentials and Pub/Sub topic/subscription permissions are provisioned and that the Gmail watch() renewal job runs periodically. Use a Pub/Sub pull subscription to avoid inbound public endpoints for Pub/Sub messages.

## Metrics
Track per-email token usage from the OpenAI Responses API and estimate cost using the pricing variables above. Store or log totals for observability.

## Data Retention
Store minimal metadata only (sender, subject, summary, category/confidence, Gmail + Telegram IDs); no email body is stored. Retain records indefinitely.

## Operations
- Monitor logs and Pub/Sub delivery.
- Rotate Gmail tokens, Telegram bot token, and OpenAI API key as needed.
- Back up Postgres volume for long-term retention.

## License
MIT License - permissive; see https://opensource.org/license/mit/
