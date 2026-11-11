create sequence decision_display_seq start 1001;

-- `confidence` is nullable. NULL means abstained. Never store 0 to mean abstained.
create table decision (
  id uuid primary key default uuid_generate_v4(),
  display_no text not null default ('D-' || nextval('decision_display_seq')),
  client_id uuid references client(id) on delete cascade,
  decision_type decision_type not null,
  status decision_status not null,
  input text,
  output text,
  confidence numeric(5,4),
  abstained boolean not null default false,
  refused boolean not null default false,
  model_version text,
  prompt_version text,
  policy_version text,
  session_date date,
  correlation_id text,
  created_at timestamptz not null default now());
create index on decision (client_id, created_at desc);

-- passage_text is copied, not referenced: a citation shown in week 8 must still
-- expand to the text the client actually saw after a week 10 re-ingest (C.2/C.6).
create table decision_source (
  id bigserial primary key,
  decision_id uuid not null references decision(id) on delete cascade,
  chunk_ref text not null,
  doc_id text not null,
  title text,
  section text,
  page int,
  score numeric(8,5),
  passage_text text not null);

create table decision_override (
  id uuid primary key default uuid_generate_v4(),
  decision_id uuid unique not null references decision(id) on delete cascade,
  reviewer_user_id uuid not null references app_user(id),
  action text not null check (action in ('approve','edit','reject')),
  reason text,
  pre_override_output text not null,
  final_output text not null,
  reviewed_at timestamptz not null default now());

create table escalation (
  id uuid primary key default uuid_generate_v4(),
  decision_id uuid not null references decision(id) on delete cascade,
  client_id uuid not null references client(id),
  reason_code text not null,
  status text not null default 'pending',
  created_at timestamptz not null default now(),
  resolved_at timestamptz);
