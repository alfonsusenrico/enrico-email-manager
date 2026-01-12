# Email Manager

![Status](https://img.shields.io/badge/status-pre--development-blue)

## Summary
Gmail inboxes receive high volume and frequent checks waste time. This project summarizes new emails with an LLM and delivers them to a Telegram DM with inline action buttons to enable near-real-time triage and reduce notification noise.

## Success Criteria
- Deliver Telegram notifications for relevant emails within seconds (typical <30s).
- Each notification contains sender, subject, AI summary, category, confidence, and inline action buttons (Open, Archive, Trash, Not-Interested).
- Enable user-trained suppression (mute by sender_key + category) with idempotent actions and no duplicate active notifications.

## Outcome and Demo
**Outcome:** Near-real-time email triage in Telegram with AI summaries, confidence-aware classification, inline mailbox actions, and user-trained suppression to reduce notification flood over time.

**Demo:** TBD

## Tech Stack
- Frontend: Telegram DM frontend (webhook or long-poll) with inline buttons and message edits.
- Backend: Go (preferred) or Python service running Pub/Sub pull consumer, Gmail sync, OpenAI calls, and Telegram handlers.
- Data: Postgres for durable state, dedupe and preferences; optional Redis for short-term dedupe/rate limits.
- Infra: Docker Compose stack; Telegram webhook and health endpoints exposed via Cloudflared tunnel; Pub/Sub pull subscription.

## Architecture
A Gmail watch publishes mailbox change notices to Cloud Pub/Sub. A pull-based worker consumes events, reads historyId, calls users.history.list to discover new message IDs, and fetches message headers/snippets/bodies for LLM summarization and classification. The AI returns category, confidence, and a short summary; suppression is computed using user preferences (sender_key + category). Non-suppressed messages are pushed to the configured Telegram DM with inline buttons (Open, Archive, Trash with confirm, Not-Interested). User actions are applied to Gmail and the Telegram message is edited to reflect final state. Low-confidence results trigger a category-picker flow and are always pushed to allow manual correction.

## Setup
1) Clone
```bash
git clone https://github.com/alfonsusenrico/enrico-email-manager
cd enrico-email-manager
```

2) Install
```bash
docker-compose pull && docker-compose build
```

3) Configure
```bash
cp .env.example .env && edit .env
```

4) Run
```bash
docker-compose up --build -d
```

## Environment Variables
| Name | Required | Example | Notes |
|------|----------|---------|-------|
| GMAIL_OAUTH_CLIENT_ID | Yes | TBD | Gmail OAuth client ID for API access and watch() authorization. |
| TELEGRAM_BOT_TOKEN | Yes | TBD | Bot token used to send messages and receive callback updates via webhook or long-poll. |

## Usage
```bash
# Start services
docker-compose up --build -d

# View logs (example)
docker-compose logs -f app
```

## Deployment
Deploy as a self-hosted Docker Compose stack. Expose the Telegram webhook endpoint via a tunnel (Cloudflared or equivalent) or use long-polling. Ensure Gmail API credentials and Pub/Sub topic/subscription permissions are provisioned and that the Gmail watch() renewal job runs periodically. Use a Pub/Sub pull subscription to avoid inbound public endpoints for Pub/Sub messages.

## Roadmap / Next Steps
- Freeze Category Enum v1 (10 categories) and publish to schema.
- Define AI output schema (strict JSON): category, confidence, summary.
- Implement sender_key normalization, suppression store (user_id, sender_key, category) => not_interested, and Telegram Set Category picker for low-confidence cases.

## License
MIT License — permissive; see https://opensource.org/license/mit/