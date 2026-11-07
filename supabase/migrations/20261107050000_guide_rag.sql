-- YOUtah guide RAG index.
--
-- This is intentionally a derived/search table: normalized places, events,
-- advisories, and stories remain the source of truth. The service-role-only
-- refresh function copies source attribution forward and clears an embedding
-- when its source content changes.

create extension if not exists vector with schema extensions;

create table if not exists public.guide_documents (
  id uuid primary key default gen_random_uuid(),
  document_key text not null unique,
  entity_type text not null check (entity_type in ('place', 'event', 'advisory', 'story', 'other')),
  entity_id uuid,
  title text not null,
  content text not null,
  source_name text not null default 'Curated',
  source_url text,
  tags text[] not null default '{}'::text[],
  latitude numeric(9,6),
  longitude numeric(9,6),
  freshness_at timestamptz,
  -- A product signal, not a paid-placement mechanism. Sponsored inventory should
  -- live in its own table and be visibly labelled at render time.
  popularity_score real not null default 0.25 check (popularity_score between 0 and 1),
  metadata jsonb not null default '{}'::jsonb,
  search_vector tsvector generated always as (
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(content, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(array_to_string(tags, ' '), '')), 'C')
  ) stored,
  -- text-embedding-3-small's default dimensionality. Keep OPENAI_EMBEDDING_MODEL
  -- aligned with this value, or make a deliberate migration before changing it.
  embedding extensions.vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists guide_documents_search_idx on public.guide_documents using gin(search_vector);
create index if not exists guide_documents_tags_idx on public.guide_documents using gin(tags);
create index if not exists guide_documents_freshness_idx on public.guide_documents(freshness_at desc);
create index if not exists guide_documents_embedding_idx
  on public.guide_documents using hnsw (embedding extensions.vector_cosine_ops)
  with (m = 16, ef_construction = 64);

alter table public.guide_documents enable row level security;

-- Context is retrieved only through server code. This prevents a caller from
-- dumping all indexed content or vector metadata through the public Data API.
grant select, insert, update, delete on public.guide_documents to service_role;

create or replace function public.refresh_guide_documents()
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  places_written integer := 0;
  events_written integer := 0;
  advisories_written integer := 0;
  stories_written integer := 0;
begin
  insert into public.guide_documents (
    document_key, entity_type, entity_id, title, content, source_name, source_url,
    tags, latitude, longitude, freshness_at, metadata
  )
  select
    'place:' || p.id::text,
    'place',
    p.id,
    p.name,
    concat_ws(E'\n', p.description, p.address, nullif('Tags: ' || array_to_string(p.tags, ', '), 'Tags: ')),
    coalesce(s.name, 'Curated'),
    coalesce(p.source_url, p.website_url),
    array_remove(array_cat(array[p.category], p.tags), null),
    p.latitude,
    p.longitude,
    p.updated_at,
    jsonb_build_object('category', p.category, 'website_url', p.website_url)
  from public.places p
  left join public.sources s on s.id = p.source_id
  on conflict (document_key) do update set
    entity_type = excluded.entity_type,
    entity_id = excluded.entity_id,
    title = excluded.title,
    content = excluded.content,
    source_name = excluded.source_name,
    source_url = excluded.source_url,
    tags = excluded.tags,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    freshness_at = excluded.freshness_at,
    metadata = excluded.metadata,
    embedding = case
      when public.guide_documents.title is distinct from excluded.title
        or public.guide_documents.content is distinct from excluded.content
        or public.guide_documents.tags is distinct from excluded.tags
      then null
      else public.guide_documents.embedding
    end,
    updated_at = now();
  get diagnostics places_written = row_count;

  insert into public.guide_documents (
    document_key, entity_type, entity_id, title, content, source_name, source_url,
    tags, latitude, longitude, freshness_at, metadata
  )
  select
    'event:' || e.id::text,
    'event',
    e.id,
    e.title,
    concat_ws(E'\n', e.description, 'Venue: ' || coalesce(p.name, 'Location TBA'),
      'Starts: ' || e.starts_at::text,
      case when e.is_free then 'Free' when e.price_low is not null then 'From $' || e.price_low::text else null end),
    coalesce(s.name, 'Curated'),
    coalesce(e.ticket_url, e.source_url),
    array_remove(array[e.category, 'event'], null),
    coalesce(e.latitude, p.latitude),
    coalesce(e.longitude, p.longitude),
    e.updated_at,
    jsonb_build_object('starts_at', e.starts_at, 'ends_at', e.ends_at, 'is_free', e.is_free)
  from public.events e
  left join public.sources s on s.id = e.source_id
  left join public.places p on p.id = e.venue_place_id
  on conflict (document_key) do update set
    entity_type = excluded.entity_type,
    entity_id = excluded.entity_id,
    title = excluded.title,
    content = excluded.content,
    source_name = excluded.source_name,
    source_url = excluded.source_url,
    tags = excluded.tags,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    freshness_at = excluded.freshness_at,
    metadata = excluded.metadata,
    embedding = case
      when public.guide_documents.title is distinct from excluded.title
        or public.guide_documents.content is distinct from excluded.content
        or public.guide_documents.tags is distinct from excluded.tags
      then null
      else public.guide_documents.embedding
    end,
    updated_at = now();
  get diagnostics events_written = row_count;

  insert into public.guide_documents (
    document_key, entity_type, entity_id, title, content, source_name, source_url,
    tags, freshness_at, metadata
  )
  select
    'advisory:' || a.id::text,
    'advisory',
    a.id,
    a.title,
    concat_ws(E'\n', a.body, 'Starts: ' || a.starts_at::text, 'Ends: ' || a.ends_at::text),
    coalesce(s.name, 'Curated'),
    a.source_url,
    array_remove(array[a.kind, a.severity, 'conditions'], null),
    a.updated_at,
    jsonb_build_object('kind', a.kind, 'severity', a.severity, 'starts_at', a.starts_at, 'ends_at', a.ends_at)
  from public.advisories a
  left join public.sources s on s.id = a.source_id
  on conflict (document_key) do update set
    entity_type = excluded.entity_type,
    entity_id = excluded.entity_id,
    title = excluded.title,
    content = excluded.content,
    source_name = excluded.source_name,
    source_url = excluded.source_url,
    tags = excluded.tags,
    freshness_at = excluded.freshness_at,
    metadata = excluded.metadata,
    embedding = case
      when public.guide_documents.title is distinct from excluded.title
        or public.guide_documents.content is distinct from excluded.content
        or public.guide_documents.tags is distinct from excluded.tags
      then null
      else public.guide_documents.embedding
    end,
    updated_at = now();
  get diagnostics advisories_written = row_count;

  insert into public.guide_documents (
    document_key, entity_type, entity_id, title, content, source_name, source_url,
    tags, latitude, longitude, freshness_at, metadata
  )
  select
    'story:' || st.id::text,
    'story',
    st.id,
    st.title,
    concat_ws(E'\n', st.teaser, st.body),
    'Curated',
    st.source_url,
    array[st.category, 'story'],
    st.latitude,
    st.longitude,
    st.updated_at,
    jsonb_build_object('slug', st.slug, 'category', st.category, 'featured', st.is_featured)
  from public.stories st
  on conflict (document_key) do update set
    entity_type = excluded.entity_type,
    entity_id = excluded.entity_id,
    title = excluded.title,
    content = excluded.content,
    source_name = excluded.source_name,
    source_url = excluded.source_url,
    tags = excluded.tags,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    freshness_at = excluded.freshness_at,
    metadata = excluded.metadata,
    embedding = case
      when public.guide_documents.title is distinct from excluded.title
        or public.guide_documents.content is distinct from excluded.content
        or public.guide_documents.tags is distinct from excluded.tags
      then null
      else public.guide_documents.embedding
    end,
    updated_at = now();
  get diagnostics stories_written = row_count;

  delete from public.guide_documents gd
  where (gd.entity_type = 'place' and not exists (select 1 from public.places p where p.id = gd.entity_id))
     or (gd.entity_type = 'event' and not exists (select 1 from public.events e where e.id = gd.entity_id))
     or (gd.entity_type = 'advisory' and not exists (select 1 from public.advisories a where a.id = gd.entity_id))
     or (gd.entity_type = 'story' and not exists (select 1 from public.stories st where st.id = gd.entity_id));

  return jsonb_build_object(
    'places', places_written,
    'events', events_written,
    'advisories', advisories_written,
    'stories', stories_written
  );
end;
$$;

create or replace function public.search_guide_documents(
  query_text text,
  query_embedding extensions.vector(1536) default null,
  match_count integer default 8,
  interest_tags text[] default '{}'::text[],
  origin_lat numeric default null,
  origin_lng numeric default null
)
returns table (
  document_key text,
  entity_type text,
  entity_id uuid,
  title text,
  content text,
  source_name text,
  source_url text,
  tags text[],
  latitude numeric,
  longitude numeric,
  freshness_at timestamptz,
  score real
)
language sql
stable
security definer
set search_path = public, extensions
as $$
  with query_input as (
    select websearch_to_tsquery('english', coalesce(nullif(trim(query_text), ''), 'utah')) as terms
  ),
  ranked as (
    select
      d.*,
      coalesce(ts_rank_cd(d.search_vector, q.terms, 32), 0)::real as keyword_score,
      case
        when query_embedding is not null and d.embedding is not null
        then greatest(0, 1 - (d.embedding <=> query_embedding))::real
        else 0::real
      end as vector_score,
      case when coalesce(interest_tags, '{}'::text[]) && d.tags then 0.12::real else 0::real end as persona_score,
      case
        when origin_lat is not null and origin_lng is not null and d.latitude is not null and d.longitude is not null
        then greatest(
          0::double precision,
          0.12 - extensions.st_distance(
            extensions.st_setsrid(extensions.st_makepoint(d.longitude::double precision, d.latitude::double precision), 4326)::extensions.geography,
            extensions.st_setsrid(extensions.st_makepoint(origin_lng::double precision, origin_lat::double precision), 4326)::extensions.geography
          ) / 8000000.0
        )::real
        else 0::real
      end as proximity_score
    from public.guide_documents d
    cross join query_input q
  )
  select
    document_key, entity_type, entity_id, title, content, source_name, source_url,
    tags, latitude, longitude, freshness_at,
    (
      keyword_score * 0.48 +
      vector_score * 0.33 +
      persona_score +
      proximity_score +
      popularity_score * 0.07
    )::real as score
  from ranked
  order by score desc, freshness_at desc nulls last
  limit least(greatest(match_count, 1), 20);
$$;

revoke all on function public.refresh_guide_documents() from public;
revoke all on function public.search_guide_documents(text, extensions.vector, integer, text[], numeric, numeric) from public;
grant execute on function public.refresh_guide_documents() to service_role;
grant execute on function public.search_guide_documents(text, extensions.vector, integer, text[], numeric, numeric) to service_role;
