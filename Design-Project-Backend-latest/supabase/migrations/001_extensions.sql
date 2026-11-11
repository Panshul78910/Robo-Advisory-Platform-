create extension if not exists "uuid-ossp";
create extension if not exists vector;
create extension if not exists pg_trgm;

create type user_role as enum ('client','admin');
create type loss_tolerance   as enum ('zero','low','medium','high');
create type liquidity_needs  as enum ('low','medium','high');
create type income_stability as enum ('unstable','variable','stable');
create type investment_exp   as enum ('none','limited','moderate','extensive');
create type investment_obj   as enum ('preservation','income','balanced','growth','aggressive_growth');
create type asset_class      as enum ('cash','bonds','equities','alternatives');
create type risk_category    as enum (
  'capital_preservation','conservative','moderately_conservative','balanced',
  'moderately_aggressive','growth','aggressive','very_aggressive');
create type portfolio_event_type as enum ('invest','rebalance','revert','switch');
create type decision_type as enum (
  'risk_assessment','suitability_gate','recommendation','rebalance','revert','chat_qa');
create type decision_status as enum (
  'completed','auto_executed','pending_review','abstained',
  'approved','approved_edited','overridden','refused');
