# Project Proposal — Email Manager

**Client/Owner:** Enrico  
**Prepared by:** Alfonsus Enrico  
**Date:** 2026-01-12  
**Version:** v0.1

---

## 1) Summary
**One-liner:** A self-hosted Gmail listener that summarizes new emails with an LLM and delivers them to a Telegram DM with inline action buttons (Open, Archive, Trash, Not-Interested) to manage inbox without opening Gmail.  
**Outcome:** Near-real-time email triage in Telegram, with user-trained suppression (mute by sender + category) to reduce notification flood over time.

## 2) Problem & Context
The Gmail inbox receives high volume; frequent checks waste time. The solution provides immediate understanding (short AI summary), quick mailbox actions from Telegram, and progressive noise reduction through user-trained suppression without complex digest or quiet-hour systems.

## 3) Goals (Success Criteria)
- Deliver Telegram notifications for relevant emails within seconds (Pub/Sub + processing latency).
- Each notification contains sender, subject, AI summary, category, confidence, and inline action buttons.
- Enable user-trained suppression (sender_key + category) with idempotent actions and production-grade reliability.

## 4) Constraints
- Gmail API must be enabled; Gmail push notifications require Pub/Sub topic permissions granted to Gmail’s publisher principal.  
- Gmail watch() expires and must be renewed periodically; push payload lacks full message and requires historyId + users.history.list to fetch changes.

## 5) Proposed Solution (How It Works)
A Gmail Watch publishes mailbox change notices to Cloud Pub/Sub. A pull-based worker consumes events, reads historyId, calls users.history.list to discover new message IDs, and fetches message headers/snippets/bodies for LLM summarization and classification. The AI returns category, confidence, and a short summary; importance and suppression are computed using user preferences (sender_key + category). Non-suppressed messages are pushed to the configured Telegram DM with inline buttons (Open, Archive, Trash with confirm, Not-Interested). User actions are applied to Gmail and the Telegram message is edited to reflect final state. Low-confidence results trigger a category-picker flow and are always pushed to allow manual correction.

## 5.1) Implementation Overview (Stack)
- Frontend: Telegram DM frontend (webhook or long-poll) with inline buttons and message edits.  
- Backend: Go (preferred) or Python service running Pub/Sub pull consumer, Gmail sync, OpenAI calls, and Telegram handlers.  
- Data: Postgres for durable state, dedupe and preferences; optional Redis for short-term dedupe/rate limits.  
- Deployment: Docker Compose stack; Telegram webhook and health endpoints exposed via Cloudflared tunnel; Pub/Sub pull subscription (no inbound required).

## 6) Scope
### In Scope
- Gmail watch + periodic renewal and incremental sync via historyId.  
- AI summarization, categorization, confidence scoring, and suppression logic.  
- Telegram notifications with inline actions (Open/Archive/Trash/Not-Interested) and 2-step trash confirmation.

### Out of Scope
- AI drafting/sending replies.  
- Digest mode, quiet hours, attachment processing/OCR.

**Change control:** Requests outside the approved scope may affect timeline and cost.

## 7) Deliverables
- docker-compose.yml, .env.example, and README setup guide.  
- App service implementing Gmail OAuth/watch, Pub/Sub pull consumer, history-based message discovery, OpenAI summarization/classification, and Telegram bot with callbacks.  
- Minimal DB schema/migrations, preference storage + suppression logic, logs and basic metrics counters.

## 8) Acceptance Criteria (Definition of Done)
- Latency: New relevant email → Telegram notification within target window (typical <30s).  
- No duplicates and idempotent actions: Same Gmail message does not create multiple active notifications; repeated callbacks/retries are safe.  
- Suppression and safety: Mute by (sender_key, category) prevents future pushes (except low-confidence); Trash requires explicit confirm and final state is reflected in message edits.

## 9) Timeline
| Phase | What you get | Target date |
|------:|--------------|-------------|
| Discovery | Running + testable core: Gmail OAuth, Pub/Sub consumer, store lastHistoryId | Day 1–3 |
| MVP | History sync, message fetch, DB persistence, Telegram send, OpenAI summarize, inline actions (Archive/Trash confirm) | Day 3 |
| Beta | Preference suppression, sender_key normalization, low-confidence category picker, idempotency hardening | Day 4–5 |
| Production | Watch renewal job, deployment hardening, docs and test checklist | Day 6 |

## 10) Cost Estimate
**Build (one-time):** TBD  
**Monthly ops:** TBD

**Assumptions:**
- OpenAI cost is the primary variable (token usage per email); short summaries and input caps will limit expense.  
- Pub/Sub and self-hosting are expected to be low-cost for personal volumes; hosting via Docker Compose on user server.

## 11) Risks & Dependencies
**Risks:**
- History gaps / invalid startHistoryId → recover by detecting 404, resetting baseline to latest historyId and continuing.  
- Misclassification → mitigate with low-confidence rule (always push) and manual category set flow.

**Dependencies:**
- Gmail API + Pub/Sub topic/subscription permissions and OAuth credentials.  
- Telegram webhook reliability and Cloudflared tunnel stability for endpoint exposure.

## 12) Ownership & Access
- Source code ownership: Alfonsus Enrico  
- Access needed (keys/repos/servers): Gmail OAuth client ID/secret and refresh token, Pub/Sub topic/subscription IAM, Telegram bot token, DB credentials.  
- Handoff (walkthrough/docs/deploy): README, deployment guide, test checklist and walkthrough session.

## 13) Optional (Include only if applicable)
### Support & Maintenance
- Offer minimal maintenance: monitor token/credential expiry, update category enum, and apply small fixes; support engagement as separate agreement.

### Security & Privacy
- Send minimal email content to the LLM (strip long footers; avoid attachments). Store only summary, classification, and message identifiers; protect OAuth refresh token and bot token.

## 14) Next Steps
- Freeze Category Enum v1 (10 categories) and publish to schema.  
- Define AI output schema (strict JSON): category, confidence, summary.  
- Implement sender_key normalization, suppression store (user_id, sender_key, category) => not_interested, and Telegram Set Category picker for low-confidence cases.