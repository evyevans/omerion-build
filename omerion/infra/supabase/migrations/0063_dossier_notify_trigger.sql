-- 0063: pg_notify trigger for research_dossiers INSERT
-- Fires 'dossier_ready' notification so the asyncpg listener dispatches
-- strategic-arch immediately on dossier insert (push vs polling).

CREATE OR REPLACE FUNCTION notify_dossier_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM pg_notify(
    'dossier_ready',
    json_build_object(
      'dossier_id',  NEW.dossier_id,
      'account_id',  NEW.account_id,
      'created_at',  NEW.created_at
    )::text
  );
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_dossier_insert ON research_dossiers;

CREATE TRIGGER on_dossier_insert
  AFTER INSERT ON research_dossiers
  FOR EACH ROW
  EXECUTE FUNCTION notify_dossier_ready();
