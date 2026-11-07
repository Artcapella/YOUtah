-- Explicit Data API grants.
--
-- Supabase projects can be created with "automatically expose new tables" turned
-- off. Tables created by migrations then carry no privileges for the API roles, and
-- every request fails with 42501 even though RLS policies exist. These grants are
-- idempotent (a no-op on projects that already auto-grant) and mirror the RLS
-- policies in the initial schema:
--   * anon / authenticated (publishable key): read the public, curated/live tables.
--     ingest_runs stays private.
--   * service_role (secret key, server-side ingestion only): full read/write.

grant usage on schema public to anon, authenticated, service_role;

grant select on
  public.sources,
  public.places,
  public.events,
  public.observations,
  public.advisories,
  public.stories
to anon, authenticated;

grant select, insert, update, delete on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;
