-- One-time rollout aid for deployments that already have local accounts.
-- Run this with psql and enter the exact UTC cutover timestamp for this release.
-- First review the count below. Then execute the UPDATE statement manually only
-- after confirming that the cutoff includes legacy users but excludes new signups.
\prompt 'Cutover timestamp in UTC (example: 2026-10-10 12:00:00+00): ' verification_cutoff

SELECT COUNT(*) AS legacy_unverified_accounts_to_migrate
FROM users
WHERE auth_provider = 'local'
  AND is_verified = FALSE
  AND created_at < :'verification_cutoff'::timestamptz;

-- After reviewing the count, run this statement with the same cutoff:
-- UPDATE users
-- SET is_verified = TRUE
-- WHERE auth_provider = 'local'
--   AND is_verified = FALSE
--   AND created_at < :'verification_cutoff'::timestamptz;
