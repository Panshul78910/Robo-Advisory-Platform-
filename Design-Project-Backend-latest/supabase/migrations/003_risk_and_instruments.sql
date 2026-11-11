create table risk_assessment (
  id uuid primary key default uuid_generate_v4(),
  client_id uuid not null references client(id) on delete cascade,
  score numeric(10,4) not null,
  category risk_category not null,
  inputs_snapshot jsonb not null,
  formula_version text not null,
  conversation_id uuid,
  created_at timestamptz not null default now());
create index on risk_assessment (client_id, created_at desc);

create table instrument (
  id uuid primary key default uuid_generate_v4(),
  symbol text unique not null,
  name text not null,
  asset_class asset_class not null,
  is_satellite boolean not null default false);

create table model_portfolio (
  id uuid primary key default uuid_generate_v4(),
  code text unique not null,
  name text not null,
  risk_category risk_category not null,
  requires_cka boolean not null default false);

create table model_portfolio_target (
  portfolio_id uuid not null references model_portfolio(id) on delete cascade,
  instrument_id uuid not null references instrument(id),
  target_weight numeric(7,4) not null,
  primary key (portfolio_id, instrument_id));

create table instrument_price (
  instrument_id uuid not null references instrument(id),
  price_date date not null,
  price numeric(18,6) not null,
  primary key (instrument_id, price_date));
