# Free-Tier Staging Deployment

This setup is for an isolated staging environment only. Do not connect it to a production database or reuse production secrets.

## Proposed services and cost

- Render Static Site: React/Vite frontend, free tier.
- Render Free Web Service: FastAPI backend, free tier; sleeps after 15 minutes of inactivity and may take about a minute to wake.
- Neon Free: separate PostgreSQL project, currently 1 GB storage per project.
- Upstash Redis Free: shared auth rate limiter, currently 256 MB and 500,000 commands/month.
- Resend Free: transactional email API, currently 3,000 emails/month with a 100/day cap.

Expected platform spend is $0/month while each service remains within its free limits and no paid add-ons/overages are enabled. Free-tier terms can change; review the current [Render free plan](https://render.com/docs/free), [Neon pricing](https://neon.com/pricing), [Upstash Redis pricing](https://upstash.com/pricing/redis), and [Resend pricing](https://resend.com/pricing/) before creating resources. Render free PostgreSQL is deliberately not used because it expires after 30 days and has no backups.

## Before creating services

1. Create a new Neon project for staging. Do not import production data. Copy its PostgreSQL connection string; ensure it uses TLS and URL-encode special characters in credentials.
2. Create an Upstash Redis database. Copy its TLS rediss:// URL. Do not commit it.
3. Create a Resend API key. The Resend-provided `onboarding@resend.dev` sender is for testing and cannot be assumed to deliver to arbitrary user addresses. To test registration and password-reset email to your own real inbox, verify a domain you control in Resend and set `RESEND_FROM_EMAIL` to an authorized address on that domain. Do not use a sender domain you do not control.
4. Confirm the Render account can create free web/static services. No resources are created by this repository change.

## Deploy from Render Blueprint

1. Confirm `render.yaml`, `backend/requirements-staging.txt`, and this guide are present on `main` (merge the reviewed setup PR first if they are not).
2. In Render, choose New → Blueprint, connect ShashwatSolanki/finmate, and select branch `main`. Review the proposed resources before applying.
3. The Blueprint defines:
   - finmate-shashwat-staging-api — Python/FastAPI, Singapore region, free instance.
   - finmate-shashwat-staging-web — static React/Vite site.
4. Enter the protected sync:false values in the API service environment:
   - DATABASE_URL — the staging Neon URL.
   - AUTH_RATE_LIMIT_REDIS_URL — the Upstash TLS URL.
   - RESEND_API_KEY — the Resend key.
5. Render generates `JWT_SECRET` for the API. Keep `EMAIL_MOCK_MODE=false`, `AUTH_ALLOW_MOCK_GOOGLE=false`, `FINMATE_USE_LLM=false`, and `FINMATE_USE_EMBEDDINGS=false` in staging. Startup validation now fails for staging if the JWT secret is a placeholder or shorter than 32 characters, email mock mode or mock Google auth is enabled, real email credentials are missing, the shared Redis URL is absent, or `CORS_ORIGINS` is not an explicit HTTPS-only allowlist. The Blueprint contains only the hosted frontend origin; do not append localhost origins to the deployed service.
6. If Render assigns a different static-site hostname, update `CORS_ORIGINS` on the API to include only the exact `https://` frontend origin, then redeploy the API. Do not add localhost to hosted CORS allowlists.
7. Confirm the API health endpoint returns {"status":"ok"} at https://<api-host>/api/health.

## Low-memory staging profile

The free backend uses backend/requirements-staging.txt, which excludes PyTorch, Transformers, PEFT and Sentence Transformers. The embedding and local Qwen paths are explicitly disabled to reduce RAM and build time. Intent routing uses keyword signals and memory falls back to recent stored memories; AI responses and semantic retrieval will not match the full local setup.

The lightweight image does not install the system Tesseract executable, so scanned-image OCR is not validated in this profile. Text-based PDF parsing remains available. Install OCR separately or use a paid/container build if scanned-document OCR is a required acceptance criterion. Google sign-in is not configured by default; set the matching `GOOGLE_CLIENT_ID` on the API and `VITE_GOOGLE_CLIENT_ID` on the static site only if Google sign-in is part of the staging test plan.

## Authentication and financial validation checklist

Run these checks only against staging, using synthetic test data:

- GET /api/health returns 200.
- Register a new account; confirm a real verification email arrives.
- Verify with the correct code; reject an incorrect/expired/reused code.
- Confirm login is blocked before verification and works after verification.
- Confirm invalid OTP attempts are rate-limited across requests using Redis.
- Test forgot/reset password; verify refresh tokens are revoked after password reset.
- Confirm unauthenticated requests cannot read user data.
- Create expense and income transactions; confirm monthly summaries total expenses only and reject ambiguous mixed-currency totals.
- Export a CSV containing cells starting with =, +, -, or @; verify the values are escaped.
- Test budget PATCH with null for a required field; expect validation failure, not HTTP 500.
- Test portfolio holdings with matching and mismatching quote currencies.
- Confirm no production database or production API key was used.

## Rollback and cleanup

Staging is disposable. If validation fails, stop before any production rollout. Delete the Render staging services and the dedicated Neon/Upstash/Resend resources when finished if you no longer need them. Export any test evidence first; never rely on free-tier staging for durable storage or backups.
