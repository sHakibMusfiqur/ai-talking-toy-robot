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

---

# Phase 6 API Reference (devices)

Same base path, auth, ID, and error conventions as Phase 5.

### `POST /api/v1/devices/register/`

Device self-registration (Flow 4). **Unauthenticated by design** — the device
has no credentials yet; Phase 7 layers challenge/token auth on the stored
public key.

Request:

```json
{"device_id": "<uuid>", "public_key": "-----BEGIN PUBLIC KEY-----...", "firmware_version": "1.2.3"}
```

- `201` → `{"id", "device_id", "pairing_status", "pairing_code"}` — the raw
  pairing code is returned **exactly once** to the registering device
- `400` → `{"device_id": [...]}` invalid UUID / `{"public_key": [...]}` not
  PEM (`-----BEGIN PUBLIC KEY-----` … `-----END PUBLIC KEY-----`)
- `409` → `{"detail": "Device already registered."}` — duplicate `device_id`
  never re-issues a pairing code

`device_id` is normalized to lowercase canonical UUID form. `POST
/api/v1/devices/register` (no trailing slash) is also accepted: the Phase 6
spec writes the path without a slash while Phase 5 convention uses one, and a
POST redirect would be unsafe, so both resolve directly.

### `POST /api/v1/devices/pair/`

Dashboard-side pairing — authenticated session required (parent account).
Claims **exactly one** robot by entering that robot's own pairing code.

Request (client-supplied `parent_id`/`owner_id` are ignored; ownership comes
from the session):

```json
{"pairing_code": "K7M2QX9B", "nickname": "Ayaan's Robo"}
```

- `200` → robot object (`{"id", "device_id", "nickname", "pairing_status",
  "firmware_version", "last_seen", "paired_at"}`) — no secrets
- `400` → `{"detail": "Invalid pairing code."}` — **identical** for invalid,
  expired, and already-used codes (single-use; no replay; no information leak)
- `401` unauthenticated, `403` CSRF failure

Pairing is explicit, per-robot, and non-cascading: pairing Robot A grants no
access to Robot B; each robot needs its own pairing operation. Codes are
8 characters from a 31-symbol unambiguous alphabet, valid 900 s
(`DEVICE_PAIRING_CODE_LENGTH`, `DEVICE_PAIRING_CODE_TTL_SECONDS`), stored
SHA-256-hashed only, consumed atomically. `POST /api/v1/devices/pair` (no
trailing slash) is also accepted.

### `GET /api/v1/devices/`

Admin-only fleet list (role `admin`).

- `200` → array of robot objects
- `401` unauthenticated, `403` non-admin role (`{"detail": "Admin access
  required."}`)

### `GET /api/v1/devices/{id}/`

Owner-scoped robot detail (Phase 6 decision: smallest endpoint that makes
ownership boundaries observable; used by the control panel later).

- `200` → robot object (paired to caller)
- `404` → not owned, unknown, or malformed id (indistinguishable)
- `405` → `PUT`/`PATCH`/`DELETE` are not in the Phase 6 contract

# Phase 7 API Reference (device authentication)

Same base path, ID, and error conventions as earlier phases. Phase 7 adds the
device signed-challenge ? JWT exchange (AR-9/AR-10). Two fully separate auth
domains now exist:

- **Humans**: session cookie + CSRF (unchanged; no human JWT in Phase 7).
- **Devices**: `Authorization: Bearer <device JWT>` � accepted only on
  device-facing views; never on human endpoints, and sessions are never
  accepted on device endpoints.

Every device credential failure returns the **same** `401 {"detail": "Invalid
credentials."}` � unknown device, unknown/expired/replayed challenge, bad
signature, malformed/expired/tampered token � with no timing or content
distinction about stored state.

### `POST /api/v1/devices/auth/challenge/`

Step 1: request a short-lived server nonce. **No token required** (the device
proves nothing yet) � but `device_id` must belong to a registered robot.

Request:

```json
{"device_id": "<uuid>"}
```

- `200` ? `{"challenge_id", "challenge", "expires_in"}` � raw challenge is
  returned exactly once; only its SHA-256 hash is stored
- `400` ? `{"device_id": [...]}` malformed UUID
- `401` ? `{"detail": "Invalid credentials."}` � identical for unknown and
  malformed-UUID-after-normalization device ids

Challenges expire after `DEVICE_CHALLENGE_TTL_SECONDS` (default 60 s) and
issuing a new one atomically invalidates the robot's previous unconsumed
challenge (one live challenge per robot). Trailing slash only.

### `POST /api/v1/devices/auth/token/`

Step 2: exchange a **signed** challenge for a short-lived device JWT. The
device signs the raw challenge string with its private key (kept on-device,
never transmitted) using the algorithm implied by its registered public key:
EC ? ECDSA-SHA256 (DER or raw 64-byte `r||s`, ESP32/mbedTLS-compatible),
RSA ? PKCS#1 v1.5 SHA-256. Signature is base64.

Request:

```json
{"device_id": "<uuid>", "challenge_id": "<objectid>", "challenge": "<hex>", "signature": "<base64>"}
```

- `200` ? `{"access_token", "token_type": "Bearer", "expires_in"}` � the
  token is the only secret in the response
- `400` ? missing/blank fields, malformed `device_id`
- `401` ? `{"detail": "Invalid credentials."}` � uniform across all faults;
  challenges are single-use (replay ? 401), consumed atomically

JWT: HS256, claims `sub` = robot ObjectId, `iss`/`aud` from settings, `iat`,
`exp` (`DEVICE_JWT_TTL_SECONDS`, default 3600). Trailing slash only.

### `GET /api/v1/devices/me/`

Authenticated device reads exactly its own robot identity (the principal
comes solely from the token � client-supplied ids in query/body/headers
cannot switch context).

- `200` ? robot object (same shape as Phase 6; no parent/owner or credential
  fields � pairing status is included but never the parent)
- `401` ? missing header (`{"detail": "Authentication credentials were not
  provided."}`) or any invalid token (`{"detail": "Invalid credentials."}`);
  `WWW-Authenticate: Bearer` is included
- `405` ? write methods

Pairing is deliberately **not** required to authenticate: the token proves
machine identity, pairing proves ownership. An unpaired robot can call
`me/` and sees `pairing_status: "unpaired"`.

# Phase 8 API Reference (MQTT command origin)

## `POST /api/v1/devices/{id}/command/`

Session-authenticated command publish (Section 26: parent/admin as the
authenticated, ownership-checked origin). The view validates the envelope
and calls `mqtt_bridge.publish_command`; it never touches the MQTT client
directly. Publishes to `robots/{device_id}/command` (QoS 1, not retained).

Request body:

```json
{"action": "wave"}
```

- `action`: 1-64 chars, `[A-Za-z0-9_.-]` only; no other fields allowed
  (Phase 8 is mechanical delivery only - Phase 13 defines semantics).
- `200` - `{"detail": "Command published."}` (broker accepted the publish;
  device-side execution ack arrives later as an `event`, never here)
- `400` - missing/blank/invalid `action`, non-string action, extra fields
- `401` - unauthenticated, **or** a device Bearer token (this endpoint is
  session-only: device tokens cannot drive the command channel)
- `404` - malformed id, unknown robot, or robot not owned by the caller
  (admins may command any robot; parents only their own - identical 404
  for unknown vs not-owned)
- `405` - any method other than POST
- `503` - `{"detail": "Robot command service unavailable."}` (MQTT transport
  failure; nothing was delivered)

## MQTT topic contract (frozen, Section 11.1)

| Topic | Publisher | Subscriber | QoS | Retained |
|---|---|---|---|---|
| `robots/{device_id}/status` | device | django-service | 1 | yes |
| `robots/{device_id}/heartbeat` | device (LWT-backed) | django-service | 0 | no |
| `robots/{device_id}/event` | device | django-service | 1 | no |
| `robots/{device_id}/command` | django-service | device | 1 | no |
| `robots/{device_id}/config/notify` | django-service | device | 1 | yes |
| `robots/{device_id}/ota/notify` | django-service | device | 1 | yes |
| `robots/{device_id}/content/notify` | django-service | device | 1 | yes |

Payloads (validated strictly, unknown fields rejected, identity always
comes from the topic - never the payload):

- `status`: `{"online": bool, "state": str<=32, "battery_percent": 0-100,
  "rssi": -300..300}` - any subset; omitted fields keep prior values.
- `heartbeat`: empty or `{}` (Section 11 defines no fields; any field is
  rejected until the frozen contract adds one).
- `event`: `{"event_type": "[A-Za-z0-9_.-]{1,64}", "payload": {object}}`.
- `command`: `{"action": "<name>"}`.
- `*/notify`: `{"version": <int>=0}` (retained version marker).
- LWT: `{"online": false}` (retained, device-set at connect).

## Broker authorization (Section 11.2, enforced by Mosquitto dynsec)

- Device client: username = `device_id`, password = the device's current
  Phase 7 JWT (provisioned at token exchange, rotated on each exchange,
  best-effort - exchange never fails when the broker is down).
- `device` role: publish only `robots/%u/{status,heartbeat,event}`;
  subscribe/receive only `robots/%u/{command,config/notify,ota/notify,
  content/notify}`.
- `django-service` role: publish only `robots/+/{command,config/notify,
  ota/notify,content/notify}`; subscribe/receive only
  `robots/+/{status,heartbeat,event}`.
- Defaults deny publish/subscribe/receive; no client ever holds a `#`
  publish right; `allow_anonymous false`. Cross-device publishes are
  **dropped by the broker** (delivery-based tests), subscribe denials
  return SUBACK 0x80, credential failures return CONNACK "Not authorised".
