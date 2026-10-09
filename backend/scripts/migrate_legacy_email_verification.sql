-- One-time rollout aid for deployments that already have local accounts.
-- IMPORTANT: Replace the timestamp below with the exact UTC cutover time for this release.
-- Only accounts created before that cutoff are treated as legacy accounts.
-- Run once against the production database after reviewing the selected row count.
BEGIN;

UPDATE users
SET is_verified = TRUE
WHERE auth_provider = 'local'
  AND is_verified = FALSE
  AND created_at < TIMESTAMPTZ '2026-10-10 00:00:00+00';

COMMIT;
