-- Portability shim.
--
-- On a Supabase stack the `auth` schema and `auth.users` already exist and this
-- file is a no-op. On a plain Postgres (docker-compose.yml) it creates the
-- minimal subset the rest of the migrations reference, so one set of migrations
-- runs unchanged in both environments.
--
-- SUPERSEDED BY 008_local_credentials.sql, and kept only so databases created
-- before it still migrate cleanly. Credentials now live in `app_credential`,
-- a table this project owns; nothing here writes to `auth` any more.

create schema if not exists auth;

create table if not exists auth.users (
  id                 uuid primary key default gen_random_uuid(),
  email              text unique,
  encrypted_password text,
  created_at         timestamptz not null default now()
);
