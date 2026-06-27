-- R1 TRACK dedup requires upsert on source_url; base migration only indexed other columns.
CREATE UNIQUE INDEX IF NOT EXISTS idx_rd_insights_source_url_unique ON rd_insights (source_url);
