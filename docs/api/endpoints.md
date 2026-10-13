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
  (the envelope itself stays mechanical, Phase 8 decision 5). On top of the
  envelope, the value must be a member of the frozen **semantic action
  contract** - case-insensitive match against `IDLE, WAVE, DANCE, NOD,
  TILT_HEAD, POINT, LISTENING_RETRY` (one central backend definition in
  `backend/core/semantic_actions.py`, docs/01 Section 19). The matched
  value is forwarded **verbatim** (never normalized), and raw servo/PWM/
  GPIO-style control can never pass this gate.
- `200` - `{"detail": "Command published."}` (broker accepted the publish;
  device-side execution ack arrives later as an `event`, never here)
- `400` - missing/blank/invalid `action`, non-string action, extra fields,
  or an unknown semantic action
  (`{"action": ["Unknown semantic action. Allowed: ..."]}`)
- `401` - unauthenticated, **or** a device Bearer token (this endpoint is
  session-only: device tokens cannot drive the command channel)
- `404` - malformed id, unknown robot, or robot not owned by the caller
  (admins may command any robot; parents only their own - identical 404
  for unknown vs not-owned; ownership resolves before body validation, so
  a foreign caller can never probe action validity)
- `405` - any method other than POST
- `429` - `{"detail": "Command repeated too quickly."}` (additive Phase A
  duplicate-command suppression: the same authenticated user repeated a
  command for the same robot inside `ROBOT_COMMAND_COOLDOWN_SECONDS`
  (default 1s, per-process); retry once the window has passed)
- `503` - `{"detail": "Robot command service unavailable."}` (MQTT
  transport failure; delivery is **unconfirmed**, not disproven. For a
  connection-level failure the message was never handed to a healthy
  broker session, but a QoS 1 acknowledgement timeout only means the
  broker's ack did not arrive within the publish window - the command may
  already have reached the robot. An immediate retry after a `503` can
  therefore cause a duplicate action.)

Successful **admin** commands append an `AuditLog` row
(`action="robot.commanded"`, `target=<robot id>`, actor + ip); parent
commands are not audited.

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

# Phase 9 Internal AI Service Reference

Base URL (compose network only): `http://ai-service:8000` - not published
on any host port. All request/response bodies are strict JSON: unknown
fields, wrong types, and text/audio contract violations return `422`
(`{"detail": [ ... ]}`).

## `GET /health`

Unauthenticated readiness probe (Section 28).

- `200` -> `{"status": "ok"}`

## `POST /v1/conversation/turn`

Internal service-to-service endpoint (Section 15). Requires
`Authorization: Bearer $AI_SERVICE_INTERNAL_TOKEN`.

Request (AI Input Contract, Section 15.2):

```json
{
  "conversation_id": "…",
  "child_profile_id": "…",
  "robot_id": "…",
  "text": "Hello",
  "audio": null,
  "language_hint": "en|bn|auto",
  "context": {
    "recent_turns": [{"role": "user", "text": "…"}],
    "memories": [{"key": "…", "value": "…"}]
  },
  "mode": "conversation|story|quiz"
}
```

- Exactly one of `text` (string) or `audio` (base64 string) is required -
  both or neither -> `422`.
- `context` is pre-fetched by Django (AR-5b): this service never reads the
  database.

Response (AI Output Contract, Section 15.3):

```json
{
  "conversation_id": "…",
  "text": "Hello! I am happy to talk with you.",
  "language": "en|bn",
  "emotion": "HAPPY|CURIOUS|THINKING|CONCERNED|NEUTRAL",
  "action": "WAVE|NOD|TILT_HEAD|IDLE|POINT|LISTENING_RETRY",
  "safety": {"flagged": false, "category": null, "action_taken": "ALLOWED"},
  "audio": null,
  "memory_writes": []
}
```

- `emotion`/`action` are semantic only - no servo/motor/GPIO/PWM/pin data
  ever appears (Section 19 / AR-17).
- `audio` is `null` while TTS is stubbed (Phase 9).

Errors:

- `401` - missing/malformed/invalid token; identical body every time:
  `{"detail": "Unauthorized."}` with `WWW-Authenticate: Bearer`
  (auth is checked before body validation, so an unauthenticated call
  never returns `422`).
- `422` - contract violation (unknown field, wrong type, text/audio XOR).
- `500` - controlled failure only: `{"detail": "Internal server error."}`;
  no stack traces, paths, or secrets are ever returned.

# Controller API Reference (control panel backend foundation)

Read APIs backing the Main Web Control Panel (docs/02 Phase 17); same base
path, auth, ID, and error conventions as earlier phases. All five are
`GET`-only (`POST`/`PUT`/`PATCH`/`DELETE` → `405`), session-authenticated
(`401` for anonymous **and** for device Bearer tokens - these are
human/session-only views), and **owner-scoped**: a robot not paired to the
caller - including for admins - returns `404`, following the existing
per-robot detail policy (fleet identity stays on `GET /devices/`).
Malformed and unknown ids are indistinguishable from non-owned ones.
`null` in any telemetry field means "not reported yet", never a fabricated
value. No endpoint ever exposes credentials, pairing material, or parent
fields.

### `GET /api/v1/devices/{id}/state/`

Live telemetry for the info view - exactly the fields `RobotState`/`Robot`
store (no invented fields):

```json
{"online": true, "state": "sleeping", "battery_percent": 76, "rssi": -63,
 "last_updated": "2026-10-03T10:15:00+06:00",
 "last_seen": "2026-10-03T10:15:00+06:00", "firmware_version": "1.2.3"}
```

A robot that has never reported state returns the same key set with the
telemetry fields `null` (`last_seen` null too, `firmware_version` still
present).

### `GET /api/v1/devices/{id}/events/`

Robot event history (diagnostics/lifecycle), newest first. Optional
`?limit=` - integer `1..100`, default `20`; anything else → `400
{"limit": [...]}`.

- `200` → `[{"event_type": "...", "payload": {...}, "timestamp": "..."}]`
  (empty list when there are no events)

### `GET /api/v1/devices/{id}/sensors/`

Sensor readings, newest first, same `?limit=` contract as events.

- `200` → `[{"sensor_type": "temperature", "value": 21.5, "timestamp": "..."}]`

### `GET /api/v1/devices/{id}/configuration/`

Read-only view of the robot's current `Configuration` version
(`is_current=true`); no write path exists on this endpoint.

- `200` → `{"version": 2, "data": {...}, "is_current": true}`; when no
  current configuration exists: `{"version": null, "data": null,
  "is_current": null}`
- `data` values under secret-marked keys (`password`, `token`, `secret`,
  `credential`, `api_key`, ...) are redacted to `"[redacted]"` before
  leaving the backend

### `GET /api/v1/devices/{id}/summary/`

One-shot assembly for the panel's first load - the same data the
individual endpoints above expose, composed in one response:

```json
{"robot": {…RobotSerializer…}, "state": {…state shape…},
 "events": [ …newest 10… ], "configuration": {…configuration shape…}}
```

# Phase 11 Conversation Turn Reference

The backend's single AI-calling boundary (docs/01 Section 15): one `POST`
turn endpoint that calls the internal AI service (above), persists the
turn, and falls back locally when that service fails. The backend never
talks to an AI provider - only the AI service does.

### `POST /api/v1/conversations/turn/`

`POST /api/v1/conversations/turn` (no trailing slash) is the same view,
for firmware clients.

Two accepted callers, one service path:

- **Session cookie** (`sessionid` + `X-CSRFToken`, same rules as every
  authenticated write): the caller must own **both** the robot and the
  child profile. Owner-scoped for every role - admins are not elevated
  (dashboard rule). Non-owned, unknown, or mismatched ids are
  indistinguishable `404`.
- **Device Bearer JWT** (Phase 7): the robot comes *only* from the token;
  a body naming a different robot, or an unpaired robot (no owner, so no
  child profile), is `404`. The child profile must belong to that robot's
  parent.

Request (JSON):

```json
{"robot_id": "…", "child_profile_id": "…",
 "conversation_id": "… (optional - continue an existing conversation)",
 "text": "Hello", "audio": "base64 (XOR with text)",
 "language_hint": "en|bn|auto or null", "mode": "conversation|story|quiz"}
```

- Exactly one of `text` or `audio` - both or neither → `400
  {"non_field_errors": ["Exactly one of 'text' or 'audio' must be
  provided."]}`; `audio` must be non-empty valid base64 (`400` under
  `audio`).
- **`context` is not accepted**: it is assembled server-side from this
  conversation's own recent turns (bounded; voice/blank-text turns
  skipped; `memories` stays `[]` until Phase 14), so client input can
  never inject history or memories (AR-5b).
- Unknown fields → `400 {"<field>": ["Unexpected field."]}`; malformed
  ids → `400 {"<field>": ["Not a valid id."]}`; missing ids → `400
  {"<field>": ["This field is required."]}`.

Order: authentication `401` → id syntax `400` → ownership `404` →
remaining envelope `400`. A `400` only ever describes the caller's own
input, never whether a resource exists.

Response - the frozen AI Output Contract (exactly 8 fields), always
`200`:

```json
{"conversation_id": "…", "text": "…", "language": "en|bn",
 "emotion": "HAPPY|CURIOUS|THINKING|CONCERNED|NEUTRAL",
 "action": "WAVE|NOD|TILT_HEAD|IDLE|POINT|LISTENING_RETRY",
 "safety": {"flagged": false, "category": null, "action_taken": "ALLOWED"},
 "audio": null, "memory_writes": [{"key": "…", "value": "…"}]}
```

Persisted per successful turn: child + robot `Message`, the robot
message's `AIInteraction` (latency, emotion, action, full safety object),
a `Conversation` row when `conversation_id` was omitted,
`Conversation.language` = the output language, `Memory` rows for the
accepted `memory_writes` (an entry whose key exceeds the 100-char column
is skipped, never failing the turn), and a `SafetyEvent` when
`safety.flagged` is true - linked to the child message, with the frozen
`category`/`action_taken` plus a derived display severity (`BLOCKED` →
`high`, `REDIRECTED` → `medium`, `ALLOWED_WITH_CAUTION`/`ALLOWED` →
`low`).

Hard-failure fallback (Section 15.3): when the AI service times out, is
unreachable, returns a non-200/invalid body, or the internal token is not
configured, the endpoint still answers `200` with the local fallback:

```json
{"conversation_id": "…", "text": "I'm having trouble thinking right now, ask me again in a bit.",
 "language": "en", "emotion": "CONCERNED", "action": "IDLE",
 "safety": null, "audio": null, "memory_writes": []}
```

The turn (child input + fallback reply) is still fully persisted and the
`AIInteraction` is marked failed via `safety_flags = {"failed": true,
"reason": "timeout|unavailable|http_error|invalid_response|config"}`.
`safety: null` means *not evaluated* - never a fabricated "allowed". No
`SafetyEvent` and no `Memory` rows are written on this path; there is no
retry loop.

Errors: `401` anonymous/device-token failures (single `{"detail": ...}`
body), `405` any method other than `POST`, plus the `400`/`404` cases
above. `429` when the caller exceeds the conversation-turn rate limit
(DRF default detail, e.g. `{"detail": "Request was throttled. Expected
available in N seconds."}`, plus a `Retry-After: <seconds>` header): a
fixed window per principal - device callers keyed by robot, session
callers by user; anonymous
requests are never IP-throttled (they still get `401`). The limit is
`CONVERSATION_TURN_RATE_LIMIT` (DRF rate syntax; default `30/min` =
30 per 60 s, per-process in-memory; empty disables) and is checked
before ownership resolution.

Raw child audio is forwarded to the AI service once and never stored:
`Message.audio_reference` stays empty until Phase 12 defines audio
transport storage.

# Phase 12 Audio Upload Reference (approved design — NOT implemented yet)

Documentation-only record (2026-10-13 decision record in
`docs/01-MASTER-SYSTEM-PLAN.md`). No such endpoint exists in the codebase
until its implementation is separately approved.

### `POST /api/v1/conversations/turn/audio/`

`POST /api/v1/conversations/turn/audio` (no trailing slash) is the same
view, for firmware clients.

- **Caller: device Bearer JWT only** (Phase 7). Session callers use the
  JSON turn endpoint above; an audio upload with a session cookie (or no
  credentials) is `401` and its body is never parsed - unless the
  pre-parse size gate has already answered `400` (see validation order
  below: that gate runs before authentication). The robot comes
  from the token; the child profile must belong to that robot's parent.
- **Throttle:** the same `CONVERSATION_TURN_RATE_LIMIT` bucket as the
  JSON turn (`30/min`, keyed by robot for device callers). Idempotent
  replays share this bucket - never a throttle-free path.
- **Request:** `multipart/form-data` with exactly one file part named
  `audio` (≤ **1,048,576 bytes / 1 MiB**, server-enforced) plus a
  `Conversation-Sidecar` header carrying JSON:
  `{"conversation_id": …, "language_hint": …, "child_profile_id": …}`
  (`mode` intentionally absent). The firmware additionally enforces a
  15 s maximum recording duration at capture.
- **`Idempotency-Key` header (optional):** when absent, the request
  proceeds normally with **no idempotent replay** (a retried request is
  processed again as a new request). When present: keyed per robot,
  retention TTL **600
  seconds (10 minutes)**. A duplicate with an identical payload within
  the TTL replays the stored response instead of reprocessing, and the
  replay response carries **`Idempotency-Replayed: true`**; the same
  robot/key reused with a different payload is **`409 Conflict`**
  (`{"detail": ...}`). Storage is process-local (in-memory),
  single-process exact. The check runs after authentication, throttle,
  request validation, and ownership (order below).
- **Validation order:** the header-only pre-parse `Content-Length` gate
  (WSGI middleware / ASGI wrapper) may reject a **missing, malformed,
  negative, oversized, or duplicate/conflicting `Content-Length` header**
  with `400` **before authentication** - a
  transport-size rejection that reveals nothing about credentials, so
  not every unauthenticated audio request receives `401`. Otherwise the
  intended authenticated flow is: authentication `401` -> throttle `429`
  -> request validation `400` (bad sidecar, missing/oversized `audio`
  file, id-syntax issues) -> ownership `404` -> idempotency replay
  (`200`, marked `Idempotency-Replayed: true`) or conflict (`409`) ->
  processing. Every rejection is a small fixed
  `{"detail"/"<field>": ...}` JSON body; oversized uploads are
  **`400`** (not `413`). Once past the gate, unauthenticated and
  throttled requests are rejected before any multipart parsing (no body
  read).
- **Response:** always the frozen 8-field Output Contract at `200`,
  including the Section 15.3 local fallback when the AI service hard-
  fails (identical rules to the JSON turn); `audio` in the response is
  capped at 1,400,000 base64 characters.
- **Privacy (application level):** the raw child audio is forwarded to
  the AI service once and is never persisted by the application to the
  database or logs; application-level upload handling keeps accepted
  uploads in memory only (`Message.audio_reference` stays empty; bounded
  in-memory upload handler, no temp files). **End-to-end disk-free
  handling is not yet verified** - it may only be claimed after the real
  deployment's proxy/server request-body buffering is inspected and
  configured: a reverse proxy must accept at least
  `AUDIO_UPLOAD_MAX_BODY_BYTES` (e.g. `client_max_body_size 2m`) **and**
  buffer request bodies in memory/tmpfs rather than on disk
  (`client_body_buffer_size` / `client_body_temp_path` - see
  `docs/backend/architecture.md`). Accepted input stays **1 MiB** and
  oversized stays **`400`**.
