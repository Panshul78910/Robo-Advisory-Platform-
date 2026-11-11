create table portfolio_event (
  id uuid primary key default uuid_generate_v4(),
  client_id uuid not null references client(id) on delete cascade,
  seq bigint not null,
  event_type portfolio_event_type not null,
  session_date date not null,
  portfolio_id uuid references model_portfolio(id),
  reverts_event_id uuid references portfolio_event(id),
  decision_id uuid,
  created_at timestamptz not null default now(),
  unique (client_id, seq));

create table trade (
  id uuid primary key default uuid_generate_v4(),
  event_id uuid not null references portfolio_event(id) on delete cascade,
  instrument_id uuid not null references instrument(id),
  side text not null check (side in ('buy','sell')),
  units numeric(20,6) not null,
  price numeric(18,6) not null,
  gross_amount numeric(18,2) not null);

create table holdings_snapshot (
  id uuid primary key default uuid_generate_v4(),
  event_id uuid unique not null references portfolio_event(id) on delete cascade,
  client_id uuid not null references client(id) on delete cascade,
  seq bigint not null,
  cash numeric(18,2) not null,
  created_at timestamptz not null default now(),
  unique (client_id, seq));

create table holdings_snapshot_line (
  snapshot_id uuid not null references holdings_snapshot(id) on delete cascade,
  instrument_id uuid not null references instrument(id),
  units numeric(20,6) not null,
  primary key (snapshot_id, instrument_id));

create view current_holdings as
select distinct on (s.client_id) s.*
from holdings_snapshot s order by s.client_id, s.seq desc;

create table idempotency_key (
  key text primary key,
  client_id uuid not null references client(id),
  endpoint text not null,
  request_hash text not null,
  response_json jsonb,
  created_at timestamptz not null default now());
