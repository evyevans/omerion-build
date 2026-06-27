-- Migration: add google_jobs to job_platform enum
-- Google Jobs (via SerpAPI) aggregates Indeed, LinkedIn, Glassdoor, ZipRecruiter
-- and company career pages into one structured source.

ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'google_jobs';
