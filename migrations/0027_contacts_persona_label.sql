-- 0027_contacts_persona_label.sql
-- Deprecates the persona enum on `contacts` in favor of a free-text label.
-- Industry packs own the persona taxonomy; the database stores the label.
-- Idempotent.

BEGIN;

ALTER TABLE IF EXISTS contacts
  ADD COLUMN IF NOT EXISTS persona_label TEXT;

UPDATE contacts
   SET persona_label = persona::TEXT
 WHERE persona_label IS NULL
   AND persona IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_persona_label
  ON contacts(persona_label)
  WHERE persona_label IS NOT NULL;

-- Note: we don't DROP the old `persona` column yet — wait one release
-- so existing code keeps reading from it. Removal scheduled for 0028.

COMMIT;
