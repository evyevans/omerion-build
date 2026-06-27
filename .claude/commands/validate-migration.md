---
description: Validate a Supabase migration SQL file for idempotency, safety, and schema compatibility before running it in production.
argument-hint: "Path to .sql file (relative to project root) or paste SQL directly"
---

You are validating a Supabase migration for the Omerion backend.

Input: **$ARGUMENTS**

If this is a file path, read the file. If this is inline SQL, parse it directly.

**Run these checks in order:**

1. **IDEMPOTENCY CHECK**
   Every `CREATE TABLE` must use `CREATE TABLE IF NOT EXISTS`.
   Every `CREATE INDEX` must use `CREATE INDEX IF NOT EXISTS` or `ON CONFLICT DO NOTHING`.
   Every `ALTER TABLE ADD COLUMN` must use `IF NOT EXISTS`.
   Every `INSERT` into a reference table must use `ON CONFLICT DO NOTHING`.
   Flag any statement that would fail on a second run.

2. **DESTRUCTIVE OPERATION CHECK**
   Flag any: `DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, `DELETE FROM`.
   If found, require explicit justification and backup confirmation.

3. **SCHEMA COMPATIBILITY CHECK**
   Cross-reference against these known tables and their PK conventions:
   - `contacts` — PK: verify if `id` or `contact_id` (ASK if ambiguous)
   - `scores` — FK: `contact_id` references `contacts`
   - `opportunities` — FK: `contact_id` references `contacts`
   - `blueprints` — FK: `contact_id` references `contacts`
   - `outbound_communications` — has `idempotency_key` UNIQUE constraint
   - `agent_telemetry` — append-only, no deletes

4. **RLS POLICY CHECK**
   Does the migration include Row Level Security policies?
   If new tables are created without RLS, flag as a security gap.

5. **OUTPUT**
   - List: PASS / FAIL for each check
   - List: specific line numbers for any issues
   - Provide a corrected version of the SQL if any issues found
   - State: "SAFE TO RUN" or "REQUIRES REVIEW" as final verdict
