-- Comprehensive Utah: source-aware shared data model
create extension if not exists pgcrypto;
create extension if not exists postgis with schema extensions;

create type public.source_status as enum ('active', 'degraded', 'disabled');
create type public.ingest_status as enum ('started', 'succeeded', 'failed');

create table public.sources (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  homepage_url text,
  attribution text,
  refresh_seconds integer,
  status public.source_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.ingest_runs (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  status public.ingest_status not null default 'started',
  records_seen integer not null default 0,
  records_written integer not null default 0,
  error_message text,
  metadata jsonb not null default '{}'::jsonb
);

create table public.places (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  external_id text,
  name text not null,
  category text not null,
  description text,
  address text,
  website_url text,
  source_url text,
  tags text[] not null default '{}',
  location extensions.geography(point, 4326),
  latitude numeric(9,6),
  longitude numeric(9,6),
  elevation_m numeric(8,2),
  raw_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique nulls not distinct (source_id, external_id)
);

create index places_category_idx on public.places(category);
create index places_tags_idx on public.places using gin(tags);
create index places_location_idx on public.places using gist(location);

create table public.events (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  external_id text,
  venue_place_id uuid references public.places(id) on delete set null,
  title text not null,
  description text,
  category text,
  starts_at timestamptz not null,
  ends_at timestamptz,
  price_low numeric(10,2),
  price_high numeric(10,2),
  is_free boolean,
  ticket_url text,
  source_url text,
  latitude numeric(9,6),
  longitude numeric(9,6),
  raw_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique nulls not distinct (source_id, external_id)
);

create index events_starts_at_idx on public.events(starts_at);
create index events_category_idx on public.events(category);

create table public.observations (
  id bigint generated always as identity primary key,
  source_id uuid references public.sources(id) on delete set null,
  metric text not null,
  observed_at timestamptz not null,
  latitude numeric(9,6),
  longitude numeric(9,6),
  value numeric,
  unit text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index observations_metric_time_idx on public.observations(metric, observed_at desc);

create table public.advisories (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  external_id text,
  kind text not null,
  severity text,
  title text not null,
  body text,
  starts_at timestamptz,
  ends_at timestamptz,
  source_url text,
  raw_payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  unique nulls not distinct (source_id, external_id)
);

create table public.stories (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  title text not null,
  teaser text not null,
  body text,
  category text not null,
  source_url text,
  image_url text,
  latitude numeric(9,6),
  longitude numeric(9,6),
  is_featured boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.sources enable row level security;
alter table public.ingest_runs enable row level security;
alter table public.places enable row level security;
alter table public.events enable row level security;
alter table public.observations enable row level security;
alter table public.advisories enable row level security;
alter table public.stories enable row level security;

create policy "public reads active source metadata" on public.sources for select using (true);
create policy "public reads places" on public.places for select using (true);
create policy "public reads events" on public.events for select using (true);
create policy "public reads observations" on public.observations for select using (true);
create policy "public reads advisories" on public.advisories for select using (true);
create policy "public reads stories" on public.stories for select using (true);

insert into public.sources (slug, name, homepage_url, attribution, refresh_seconds) values
  ('nws', 'National Weather Service', 'https://weather.gov', 'Weather data: National Weather Service', 900),
  ('utah-daq', 'Utah Division of Air Quality', 'https://air.utah.gov', 'Air quality data: Utah Division of Air Quality', 900),
  ('udot', 'UDOT Traffic', 'https://udottraffic.utah.gov', 'Road data: UDOT Traffic', 300),
  ('uta', 'Utah Transit Authority', 'https://rideuta.com', 'Transit data: Utah Transit Authority', 120),
  ('ugrc', 'Utah Geospatial Resource Center', 'https://gis.utah.gov', 'Geospatial data: UGRC', 86400),
  ('ticketmaster', 'Ticketmaster Discovery', 'https://developer.ticketmaster.com', 'Event data: Ticketmaster', 900)
on conflict (slug) do nothing;

insert into public.stories (slug, title, teaser, category, latitude, longitude, is_featured) values
  ('the-whale', 'The Whale', 'A beloved 9th & 9th landmark and a perfect entry point into Salt Lake City''s wonderfully specific local culture.', 'Local lore', 40.736816, -111.863170, true),
  ('community-libraries', 'Libraries as third places', 'Find free events, workspace, language resources, and community connection across Utah.', 'Community', 40.760780, -111.891050, true)
on conflict (slug) do nothing;
