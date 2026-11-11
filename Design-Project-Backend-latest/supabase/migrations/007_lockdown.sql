-- Invariant 9: RLS on with no policies is deny-all for anon/authenticated.
-- The service role (Supabase) and the table owner (local) bypass it. This is a
-- fail-closed backstop; the primary control is principal scoping in the
-- repository layer (app/db/repositories/base.py).
do $$
declare t record;
begin
  for t in
    select tablename from pg_tables
    where schemaname = 'public'
  loop
    execute format('alter table public.%I enable row level security', t.tablename);
  end loop;
end $$;

-- Invariant 2: corrections to decision and position data are new rows, never
-- edits. `revoke` covers non-owner roles; the trigger covers everyone, including
-- a careless service-role query or a psql session.
create or replace function reject_mutation() returns trigger
language plpgsql as $$
begin
  raise exception
    'table % is append-only; record a correcting row instead of %', TG_TABLE_NAME, TG_OP
    using errcode = 'restrict_violation';
end $$;

do $$
declare t text;
begin
  foreach t in array array[
    'decision','decision_source','portfolio_event','trade',
    'holdings_snapshot','holdings_snapshot_line','risk_assessment'
  ] loop
    execute format('revoke update, delete on public.%I from public', t);
    execute format(
      'create trigger %I before update or delete on public.%I
         for each statement execute function reject_mutation()',
      t || '_append_only', t);
  end loop;
end $$;
