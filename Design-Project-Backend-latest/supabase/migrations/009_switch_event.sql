-- Forward migration for databases that already applied 001_extensions.sql
-- before `switch` was added to portfolio_event_type.
--
-- `make reset` recreates the type from 001 already containing `switch`;
-- IF NOT EXISTS keeps this a no-op in that case. PostgreSQL 12+ allows
-- ADD VALUE inside a transaction, which is how scripts/migrate.py runs.
alter type portfolio_event_type add value if not exists 'switch';
