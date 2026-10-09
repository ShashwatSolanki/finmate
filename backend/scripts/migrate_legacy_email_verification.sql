-- One-time rollout aid for deployments that already have local accounts.
-- Run this with psql and enter the exact UTC cutover timestamp for this release.
-- Only accounts created before the cutover are treated as legacy accounts.
-- Review the selected row count before running against production.
\prompt 'Cutover timestamp in UTC (example: 2026-10-10 12:00:00+00): ' verification_cutoff

BEGIN;

UPDATE users
SET is_verified = TRUE
WHERE auth_provider = 'local'
  AND is_verified = FALSE
  AND created_at < :'verification_cutoff'::timestamptz;

COMMIT;
