-- Run once if you already had a `users` table without `password_hash`:

--   psql $DATABASE_URL -f scripts/migrate_add_password_hash.sql



ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);


