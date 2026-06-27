# Deployer — Migration Safety Checklist

Last updated: 2026-06-04
Maintained by: DEPLOY (deployer, Agent #18)

Surfaced to the founder during the G3 HITL migration review gate.
DEPLOYER never executes any migration without explicit founder approval.

## Green — Safe to Approve

| Pattern | Why safe |
|---------|---------|
| `ADD COLUMN IF NOT EXISTS` with DEFAULT value | Non-breaking; existing rows get the default |
| `CREATE INDEX CONCURRENTLY IF NOT EXISTS` | Non-locking on Postgres; safe on live table |
| `CREATE TABLE IF NOT EXISTS` | Additive only; no existing data affected |
| `CREATE OR REPLACE FUNCTION` | Atomic in-place replacement |
| `UPDATE ... WHERE ...` with specific filter | Scoped; verify row count in EXPLAIN first |
| `ADD CONSTRAINT IF NOT EXISTS` | Applies to new rows; existing rows scanned once |

## Yellow — Review Carefully Before Approving

| Pattern | Risk | Check before approving |
|---------|------|----------------------|
| `ALTER COLUMN TYPE` | Fails if existing data doesn't cast | Run `SELECT COUNT(*) WHERE col::newtype IS NULL` |
| `DROP CONSTRAINT IF EXISTS` alone | Removes integrity guarantee | Confirm replacement is in same migration |
| `ADD COLUMN NOT NULL` without DEFAULT | Fails on tables with existing rows | Must have DEFAULT or be split into two migrations |
| `CREATE UNIQUE INDEX` on existing data | Fails if duplicates exist | Run `SELECT col, COUNT(*) GROUP BY col HAVING COUNT(*) > 1` |

## Red — Do Not Approve Without Explicit Justification

| Pattern | Risk |
|---------|------|
| `DROP TABLE` | Irreversible data loss |
| `DROP COLUMN` | Irreversible data loss |
| `TRUNCATE` | Wipes all rows; cannot be rolled back |
| `RENAME TABLE` / `RENAME COLUMN` | Breaks all PostgREST queries referencing old name |
| Any DDL without `IF NOT EXISTS` / `IF EXISTS` guard | Not idempotent — double-run will error |
| Any modification to `auth.*` or `storage.*` schemas | Supabase-managed — may break the platform |

## Idempotency Rule

Every migration run through DEPLOYER MUST be idempotent.
If any statement lacks an `IF NOT EXISTS` / `IF EXISTS` guard on a CREATE or DROP:
reject the migration and return to the originating agent with explanation.
