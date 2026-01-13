create table if not exists schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);

create table if not exists app_state (
  key text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);

create table if not exists gmail_accounts (
  id bigserial primary key,
  email text not null unique,
  watch_label_ids text[] not null default array['INBOX']::text[],
  last_history_id bigint,
  watch_expiration timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists notifications (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  gmail_message_id text not null,
  gmail_thread_id text,
  history_id bigint,
  sender_email text,
  sender_name text,
  sender_key text,
  subject text,
  summary text,
  category text,
  confidence real,
  status text not null default 'notified',
  telegram_chat_id bigint,
  telegram_message_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  delivered_at timestamptz,
  archived_at timestamptz,
  trashed_at timestamptz,
  unique (account_id, gmail_message_id)
);

create index if not exists notifications_account_status_idx
  on notifications (account_id, status);
create index if not exists notifications_telegram_message_idx
  on notifications (telegram_message_id);
create index if not exists notifications_sender_key_idx
  on notifications (account_id, sender_key);

create table if not exists suppressions (
  id bigserial primary key,
  account_id bigint not null references gmail_accounts(id) on delete cascade,
  sender_key text not null,
  category text not null,
  created_at timestamptz not null default now(),
  unique (account_id, sender_key, category)
);

create table if not exists usage_daily (
  id bigserial primary key,
  account_id bigint references gmail_accounts(id) on delete set null,
  model text not null,
  usage_date date not null,
  input_tokens bigint not null default 0,
  cached_input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  input_cost_usd numeric(12, 6) not null default 0,
  cached_input_cost_usd numeric(12, 6) not null default 0,
  output_cost_usd numeric(12, 6) not null default 0,
  total_cost_usd numeric(12, 6) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (account_id, model, usage_date)
);
