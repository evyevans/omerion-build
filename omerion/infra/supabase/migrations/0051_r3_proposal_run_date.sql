-- Migration 0051: add run_date to rd_proposals for R3 idempotency guard
-- R3 write_proposal checks (title, run_date) before INSERT to prevent
-- duplicate proposals on checkpoint replay within the same weekly run.

ALTER TABLE rd_proposals
  ADD COLUMN IF NOT EXISTS run_date DATE NOT NULL DEFAULT CURRENT_DATE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_rd_proposals_title_run_date
  ON rd_proposals (title, run_date);

COMMENT ON COLUMN rd_proposals.run_date IS
  'The calendar date of the R3 synthesis run that created this proposal. '
  'Combined with title as idempotency key — prevents duplicate rows on replay.';
