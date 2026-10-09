# Frontend

## 1. Stack

The frontend uses React with Vite and TypeScript.

The application is organized around authenticated pages and reusable chat/invoice components.

## 2. Main pages

| Route | Purpose |
|---|---|
| `/login` | Sign in |
| `/register` | Create an account |
| `/chat` | Main assistant and conversation interface |
| `/settings` | Financial profile, imports, portfolio and Invoice Studio |

## 3. Important components

Examples include:

- `ChatSidebar.tsx`
- `ChatComposerMenu.tsx`
- `InvoiceExportActions.tsx`
- `MessageMetadata.tsx`
- `InvoiceImportPanel.tsx`

## 4. Chat page

The chat interface is responsible for:

1. loading persisted conversations
2. selecting/creating conversations
3. sending authenticated chat requests
4. displaying user and assistant turns
5. showing the selected/responding agent
6. displaying metadata
7. showing invoice export actions when artifacts exist
8. supporting document/CSV import from the composer

The backend response contains machine-readable tags and JSON. The frontend removes those implementation details before displaying the natural-language response.

## 5. Settings

The Settings page provides:

- onboarding profile
- transaction CSV import
- portfolio holdings
- live-price refresh
- Invoice Studio
- invoice import
- sample PDF generation

## 6. Authentication state

The frontend stores the short-lived access token (`finmate_token`) and rotating refresh token (`finmate_refresh_token`) in browser `localStorage`. Authenticated API calls go through the shared `apiFetch` helper.

When an authenticated request returns HTTP 401, `apiFetch` coordinates a single in-flight `POST /api/auth/refresh` request, persists the rotated token pair, and retries the original request once. This single-flight behavior matters because the backend revokes each refresh token after use. If the refresh token is definitively invalid or expired, the helper clears stored auth and the protected route returns to login; transient network failures do not automatically clear the session.

**Security limitation:** tokens in `localStorage` are readable by JavaScript and can be exfiltrated by a successful XSS attack. Before production use with real financial accounts, consider migrating the refresh token to a `Secure`, `HttpOnly`, appropriately `SameSite` cookie and adding the required CSRF/origin protections. The current flow improves expiration handling but does not eliminate this XSS risk.

## 7. Backend integration

The frontend communicates with the FastAPI backend through REST APIs.

During local development, Vite proxies `/api` requests to the backend.

This keeps frontend and backend independently replaceable as long as the API contract is preserved.

## 8. UI data flow

```text
User action
   ↓
React page/component
   ↓
REST API
   ↓
FastAPI
   ↓
database / agent / service
   ↓
JSON response
   ↓
React state
   ↓
UI
```

## 9. Invoice integration

For invoice workflows:

```text
Upload / chat request
       ↓
FastAPI invoice route/agent
       ↓
Structured invoice
       ↓
Frontend preview / Invoice Studio
       ↓
PDF or CSV export
```

## 10. Frontend documentation rule

Document behavior and API contracts here. Avoid copying backend implementation details into frontend documentation; link to the backend/agent documents instead.
