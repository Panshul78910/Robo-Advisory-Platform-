create table app_session (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references app_user(id) on delete cascade,
  session_date date not null,
  created_at timestamptz not null default now());

-- Conversation state is checkpointed here rather than held in process memory:
-- a backend restart mid-demo must not lose a paused review (B.7).
create table conversation (
  id uuid primary key default uuid_generate_v4(),
  client_id uuid not null references client(id) on delete cascade,
  kind text not null check (kind in ('profiling','advisory')),
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now());

create table message (
  id uuid primary key default uuid_generate_v4(),
  conversation_id uuid not null references conversation(id) on delete cascade,
  seq int not null,
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  decision_id uuid references decision(id),
  created_at timestamptz not null default now(),
  unique (conversation_id, seq));

create table corpus_version (
  id uuid primary key default uuid_generate_v4(),
  label text unique not null,
  note text,
  created_at timestamptz not null default now());

create table document (
  id uuid primary key default uuid_generate_v4(),
  corpus_version_id uuid not null references corpus_version(id) on delete cascade,
  doc_id text not null,
  title text not null,
  source_path text not null,
  audience text not null default 'admin' check (audience in ('client','admin')),
  unique (corpus_version_id, doc_id));

create table chunk (
  id uuid primary key default uuid_generate_v4(),
  document_id uuid not null references document(id) on delete cascade,
  chunk_ref text not null,
  ordinal int not null,
  heading text,
  section text,
  page int,
  text text not null,
  token_count int,
  audience text not null default 'admin',
  embedding vector(384),
  tsv tsvector generated always as (to_tsvector('english', text)) stored);
create index on chunk using hnsw (embedding vector_cosine_ops);
create index on chunk using gin (tsv);
create index on chunk (document_id, ordinal);

create table llm_call (
  id uuid primary key default uuid_generate_v4(),
  correlation_id text not null,
  decision_id uuid references decision(id),
  attempt int not null default 1,
  payload jsonb not null,
  block_tokens jsonb not null,
  total_tokens int,
  context_limit int,
  response_text text,
  status text not null,
  latency_ms int,
  created_at timestamptz not null default now());
create index on llm_call (created_at desc);
