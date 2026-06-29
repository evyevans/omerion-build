-- 0068_founder_review_discord_message.sql
-- Capture the Discord message id of the HITL card so the /hitl/resolve handler
-- can edit the original card in place (strip the approve/reject links and stamp
-- "✅ Approved by founder · <time>") once the founder clicks.
--
-- Without this, the webhook-posted card has no feedback loop: a markdown-link
-- click is a GET to the backend that Discord never sees, so the card stays
-- forever showing live Approve/Reject links even after the decision is recorded.

ALTER TABLE founder_review_queue
    ADD COLUMN IF NOT EXISTS discord_message_id TEXT;

-- PostgREST caches the schema; reload it so the new column is writable immediately.
NOTIFY pgrst, 'reload schema';
