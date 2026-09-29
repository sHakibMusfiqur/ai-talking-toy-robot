# Phase 5 API Reference

Base path: `/api/v1/`. Content type: `application/json`.

## Conventions

- **Authentication:** session cookie (`sessionid`) established by `POST /auth/login/`.
  All authenticated `POST`/`PATCH`/`DELETE` requests require a valid CSRF token
  (`csrftoken` cookie + `X-CSRFToken` header). Missing/invalid CSRF → `403`.
- **IDs:** MongoDB ObjectIds serialized as 24-char lowercase hex strings
  (e.g. `"665f1c2e8b3d4a5f6e7d8c9b"`). Never integers.
- **Errors:** `{"detail": "..."}` for 401/403/404/405, field-keyed maps for 400
  (e.g. `{"first_name": ["This field is required."]}`).
- **Ownership:** child resources are scoped to the logged-in parent. Any child
  not owned by the caller (including malformed ids) returns **404**, never 403 —
  resource existence is never leaked across parents.

## Endpoints

### `GET /health`

Canonical liveness check, no auth, no DB call, **no redirect**.

`200` → `{"status": "ok"}`

`GET /health/` is kept as a compatibility alias and returns the same 200.

### `POST /api/v1/auth/signup/`

Creates a `User` (role `parent`) and linked `Parent` atomically. Does **not**
log the caller in. Password must pass Django's four default validators.

Request:

```json
{"email": "parent@example.com", "password": "S3cure!Passw0rd"}
```

- `201` → `{"id": "...", "email": "parent@example.com"}`
- `400` → duplicate email or weak password (`{"email": [...]}` / `{"password": [...]}`)

Emails are trimmed and lowercased before storage and uniqueness checks.

### `POST /api/v1/auth/login/`

Request:

```json
{"email": "parent@example.com", "password": "S3cure!Passw0rd"}
```

- `200` → `{"id": "...", "email": "parent@example.com", "role": "parent"}`,
  sets session cookie
- `401` → `{"detail": "Invalid credentials."}` — byte-identical for unknown
  email and wrong password (no account-existence leak)

### `POST /api/v1/auth/logout/`

- `204` — session destroyed
- `401` if unauthenticated, `403` if CSRF missing

### `GET /api/v1/children/`

- `200` → array of the caller's children only:
  `[{"id": "...", "first_name": "Nadi", "age_band": "7-9", "language": "en"}]`
- `401` unauthenticated

### `POST /api/v1/children/`

Request (client-supplied `parent_id` is ignored; ownership comes from session):

```json
{"first_name": "Nadi", "age_band": "7-9", "language": "en"}
```

- `201` → child object; parent link is set server-side
- `400` missing/blank `first_name`, `age_band`, or `language`
- `401` unauthenticated, `403` CSRF failure

### `GET /api/v1/children/{id}/`

- `200` → child object (own) / `404` otherwise

### `PATCH /api/v1/children/{id}/`

Partial update; any subset of `first_name`, `age_band`, `language`.

- `200` → updated object / `404` (not owned, unknown, or malformed id)

### `DELETE /api/v1/children/{id}/`

- `204` / `404`

`PUT` and collection-level `DELETE` are **405** (not in the Phase 5 contract).

## Template route

- `GET /login/` — Django login form (field `username` holds the email), CSRF
  protected; authenticates via the session and redirects to `/`.
- `GET /` — `302` → `/login/` (anonymous) or `/health` (authenticated).
