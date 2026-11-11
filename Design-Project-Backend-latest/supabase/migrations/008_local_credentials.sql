-- Move credentials out of Supabase's `auth` schema and into a table we own.
--
-- Why: this service is its own identity provider. It never calls the Supabase
-- API and mints its own tokens, so the original link to `auth.users` bought
-- nothing -- and on a hosted project it actively breaks. There, `auth.users.id`
-- is NOT NULL with no default (GoTrue supplies it) and email uniqueness is a
-- *partial* index, so seeding fails on both the not-null constraint and the
-- ON CONFLICT clause.
--
-- Beyond the immediate error: `auth.users` belongs to GoTrue. Rows we fabricate
-- there are not real users -- no identity records, no confirmation state -- and
-- Supabase migrates that table between releases. Owning our own credential
-- store removes the whole class of problem.
--
-- Safe on an empty database: the constraint drop loses no rows. On a populated
-- one, back-fill `app_credential` from `auth.users.encrypted_password` before
-- applying.

-- `app_user` now identifies its own users, rather than borrowing auth.users.
alter table app_user add column if not exists email text;

do $$
begin
  alter table app_user add constraint app_user_email_key unique (email);
exception when duplicate_table or duplicate_object then null;
end $$;

-- Previously supplied by GoTrue; now ours to generate.
alter table app_user alter column id set default gen_random_uuid();

-- Credentials live apart from application data, so `app_user` still holds no
-- secret and can be selected freely by admin views.
create table if not exists app_credential (
  user_id uuid primary key references app_user(id) on delete cascade,
  password_hash text not null,
  updated_at timestamptz not null default now());

alter table app_credential enable row level security;

-- Drop the foreign key to auth.users, whatever it happens to be named. Guarded
-- so this runs on a database that has no `auth` schema at all.
do $$
declare
  constraint_name text;
begin
  if to_regclass('auth.users') is null then
    return;
  end if;
  select conname into constraint_name
    from pg_constraint
   where conrelid = 'public.app_user'::regclass
     and contype = 'f'
     and confrelid = 'auth.users'::regclass;
  if constraint_name is not null then
    execute format('alter table public.app_user drop constraint %I', constraint_name);
  end if;
end $$;
