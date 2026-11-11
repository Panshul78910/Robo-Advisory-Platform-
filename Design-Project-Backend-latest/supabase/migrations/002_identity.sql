create table app_user (
  id uuid primary key references auth.users(id) on delete cascade,
  role user_role not null,
  display_name text not null,
  created_at timestamptz not null default now());

create table client (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid unique not null references app_user(id) on delete cascade,
  client_code text unique not null,
  created_at timestamptz not null default now());

-- Profile fields are nullable on purpose: an incomplete profile must be
-- representable so the A.2 profiling conversation can ask for what is missing.
create table client_profile (
  client_id uuid primary key references client(id) on delete cascade,
  full_name text not null,
  date_of_birth date,
  email text,
  monthly_salary numeric(14,2),
  monthly_commitments numeric(14,2),
  dependants int,
  investment_objective investment_obj,
  investment_horizon_yrs int,
  loss_tolerance loss_tolerance,
  liquidity_needs liquidity_needs,
  income_stability income_stability,
  investment_experience investment_exp,
  cka_passed boolean not null default false,
  cka_passed_at timestamptz,
  updated_at timestamptz not null default now());

create table client_profile_history (
  id bigserial primary key,
  client_id uuid not null references client(id) on delete cascade,
  snapshot jsonb not null,
  changed_at timestamptz not null default now(),
  changed_by uuid references app_user(id));

create table client_document (
  id uuid primary key default uuid_generate_v4(),
  client_id uuid not null references client(id) on delete cascade,
  storage_path text not null,
  filename text not null,
  mime_type text,
  size_bytes bigint,
  uploaded_at timestamptz not null default now());
