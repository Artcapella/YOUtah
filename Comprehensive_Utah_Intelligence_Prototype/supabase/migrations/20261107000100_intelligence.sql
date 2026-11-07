-- Additive ML lineage tables for Comprehensive Utahn.
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
create index if not exists ml_predictions_lookup_idx
  on public.ml_predictions(entity_id, prediction_time desc, created_at desc);

-- Only server-side service-role code should write this. No browser insert policy.
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
