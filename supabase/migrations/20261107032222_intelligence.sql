-- Comprehensive Utah intelligence: ML lineage tables for the Outdoor Comfort nowcast
-- and the Local Pulse engagement signal. Additive only.

-- Point-in-time inputs. For Utah DAQ station readings (entity_type = 'station'),
-- prediction_time is the observation hour and available_at is when this project first
-- ingested it; training joins must only use values available before prediction time.
create table if not exists public.ml_feature_snapshots (
  id bigint generated always as identity primary key,
  entity_id text not null,
  entity_type text not null check (entity_type in ('place', 'area', 'station', 'event')),
  prediction_time timestamptz not null,
  available_at timestamptz not null,
  feature_schema_version text not null,
  features jsonb not null,
  created_at timestamptz not null default now()
);
-- Ingest re-reads overlapping history; this key lets it insert only new station-hours
-- (on conflict do nothing), which also preserves the first-seen available_at.
create unique index if not exists ml_feature_snapshots_identity_key
  on public.ml_feature_snapshots(entity_type, entity_id, prediction_time, feature_schema_version);
create index if not exists ml_feature_snapshots_lookup_idx
  on public.ml_feature_snapshots(entity_id, prediction_time desc, available_at desc);

create table if not exists public.ml_predictions (
  id uuid primary key default gen_random_uuid(),
  entity_id text not null,
  entity_type text not null check (entity_type in ('place', 'area', 'station', 'event')),
  prediction_time timestamptz not null,
  horizon_hours integer not null check (horizon_hours >= 0 and horizon_hours <= 168),
  model_name text not null,
  model_version text not null,
  prediction_kind text not null check (prediction_kind in ('trained_nowcast', 'explainable_estimate', 'conservative_fallback')),
  label text,
  score numeric,
  confidence numeric check (confidence between 0 and 1),
  probabilities jsonb,
  explanations jsonb not null default '[]'::jsonb,
  source_freshness jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create unique index if not exists ml_predictions_identity_key
  on public.ml_predictions(entity_type, entity_id, prediction_time, horizon_hours, model_name, model_version);
create index if not exists ml_predictions_lookup_idx
  on public.ml_predictions(entity_id, prediction_time desc, created_at desc);

-- Privacy-safe aggregate interactions (no user ids, IPs, or movement histories).
-- Only server-side service-role code writes this. No browser insert policy.
create table if not exists public.analytics_events (
  id bigint generated always as identity primary key,
  entity_id text not null,
  entity_type text not null check (entity_type in ('event', 'area', 'place')),
  event_type text not null check (event_type in ('detail_opened', 'saved', 'directions_requested', 'dismissed')),
  occurred_at timestamptz not null,
  session_hash text,
  created_at timestamptz not null default now()
);
create index if not exists analytics_events_training_idx
  on public.analytics_events(entity_id, event_type, occurred_at desc);

alter table public.ml_feature_snapshots enable row level security;
alter table public.ml_predictions enable row level security;
alter table public.analytics_events enable row level security;

create policy "public reads published intelligence predictions"
  on public.ml_predictions for select using (true);

-- Explicit Data API grants (this project does not auto-expose new tables; see
-- 20261107023809_grant_data_api_access.sql). Feature snapshots and analytics stay private.
grant select on public.ml_predictions to anon, authenticated;
grant select, insert, update, delete on
  public.ml_feature_snapshots,
  public.ml_predictions,
  public.analytics_events
to service_role;
grant usage, select on all sequences in schema public to service_role;
