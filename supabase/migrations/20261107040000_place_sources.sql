-- Places from the API connectors (backend/providers/ridb.py, osm.py, ugrc.py),
-- plus a proximity query so the dashboard reads nearby rows from a statewide table.

-- `ugrc` already exists (initial schema). Names are shown as each place's attribution.
insert into public.sources (slug, name, homepage_url, attribution, refresh_seconds) values
  ('ridb', 'Recreation.gov', 'https://ridb.recreation.gov', 'Facility data: Recreation.gov (RIDB)', 604800),
  ('osm', 'OpenStreetMap contributors', 'https://www.openstreetmap.org/copyright', '© OpenStreetMap contributors (ODbL)', 604800)
on conflict (slug) do nothing;

-- The places nearest a point: at most `per_category` per category within `radius_m`,
-- nearest first. Ranking happens here because a statewide table can hold more rows
-- near a city than one Data API response returns (1000 by default).
-- SECURITY INVOKER (the default), so the caller's RLS policies still apply.
create or replace function public.nearby_places(
  lat double precision,
  lng double precision,
  radius_m double precision default 40000,
  per_category integer default 8
)
returns table (
  id uuid,
  name text,
  category text,
  description text,
  source_url text,
  website_url text,
  tags text[],
  latitude numeric,
  longitude numeric,
  elevation_m numeric,
  updated_at timestamptz,
  source_name text,
  distance_m double precision
)
language sql
stable
set search_path = ''
as $$
  with origin as (
    select extensions.st_setsrid(extensions.st_makepoint(lng, lat), 4326)::extensions.geography as point
  ),
  candidates as (
    select
      p.id, p.name, p.category, p.description, p.source_url, p.website_url, p.tags,
      p.latitude, p.longitude, p.elevation_m, p.updated_at,
      s.name as source_name,
      extensions.st_distance(
        extensions.st_setsrid(extensions.st_makepoint(p.longitude::float8, p.latitude::float8), 4326)::extensions.geography,
        o.point
      ) as distance_m
    from public.places p
    cross join origin o
    left join public.sources s on s.id = p.source_id
    where p.latitude is not null
      and p.longitude is not null
      -- Cheap latitude band first (1 degree is about 111.3 km); exact distance below.
      and p.latitude between lat - radius_m / 111320.0 and lat + radius_m / 111320.0
  ),
  ranked as (
    select c.*, row_number() over (partition by c.category order by c.distance_m) as category_rank
    from candidates c
    where c.distance_m <= radius_m
  )
  select
    r.id, r.name, r.category, r.description, r.source_url, r.website_url, r.tags,
    r.latitude, r.longitude, r.elevation_m, r.updated_at, r.source_name, r.distance_m
  from ranked r
  where r.category_rank <= per_category
  order by r.distance_m
$$;

grant execute on function public.nearby_places(double precision, double precision, double precision, integer)
  to anon, authenticated, service_role;
