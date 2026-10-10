# Security hardening

This document tracks security-specific behavior and deployment checks. It is not a penetration-test report or a guarantee that the application is vulnerability-free.

## Implemented in the hardening branch

- Backend and lightweight staging environments update `python-multipart`, Pillow, PyMuPDF, `python-dotenv`, PDFMiner (through `pdfplumber`), FastAPI/Starlette, and Sentence Transformers/Transformers. JWT handling was moved from `python-jose` (which pulls `ecdsa`) to PyJWT after the new dependency audit identified known advisories in the previous tree.
- CI runs `pip-audit` for Python dependencies and `npm audit --omit=dev --audit-level=high` for production frontend dependencies.
- Invoice uploads are read in bounded chunks and rejected above 12 MiB. The parser determines PDF/image type from file contents, allows only supported raster image formats, and rejects images above 25 megapixels before OCR.
- Docker Compose binds PostgreSQL to `127.0.0.1` rather than all host interfaces. The sample password is for local development only; do not reuse it for a shared or production database.
- Verification and password-reset codes are persisted as salted PBKDF2 hashes. On first startup after deployment, PostgreSQL migration code adds hash columns and clears legacy plaintext OTPs. Pending legacy codes are intentionally invalidated; users must request fresh codes.
- Account creation is rate-limited by normalized email and client IP. Production/staging still require shared Redis rate limiting.
- OTP previews in API responses are returned only when mock email is explicitly enabled in known local/test environments; other environment names fail closed.
- Protected frontend requests use a shared single-flight refresh flow: expired access tokens trigger one refresh attempt, the token pair is rotated, and the original request is retried once.
- Invalid structured invoice PDF requests return a validation response rather than an unhandled server error.

## Deployment checklist

1. Set `APP_ENV=production` or `APP_ENV=staging` explicitly and configure a long random `JWT_SECRET`.
2. Configure HTTPS-only `CORS_ORIGINS`, real email delivery, and `AUTH_RATE_LIMIT_REDIS_URL` as required by `backend/app/config.py`.
3. Do not enable `EMAIL_MOCK_MODE` or `AUTH_ALLOW_MOCK_GOOGLE` in deployed environments.
4. Keep PostgreSQL private. Do not reuse local Compose credentials, and do not connect staging to production financial data.
5. Before deploying the OTP-hash migration, tell users that any still-pending verification/reset OTP may need to be re-issued. The migration preserves accounts and financial/chat records but deliberately invalidates plaintext pending codes.
6. Enforce request-body limits, timeouts, and concurrency limits at the hosting/proxy layer as well as in the application.
7. Run the full backend and frontend test/build workflow; inspect dependency-audit findings and do not deploy with unresolved high-severity advisories without a reviewed exception.

## Dependency audit commands

From the repository root:

```bash
cd backend
python -m pip install -r requirements.txt pip-audit
pip-audit -r requirements.txt
```

For frontend production dependencies:

```bash
cd frontend
npm ci
npm audit --omit=dev --audit-level=high
npm test
npm run build
```

The CI workflow performs the dependency audits on pull requests. Advisory databases change over time, so a clean audit is only a point-in-time result.

## Remaining risks / follow-up work

- Access and refresh tokens are currently stored in browser `localStorage`. Any successful same-origin XSS could read them. Moving refresh tokens to appropriately scoped HttpOnly/Secure cookies and using a strong Content Security Policy should be considered in a separate, carefully tested change.
- Run a threat model and authorized penetration test against a deployed, isolated staging environment before storing real financial data.
- Consider migrating ad-hoc startup schema changes to a versioned migration framework once the schema change workflow is stable.
- Vite is a development/build dependency and is not included in the production-only npm audit gate. The current dev script explicitly binds to `127.0.0.1`; keep the Vite toolchain updated and do not change it to a network-facing host without reviewing the applicable Vite advisories.
- Security controls reduce risk but do not establish that every possible vulnerability has been found.
