alter table if exists notifications
  add column if not exists importance text not null default 'medium',
  add column if not exists digest_group_id text,
  add column if not exists digested_at timestamptz;

-- suppression v2: scope/rule_value/category_key
alter table if exists suppressions
  add column if not exists scope text not null default 'sender_category',
  add column if not exists rule_value text,
  add column if not exists category_key text not null default '';

update suppressions
   set rule_value = coalesce(rule_value, sender_key),
       category_key = case when category is null then '' else category end
 where rule_value is null or category_key is null;

alter table if exists suppressions
  alter column rule_value set not null,
  alter column category_key set not null;

create unique index if not exists suppressions_rule_unique_idx
  on suppressions(account_id, scope, rule_value, category_key);

create index if not exists suppressions_lookup_idx
  on suppressions(account_id, scope, rule_value, category_key);

create index if not exists notifications_digest_queue_idx
  on notifications(status, created_at)
  where status = 'digest_queued';
