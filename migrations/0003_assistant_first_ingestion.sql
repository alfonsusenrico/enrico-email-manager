alter table if exists gmail_accounts
  add column if not exists sync_status text not null default 'idle',
  add column if not exists last_successful_sync_at timestamptz,
  add column if not exists last_sync_error text,
  add column if not exists last_watch_error text;

create table if not exists gmail_watch_events (
  id bigserial primary key,
  account_id bigint references gmail_accounts(id) on delete set null,
  email_address text not null,
  pubsub_message_id text,
  gmail_history_id bigint not null,
  status text not null default 'received',
  error text,
  received_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists gmail_watch_events_status_idx
  on gmail_watch_events (status, received_at);

create table if not exists gmail_sync_runs (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  watch_event_id bigint references gmail_watch_events(id) on delete set null,
  start_history_id bigint,
  end_history_id bigint,
  discovered_message_count integer not null default 0,
  queued_message_count integer not null default 0,
  status text not null default 'running',
  error text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

create index if not exists gmail_sync_runs_account_started_idx
  on gmail_sync_runs (account_id, started_at desc);

create table if not exists gmail_message_ingest_jobs (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  watch_event_id bigint references gmail_watch_events(id) on delete set null,
  sync_run_id bigint references gmail_sync_runs(id) on delete set null,
  gmail_message_id text not null,
  history_id bigint,
  status text not null default 'queued',
  attempt_count integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  last_error_type text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (account_id, gmail_message_id)
);

create index if not exists gmail_message_ingest_jobs_queue_idx
  on gmail_message_ingest_jobs (status, next_attempt_at, created_at);

create table if not exists gmail_message_failures (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  gmail_message_id text not null,
  ingest_job_id bigint references gmail_message_ingest_jobs(id) on delete set null,
  sync_run_id bigint references gmail_sync_runs(id) on delete set null,
  watch_event_id bigint references gmail_watch_events(id) on delete set null,
  stage text not null,
  error_type text not null,
  error text not null,
  created_at timestamptz not null default now()
);

create index if not exists gmail_message_failures_lookup_idx
  on gmail_message_failures (account_id, gmail_message_id, created_at desc);

create table if not exists email_messages (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  gmail_message_id text not null,
  gmail_thread_id text not null default '',
  history_id bigint,
  message_internal_at timestamptz,
  sender_name text,
  sender_email text,
  sender_domain text,
  to_recipients jsonb not null default '[]'::jsonb,
  cc_recipients jsonb not null default '[]'::jsonb,
  subject text,
  snippet text,
  normalized_body_text text,
  labels_json jsonb not null default '[]'::jsonb,
  headers_json jsonb not null default '{}'::jsonb,
  raw_size_bytes bigint,
  ingest_status text not null default 'ingested',
  ingested_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (account_id, gmail_message_id)
);

create index if not exists email_messages_account_thread_idx
  on email_messages (account_id, gmail_thread_id, message_internal_at desc);

create table if not exists assistant_evaluation_requests (
  id bigserial primary key,
  email_message_id bigint not null references email_messages(id) on delete cascade,
  trigger_type text not null,
  trigger_reference text,
  idempotency_key text not null unique,
  payload_version text not null default 'v1',
  status text not null default 'queued',
  attempt_count integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  queued_at timestamptz not null default now(),
  dispatched_at timestamptz,
  acked_at timestamptz,
  completed_at timestamptz,
  last_http_status integer,
  last_error text,
  last_error_at timestamptz,
  updated_at timestamptz not null default now()
);

create index if not exists assistant_evaluation_requests_queue_idx
  on assistant_evaluation_requests (status, next_attempt_at, queued_at);

create table if not exists assistant_evaluation_results (
  id bigserial primary key,
  request_id bigint not null references assistant_evaluation_requests(id) on delete cascade,
  decision text not null,
  importance text,
  reason_summary text,
  surface_target text,
  assistant_trace_id text,
  raw_response_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (request_id)
);
