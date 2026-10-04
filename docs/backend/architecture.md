# Phase 5 Backend Architecture

## Stack

- Django 5.2.17 + `django-mongodb-backend` 5.2.4 (MongoDB database backend)
- Django REST Framework 3.18.1 (session auth only, JSON renderer)
- pymongo 4.18.2; MongoDB 7.0 single-node replica set (`rs0`)
- Python 3.11.9; tests: pytest 9.1.1 + pytest-django 4.14.0 against a real
  MongoDB test database (no mocks for DB behavior)

## Layout

```
backend/
  manage.py
  pytest.ini                 # DJANGO_SETTINGS_MODULE=config.settings
  conftest.py                # api_client fixture (DRF APIClient)
  config/                    # settings, urls, wsgi/asgi
  core/                      # health/root views, error handler, AuthRequired,
                             # Mongo-aware AppConfigs, tests
  accounts/                  # User/Parent/ChildProfile models, auth + children
                             # API, serializers, services, tests
  mongo_migrations/          # generated contrib migrations (auth, contenttypes)
  templates/registration/    # login.html
```

## MongoDB-specific decisions

- `DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"` —
  all PKs are ObjectIds exposed as JSON strings.
- `MIGRATION_MODULES` redirects `auth` → `mongo_migrations.auth` and
  `contenttypes` → `mongo_migrations.contenttypes` (contrib migrations are
  generated, not shipped). `sessions` keeps its built-in migration (String PK).
- Transactions: `accounts.services.create_parent_account` uses
  `django_mongodb_backend.transaction.atomic()` (Mongo-transactions-aware;
  Django's `django.db.transaction` is unsupported by this backend). Requires
  the replica set — single-node `rs0` is provisioned by `infra/mongo/healthcheck.js`.
- `features.supports_transactions = False` in test settings path: Django
  `TestCase` skips its atomic wrapper, so both pytest-django modes work; signup
  atomicity is covered by dedicated `transaction=True` tests that verify both
  creation and rollback on the real replica set.
- No integer counters, no slug uniqueness — email is the only unique natural key.

## Auth & security

- `AUTH_USER_MODEL = "accounts.User"`; email is `USERNAME_FIELD` (unique);
  role field (`parent`/`admin`, default `parent`).
- DRF: `SessionAuthentication` + `JSONRenderer` only; default permission class
  is `core.permissions.AuthRequired` (IsAuthenticated), so unauthenticated
  requests return **401** (custom `core.exceptions.api_exception_handler`
  converts DRF's default 403 for session auth).
- CSRF: enforced by DRF for every session-authenticated write.
- Child access: ownership-filtered querysets (`ParentOwnershipMixin`) —
  non-owned ids behave exactly like missing ids (404).
- Passwords: Django's default four validators; no custom rules.
- Emails normalized (`.strip().lower()`) at signup and login.

## Running

```bash
# Mongo only (dev against host venv)
docker compose --env-file .env -f infra/docker-compose.yml up -d mongo

# full stack (migrate + runserver on :8000)
docker compose --env-file .env -f infra/docker-compose.yml up -d --build

# local commands (repo root)
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py migrate
# tests (from backend/)
..\.venv\Scripts\python.exe -m pytest
```

Test DB: `test_ai_talking_toy` on the same replica set, created/dropped by
pytest-django; per-test cleanup uses flush (migration records preserved).

## Out of scope (Phase 5)

devices/, MQTT, conversations/, AI service, dashboards, OTA, firmware,
production WSGI server (dev server only at this phase).

---

# Phase 6 Backend Architecture (addendum)

## Apps & schema

Models live in their eventual apps per `docs/01` §12.1 (full Section 13
schema migrated up front):

```
devices/          Robot, DeviceCredential, RobotState, RobotEvent, SensorData
                  + registration/pairing services, API, tests
conversations/    Conversation, Message, AIInteraction   (models only)
memory/           Memory                                 (models only)
configuration/    Configuration                          (models only)
firmware/         Firmware, OTAUpdate                    (models only)
safety/           SafetyEvent                            (models only)
analytics/        UsageLog, AuditLog + record_audit()
```

`content/` gets no app yet (no Section 13 entity). All PKs remain ObjectIds;
`Configuration.version` is domain data, not an identifier.

## Registration & pairing (Flow 4)

- `devices.services.register_device` / `pair_robot` are the **only**
  state-changing paths; views are thin (REST today, MQTT bridge reuses the
  same functions later).
- Multi-document writes (`Robot` + `DeviceCredential` + `AuditLog`) use
  `django_mongodb_backend.transaction.atomic()` on the `rs0` replica set.
- Pairing codes: 8 chars from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`
  (settings-driven: `DEVICE_PAIRING_CODE_LENGTH`,
  `DEVICE_PAIRING_CODE_TTL_SECONDS`, default 900 s), generated with
  `secrets`, stored **SHA-256 only**, single-use, consumed atomically with a
  check-and-set update; invalid/expired/used all return the same generic
  `{"detail": "Invalid pairing code."}`. Raw codes are never logged and never
  re-returned after registration.
- Ownership (`Robot.parent`) is set only server-side from the authenticated
  session during pairing — client-supplied `parent_id`/`owner_id` are
  ignored. Detail reads filter by owner (404 for everything else).
- Registration is unauthenticated in Phase 6 (device has no credentials
  yet); duplicate `device_id` → 409. Phase 7 adds challenge/token auth on the
  stored public key.
- `AuditLog` rows: `robot.registered` (actor null) and `robot.paired`
  (actor = pairing user), target = robot id only.

## Phase 6 out of scope (unchanged later-phase boundaries)

No device JWT/challenge auth (Phase 7), no MQTT (Phase 8), no conversation/AI
processing (Phase 9–12), no OTA execution, no dashboards, no firmware.

# Phase 7 Backend Architecture (addendum)

## Device authentication (AR-9 / AR-10)

```
devices/models.py       + DeviceChallenge (migration 0002_devicechallenge)
devices/services.py     + create_device_challenge, exchange_device_token,
                          _verify_device_signature, sha256_hex
devices/tokens.py         issue_device_token / decode_device_token (PyJWT)
devices/authentication.py DeviceJWTAuthentication (Bearer, per-view)
devices/permissions.py    + DeviceAuthenticated
```

- `DeviceChallenge`: one row per issued nonce � `challenge_hash` (SHA-256
  only, raw never stored), `expires_at` (`DEVICE_CHALLENGE_TTL_SECONDS`,
  default 60 s), `consumed_at`. Issuing invalidates the robot's previous
  unconsumed challenge and prunes expired rows (bounded storage).
- Exchange verifies the signature **before** consuming the challenge
  (retries within TTL are allowed; replay after success is impossible),
  then consumes via check-and-set update inside
  `django_mongodb_backend.transaction.atomic()` together with issuing the
  token and updating `DeviceCredential.token_hash`/`token_expires_at`
  (record-only state: validation is stateless).
- Signature verification (`cryptography`): PEM SubjectPublicKeyInfo; EC ?
  ECDSA-SHA256 accepting DER **or** raw 64-byte `r||s` (ESP32/mbedTLS),
  RSA ? PKCS#1 v1.5 SHA-256; malformed material is a plain `False`, never a
  500.
- JWT (`PyJWT`): HS256, secret `DEVICE_JWT_SECRET` (falls back to
  `SECRET_KEY` for dev; production sets an independent =32-char value),
  `iss`/`aud`/`exp`/`iat`/`sub` all required and validated on decode. Token
  validation is stateless � revocation = delete the Robot (tested).
- `DeviceJWTAuthentication` is attached **per-view** (`authentication_classes`
  on device views only), never to `DEFAULT_AUTHENTICATION_CLASSES`, so a
  device Bearer cannot authenticate any human endpoint and a dashboard
  session cannot authenticate a device endpoint. It resolves `request.auth`
  as `{"robot": <Robot>, "payload": <claims>}` � client-supplied
  `robot_id`/`device_id` are never consulted (tested).
- `authenticate_header()` returns `Bearer` (proper `WWW-Authenticate`);
  `core.exceptions.api_exception_handler` also forces 401 for
  `AuthenticationFailed` so the uniform 401 contract cannot degrade to 403.
- Audit categories (target = robot id only; never challenge, signature, or
  token material): `device.token_issued`, `device.auth_failed.challenge`,
  `device.auth_failed.signature`. Unknown-device attempts write **no** audit
  row (anti-probing; enumeration/rate limiting is Phase 19).
- New pinned deps: `PyJWT==2.15.1`, `cryptography==50.0.1`.

## Phase 7 decisions (recorded)

1. **No refresh endpoint** � devices re-run challenge/token within the TTL
   window (�26's "proactive refresh" is firmware-side: exchange again).
2. **Authentication ? pairing** � challenge/token require registration only;
   pairing remains the separate ownership act.
3. **No human JWT in Phase 7** � dashboard keeps session+CSRF until the
   SPA/API decision point (Phase 17); two auth domains stay disjoint.
4. **Trailing slash only** for Phase 7 paths � the Phase 6 dual-slash
   aliases were a one-off spec conflict, not a pattern to extend.
5. **Challenge issued to unpaired robots** � a registered device can
   authenticate before being claimed; `me/` exposes no owner data.
6. **Signature payload** is the raw challenge UTF-8 bytes (no DER wrapping,
   no prefixes) � simplest for firmware.

## Phase 7 out of scope (unchanged later-phase boundaries)

No MQTT (Phase 8), no conversation/AI processing (Phase 9�12), no OTA
execution, no dashboards, no firmware implementation, no rate limiting of
auth endpoints (Phase 19).

## Phase 8 MQTT bridge (Sections 11, 12.2)

`backend/mqtt_bridge/` is a thin translation layer (one process command:
`manage.py run_mqtt_bridge`):

- `topics.py` - the ONLY place topic strings are built/parsed (frozen
  Section 11.1 shapes; wildcard/slash device ids rejected; QoS/retain
  constants asserted against the plan literals by tests).
- `payloads.py` - strict inbound/outbound payload validation (unknown
  fields rejected, JSON types enforced without coercion, 8 KiB cap).
- `client.py` - paho-mqtt 2.1.0 wrapper (CallbackAPIVersion.VERSION2),
  bounded connect/publish timeouts, auto-reconnect with
  `reconnect_delay_set(min,max)`, deterministic `backoff_delay()` for
  initial connect, `simulate_network_drop()` test hook.
- `provisioning.py` - dynsec control API (`$CONTROL/dynamic-security/v1`,
  `{"commands": [...]}` request / `responses` reply) using short-lived
  admin clients; `ensure_device_client` (create/rotate + `device` role)
  and `ensure_bridge_client` (`django-service` role).
- `subscriber.py` - broker -> validate -> `devices.services` calls only
  (`update_status` / `record_heartbeat` / `record_robot_event`) - the
  same single write path Section 12.2 mandates for REST; never writes
  models directly, never logs raw payloads, drops malformed/unknown-device
  messages with topic/category-only warnings.
- `publisher.py` - lazy singleton; views/services call
  `publish_command(robot, payload)` and `publish_{config,ota,content}_notify(robot, version)`.

New service functions in `devices/services.py` (single write path for both
transports): `update_status` (partial update, refreshes `last_seen`),
`record_heartbeat` (marks online + `last_seen`), `record_robot_event`
(creates `RobotEvent` + `last_seen`). The Phase 7 token exchange now calls
`_provision_mqtt_client(device_id, token)` OUTSIDE its transaction
(network I/O never holds a Mongo transaction open) and swallows failures
(token exchange never fails because the broker is unavailable).

`POST /api/v1/devices/{id}/command/` (`devices.views.RobotCommandView`)
is the Phase 8 command origin: session-only, owner-scoped (admins any
robot), 404 for unknown/not-owned, 503 on MQTT transport failure
(`RobotCommandUnavailable`).

Infrastructure: `infra/mosquitto/` (mosquitto.conf + idempotent
docker-entrypoint.sh role bootstrap via `mosquitto_ctrl`), Compose service
with `MOSQUITTO_DYNSEC_PASSWORD: ${MQTT_ADMIN_PASSWORD}` and a
`mosquitto_ctrl dynsec listRoles` healthcheck; settings gained `MQTT_*`
(host/port, bridge+admin credentials, reconnect bounds, timeouts).

Tests (87 new, all against the live broker - no mocked ACL): delivery-
based cross-device negatives (a denied QoS-1 publish may still PUBACK but
is never delivered), SUBACK 0x80 subscribe denials, LWT offline ingestion
through `update_status`, reconnect/resubscribe (socket drop + full
`docker restart` of the broker), strict payload rejection, service-layer
routing spies, REST authorization matrix, and Section 26 end-to-end
(JWT usable as MQTT password immediately after exchange). Under pytest
only, `settings.py` loads `MQTT_*` defaults from the repo-root `.env`.

## Phase 8 decisions (recorded, 2026-09-30)

1. **Token-derived MQTT credentials (Section 26)**: username = `device_id`,
   password = the current device JWT; broker user provisioned best-effort at
   token exchange (create/rotate + role). No new credential material appears
   in any API response; rotation = next exchange.
2. **clientId is not pinned** to `device_id` by the backend (ACL scoping is
   username-based); firmware may choose any unique client id.
3. **Deny-by-default receive** (`setDefaultACLAccess publishClientReceive
   deny`) plus explicit denies for send/subscribe in the role bootstrap.
4. **Denied-publish semantics**: Mosquitto may PUBACK a `publishClientSend`
   violation and still drop the message - all security assertions are
   delivery-based (plus SUBACK codes / CONNACK refusals), never PUBACK-based.
5. **Command envelope** is `{"action": str}` only (mechanical); Phase 13
   defines action semantics. Commands are logged/acked, never executed.
6. **Heartbeat payload contract**: empty/`{}` only, per the frozen Section 11
   (rejects unknown fields rather than silently ignoring them).
7. **Strict JSON types** for status/command payloads (no DRF coercion, e.g.
   `"yes"` is not a boolean, `123` is not an action string).
8. **Session-only command endpoint**: device Bearer tokens are rejected from
   `/command/` (401) - humans on the dashboard are the command origin.
9. **QoS/retain exactly per Section 11.1** - asserted as contract tests so
   drift fails the suite.

## Phase 8 out of scope (unchanged later-phase boundaries)

No firmware `network/mqtt` implementation (Part 1, separately owned -
firmware must connect with `device_id` credentials, set the retained
LWT, re-subscribe and re-publish status on reconnect); no command
execution/actions (Phase 13); no dashboard UI for status (Phase 17); no
OTA/config/content payload design beyond the notify version marker
(Phases 14-16); no AI/conversation integration (Phase 11).

# Phase 9 AI Service Foundation (addendum)

Standalone FastAPI service in `ai_service/` (Section 14.3 layout). Separate
process/network from Django; Django remains the only component that talks
to end users/devices, and from Phase 11 on the only component that calls
this service.

## Service surface

- `GET /health` - readiness probe (Section 28), unauthenticated.
- `POST /v1/conversation/turn` - internal AI endpoint (Section 15);
  `Authorization: Bearer $AI_SERVICE_INTERNAL_TOKEN` required.
- OpenAPI tag `internal`; global exception handler returns a controlled
  `{"detail": "Internal server error."}` (no stack traces, paths, tokens).
- Lifespan fails fast when `AI_SERVICE_INTERNAL_TOKEN` is unset; auth runs
  before body validation, so unauthenticated requests always get `401`
  (never `422`), with one uniform body across missing/malformed/invalid
  token failures.

## Stack & layout

Python 3.11 + FastAPI + Pydantic v2, pinned in `ai_service/requirements.txt`
(`fastapi==0.142.2`, `uvicorn==0.54.0`); dev/test in `requirements-dev.txt`
(`httpx`, `pytest`). Flat top-level packages (`api/`, `contract/`, stage
dirs) so `api.main:app` imports identically in the host venv and the
container. Docker image `python:3.11.9-slim`, uvicorn on `0.0.0.0:8000`
inside the compose network only - **no host port is published**; Django
will reach `http://ai-service:8000` from Phase 11.

## Contracts (documented concretizations of Section 15)

- Input: strict `extra="forbid"` model; exactly one of `text` (string) or
  `audio` (base64 string, validated) - both/neither = `422`;
  `context` (pre-fetched recent turns + memories, AR-5b) required;
  `language_hint` (`en|bn|auto`) and `mode` (`conversation|story|quiz`)
  optional with defaults.
- Output: exactly the 8 frozen fields; `safety` is the Section 15.3 object
  (`flagged`/`category`/`action_taken`); `audio` is `null` while TTS is
  stubbed; `memory_writes` is `[{key, value}]`.
- Frozen enums: emotion `HAPPY|CURIOUS|THINKING|CONCERNED|NEUTRAL`, action
  `WAVE|NOD|TILT_HEAD|IDLE|POINT|LISTENING_RETRY`, safety category
  `PERSONAL_INFO_REQUEST|UNSAFE_TOPIC`, safety action
  `ALLOWED|REDIRECTED|BLOCKED|ALLOWED_WITH_CAUTION`. Semantic-only: servo/
  motor/GPIO/PWM/LED-pin keys are rejected by schema and scanned for in
  tests.

## Deterministic mock pipeline (Phase 9)

`contract/pipeline.run_pipeline` orchestration with injectable stages
(`PipelineStages`): STT -> understanding -> context -> safety(pre) ->
LLM -> safety(post) -> emotion -> action -> assembler -> TTS stub.
Deterministic mocks: transcript `"Hello"` (conf 0.99), fixed reply
`"Hello! I am happy to talk with you."`, `HAPPY`/`WAVE`, audio `null`.
Boundary fallbacks: STT confidence < 0.5 -> fixed listening-retry text,
`NEUTRAL`/`LISTENING_RETRY`, `safety=null`, LLM skipped; any safety flag
(pre or post) -> fixed redirect text, `NEUTRAL`/`IDLE`, LLM output
discarded. Both safety stages are always routed (single audit path).

## Phase 9 decisions (recorded, 2026-10-01)

1. `AI_SERVICE_INTERNAL_TOKEN` env var, compared with `hmac.compare_digest`;
   empty/missing env refuses to start (no development bypass).
2. Audio travels as base64 in JSON for this internal endpoint; the contract
   still enforces text XOR audio.
3. `ALLOWED` added to `SafetyAction` so unflagged turns carry the full
   Section 15.3 shape (`category` stays `null`).
4. Network isolation test fixture denies external connects/DNS only and
   allows loopback (asyncio's Windows event loop needs it).

## Phase 9 out of scope (unchanged later-phase boundaries)

No real STT/LLM/TTS provider, provider SDK, or API key (Phase 10); no
language detection (`lang_detect/`, Phase 10); no Django integration -
no `AIInteraction` persistence, no robot->Django->AI call path (Phase 11);
no streaming/websockets/SSE; no MongoDB access from this service; no OTA,
dashboard, or firmware work.

# Phase 10 (current) - Provider-Agnostic Integration Architecture (addendum)

**Phase 10 currently provides provider-agnostic integration architecture
and deterministic implementations. Real provider selection and provider
implementation are intentionally deferred** (project-owner decision).
The frozen Section 15 Input/Output Contract, Section 25 internal auth,
the semantic Emotion/Action enums, the API route, and all security
boundaries are unchanged.

## Structure (provider adapter rule)

Provider-capable stages now follow the layout `interface.py` +
`mock.py` + `providers/` (empty, for future adapters):

- `stt/`, `llm/`, `tts/` - `interface.py` Protocols, deterministic
  `mock.py`, `providers/` placeholder packages.
- `safety/` - `interface.py` (`SafetyResult`, combined `SafetyStage`)
  plus explicit halves: `pre.py` (pre-LLM gate), `post.py` (post-LLM,
  pre-TTS gate). Order is fixed: input -> pre-safety -> LLM ->
  post-safety -> TTS. Unsafe LLM output never reaches TTS.
- `lang_detect/` (new, additive) - provider/library-agnostic
  `LangDetectStage` resolving per-utterance language (AR-12): explicit
  `language_hint` wins, otherwise deterministic script detection
  (Bengali block -> `bn`, else `en`). No library, no external service.
- `understanding/`, `emotion/`, `action/`, `context/` - unchanged
  Phase 9 interfaces; context remains request-provided only (AR-5b).

Provider-specific code must live behind adapters in `providers/` - never
in `pipeline.py`, `api/main.py`, `contract/schemas.py`, or
`contract/enums.py`. The pipeline depends only on injected Protocols
(`PipelineStages`), so a future adapter swaps in without pipeline,
contract, or API changes.

## Neutral configuration & retry (`config.py`, `retry.py`, `errors.py`)

- `config.py`: provider-neutral env keys per component
  (`AI_{STT,LLM,TTS}_{PROVIDER,MODEL,ENDPOINT,TIMEOUT_SECONDS,RETRY_COUNT,RETRY_BACKOFF_SECONDS}`).
  Defaults: provider `mock`, timeout 30s, retry count 1, backoff 0.5s.
  Missing/empty env = safe defaults (pipeline runs with zero
  configuration/credentials); malformed numerics raise `ValueError`
  naming the key only (values never echoed).
- `errors.py`: provider-neutral `ProviderError`.
- `retry.py`: `RetryPolicy` + `run_with_retry` - retries
  `ProviderError`/`TimeoutError` with exponential backoff, injectable
  `sleep` (testable offline, no network I/O of its own).

## Fallback extension points (contract-respecting, no new output fields)

- STT provider failure -> retry once, then the Section 15.1
  listening-retry fallback (`safety: null`).
- LLM provider failure -> retry once, then a generic safe text with
  `NEUTRAL`/`IDLE` and an unflagged safety object (Section 16).
- TTS provider failure -> degraded text-only response (`audio: null`).
- Safety stage failure -> fail closed: approved redirect text,
  `flagged: true`, `action_taken: BLOCKED`, LLM never executed.
- All other behavior (low-confidence STT fallback, pre/post safety
  flag fallbacks, controlled 500s) is exactly Phase 9.

## Tests

Phase 10 architecture suite (`tests/test_provider_architecture.py`,
`tests/test_provider_failures.py`): full-pipeline injection of
non-mock implementations, lang_detect behavior, neutral config
loading, no-SDK/no-credential checks, retry mechanics, all four
failure fallbacks, and TTS-spy proof that unsafe LLM output never
reaches TTS - all offline (`block_network`).

## Phase 10 step 3 - deterministic two-stage safety package (addendum)

Owner-approved interim scope while real providers remain deferred
(no provider, no SDK, no credential, no network - fully offline).

- `safety/rules.py` - `RuleBasedSafety`, the deterministic default
  `SafetyStage`, now wired into `default_stages()` (replacing
  `MockSafety` as the default; `mock.py` remains for pinned tests).
  - Pre-LLM check (Section 14.2.1) runs on transcript + understood
    intent; a flag stops the pipeline before the LLM and answers with
    the approved redirect (`action_taken: REDIRECTED`).
  - Post-LLM check (Section 14.2.2) is the last gate before TTS; a
    flag replaces the draft (`action_taken: BLOCKED`) - flagged output
    is never returned or spoken (Section 18).
  - Category mapping uses the frozen Phase 0 taxonomy only:
    `PERSONAL_INFO_REQUEST` (Section 17.1 hard rule - address, school
    name, exact location, contact details: always blocked/redirected,
    any phrasing, both languages) and `UNSAFE_TOPIC` (age-inappropriate
    requests, adult content, violence, dangerous instructions,
    self-harm-adjacent content, external links, unsafe challenges,
    manipulation/grooming patterns). A finer-grained taxonomy would be
    a frozen-contract extension and needs an owner decision record.
  - Coverage is bilingual (English patterns + Bangla Unicode
    patterns); URL/link detection enforces the Section 17.1 "no links
    in robot responses" contract rule at both stages.
  - Precedence: PII hard rule > unsafe topic > external link.
    Intents `personal_info_request` / `unsafe_request` are honored
    pre-generation (the understanding-adapter signal; text patterns are
    the primary signal today).
- Canonical fixture set: `docs/testing/child-safety-fixtures.json`
  (versioned: fixture `version` + `taxonomy_version`) with required
  risk-area minimums per Section 31 levels 6/14; pass/fail history in
  `docs/testing/SAFETY-FIXTURE-RESULTS.md`.
- Tests (`tests/test_safety_fixtures.py`): 100%-categorization gate
  over every fixture, coverage-shrink guard, bilingual/both-stage
  assertions, full-pipeline proof that pre-flagged input never reaches
  the LLM and that flagged post-stage drafts never reach TTS (only the
  approved fallback is ever spoken), PII-precedence, and intent-signal
  tests. The rule engine passes the existing no-provider/no-network
  static scans unchanged.

## Phase 10 out of scope (unchanged later-phase boundaries)

No real STT/LLM/TTS provider, provider SDK, provider account, or
credential (the deterministic safety filter is rule-based, not a
provider); no `lang_detect` library; no Django integration (Phase 11);
no ESP32/firmware work; no public port; no contract change. Real
provider selection remains an owner/AI-Developer decision. Expanding
the safety taxonomy beyond the frozen two values is an owner decision.

# Controller API (control panel backend foundation, addendum)

Backend-only foundation for the Main Web Control Panel (docs/02 Phase 17):
semantic command handling on the existing endpoint plus owner-scoped read
APIs. No UI, no new models, no migrations.

## Command semantics (Section 19 pass-through)

1. **One central definition**: `core/semantic_actions.py` holds
   `SEMANTIC_ACTIONS` = the frozen AI `Action` enum (`IDLE, WAVE, NOD,
   TILT_HEAD, POINT, LISTENING_RETRY`, docs/01 Section 19 /
   `ai_service/contract/enums.py`) **+ `DANCE`** - the controller-origin
   addition required by the existing Phase 8 command tests and the Phase
   17 quick actions. Extending this set is a frozen-contract change and
   needs an owner decision record.
2. **Where it is enforced**: `RobotCommandView` validates envelope shape
   (`DeviceCommandSerializer`, mechanical - unchanged) and *then*
   semantic membership, before publishing. The shared serializer/publisher
   path stays mechanical (Phase 8 decision 5; recording tests publish
   arbitrary names), so mqtt_bridge remains transport-only.
3. **Case-insensitive, verbatim pass-through**: `wave`/`WAVE`/`Wave` all
   match; the value is forwarded exactly as received (Section 19: the
   backend validates/neutralizes, never translates or normalizes).
   Firmware must therefore compare action names case-insensitively.
   Unknown actions are `400`; a body carrying raw hardware fields
   (`servo_angle`, `pwm`, `gpio`, ...) is `400` at the envelope stage.
4. **Ordering**: ownership resolves first (404), envelope second (400),
   semantic third (400), publish fourth (503) - a foreign caller can
   never probe robot existence or action validity.
5. **Admin audit**: a successful admin command appends
   `AuditLog(action="robot.commanded", target=<robot id>)` via
   `devices.services.audit_robot_command`; parent commands are not
   audited (Section 28 append-only convention).

## Read APIs (owner-scoped, existing-data only)

- Endpoints: `GET /api/v1/devices/{id}/{state,events,sensors,
  configuration,summary}/` - all session-only (`401` for anonymous and
  device Bearer tokens), `GET`-only (`405` otherwise), and resolved
  through one `_get_owned_robot()` helper that reproduces the existing
  `RobotDetailView` policy: **owner-scoped for everyone, admins
  included** (per-robot reads never bypass pairing; fleet identity
  remains the admin list endpoint's job). Non-owned/unknown/malformed →
  indistinguishable `404`.
- **Stable shapes, honest nulls**: `state` always returns the same seven
  keys; a robot that never reported has `null` telemetry (null = not
  reported, never fabricated). `events`/`sensors` are bounded lists
  (`?limit=` integer 1..100, default 20, else `400`; newest first;
  `[]` when empty).
- **Configuration is read-only**: only the `is_current` version is
  exposed; `data` passes a recursive secret-key redaction guard
  (`password`, `token`, `secret`, `credential`, `api_key`, ...) so no
  writer can leak credentials through this API. Absent configuration →
  `{"version": null, "data": null, "is_current": null}`.
- **`summary`** composes `robot` (existing `RobotSerializer`) + `state` +
  newest 10 `events` + `configuration` from the same helpers - no new
  fields, queries of new stores, or ownership rules.
- Field sets come only from existing models (Section 23 panel fields:
  online, last seen, state, battery, firmware, Wi-Fi signal); charging
  state/uptime/history windows are not modelled yet and are not invented.

## Controller API out of scope (unchanged later-phase boundaries)

No visual UI/templates/JS (Phase 17 owns the panel); no bulk/group
command endpoint; no configuration write path; no conversation/memory/
safety-history endpoints (Phase 11+); no fleet-wide admin telemetry read
(owner decision if/when the admin dashboard needs it); no changes to the
frozen MQTT topic/payload contract, firmware, or AI-service contracts.
Phase 11 AI model/provider integration remains explicitly on hold.


# Controller UI (Main Web Control Panel, addendum)

Server-rendered control panel (docs/02 Phase 17 panel surface, owner-approved
Option B). Browser talks **only to Django**: Django Templates serve the pages
and the same-origin session + existing REST endpoints supply all live data -
no BFF layer, no new REST endpoints, no frontend build pipeline.

## Structure & routes

- `templates/base.html` (CSRF meta, static links) +
  `templates/dashboard/{index,controller}.html` +
  `templates/dashboard/partials/sidebar.html`; styling/behaviour in
  `static/css/{tokens,dashboard}.css` and `static/js/{api,dashboard,
  controller}.js` (classic scripts, `STATICFILES_DIRS` discovery; static
  *serving* follows the normal Django DEBUG/dev-server rule - production
  serving is a deployment-phase concern).
- `GET /dashboard/` (login-required): server-rendered, **parent-scoped**
  robot list + counts (total/online/offline/recently-active). Online =
  `RobotState.online` when a state row exists, otherwise offline (never
  reported). Derived only from existing Robot/RobotState data.
- `GET /dashboard/robots/<id>/` (login-required): controller page for one
  robot, resolved by the same `_get_owned_robot()` helper as the read APIs
  (the helper now raises `Http404`, which DRF converts to the identical
  `404 {"detail": "Not found."}` on API views); non-owned/unknown/malformed
  id -> indistinguishable 404, no debug/traceback output.
- The HTML robot id is a convenience for JavaScript only - every API call
  is re-authorized server-side (hidden ids are never authorization).
- `login.html` `next` now defaults to `/dashboard/` (root route unchanged).

## Ownership & role policy

- Dashboard query is `Robot.objects.filter(parent=_get_parent(request))`
  for **every** role: admins are deliberately not elevated (no fleet view
  in the UI); fleet identity stays on admin-only `GET /api/v1/devices/`.
- Existing API boundaries unchanged: parent device list still 403, per-robot
  reads still owner-scoped 404, identical 401 bodies.

## Client behaviour (`static/js`)

- `api.js` (`window.RobotAPI`): same-origin `fetch` with session cookie +
  `X-CSRFToken` from the `<meta name="csrf-token">` header (never
  localStorage); consumes only the existing endpoints (`summary`, `state`,
  `events`, `sensors`, `configuration`, `command`); friendly status-based
  messages (401/403/404/429/5xx/network) - never raw server internals or
  stack traces; 401 redirects to `/login/?next=...`.
- `controller.js`: one `summary` load for state/events/config + separate
  `sensors` fetch; state polling every 15s (paused while the tab is hidden,
  single-flight, resumes on `visibilitychange`); each section renders
  loading/success/empty/error with retry; all dynamic text via `textContent`
  (never `innerHTML`); the seven semantic buttons (`IDLE, WAVE, DANCE, NOD,
  TILT_HEAD, POINT, LISTENING_RETRY`) disable while sending (double-click
  safe) and show success/error status - no servo/PWM/GPIO/motor vocabulary
  anywhere in the UI.
- Configuration section is display-only (no forms, no write path).

## Controller UI out of scope (unchanged later-phase boundaries)

No new REST endpoints, no `/auth/me`, no changes to API authorization
rules; no AI/STT/LLM/TTS surface, no conversations/dashboards analytics,
no OTA/firmware, no config writes, no frontend framework/build tooling;
Phase 11 AI model/provider integration remains explicitly on hold.

# Controller UI premium redesign (Tailwind CSS v3, addendum)

UI-only pass over the panel defined above - structure, ownership and API
contracts unchanged. Browser still talks only to Django (session cookie +
`X-CSRFToken`, same-origin `fetch`); no SPA, no frontend framework, no
browser-MQTT, no new endpoints.

## Build pipeline (minimal, CLI-only)

- `package.json` (devDependency `tailwindcss@^3.4` only) + `tailwind.config.js`
  + `frontend/styles/input.css` -> `npm run build:css` ->
  **`backend/static/css/app.css`** (minified, purged; generated artifact,
  committed - CI does not run npm). `npm run watch` for development.
- Tailwind scans `backend/templates/**/*.html` and `backend/static/js/**/*.js`.
  Design tokens (`#C41E3A` primary, `#0B0B0D`/`#141417`/`#1B1B20` surfaces,
  `#2A2A31`/`#3A3A44` borders, `#F5F5F7`/`#A1A1AA`/`#71717A` text) are defined
  once in `tailwind.config.js`; handwritten CSS stays in `frontend/styles/
  input.css` (base/components layers: glass cards, buttons, off-canvas
  sidebar, skeletons, `prefers-reduced-motion`). Replaces the retired
  `tokens.css`/`dashboard.css`.
- Django: `STATICFILES_DIRS` (discovery) + `STATIC_ROOT` (collectstatic
  validation target, git-ignored). No static hosting added - serving strategy
  remains the deployment phase's concern.

## Page structure (contract-preserving)

- `base.html` (CSRF meta + `app.css`), `dashboard/partials/sidebar.html`
  (persistent desktop / off-canvas below `lg`, `aria-expanded`+`aria-controls`
  hamburger), `dashboard/index.html` (topbar with account/sign-out, local-time
  greeting, stat cards, robot cards), `dashboard/controller.html` (identity
  header with backend-driven connection badge, status metrics, command center,
  events/sensors/config with loading/skeleton + empty + error + retry views,
  read-only configuration), `registration/login.html` (same auth logic, now on
  the shared design system).
- JS layer unchanged architecturally: `api.js` = single API helper; `controller.js`
  keeps 15s single-flight polling (paused while hidden, resumed on
  `visibilitychange`) and the seven semantic actions with in-flight lock;
  `dashboard.js` handles sidebar toggle, sign-out, greeting.

## Test note

`test_controller_page_has_no_hardware_terminology` parses rendered HTML with
the stdlib `html.parser`, scans **user-visible text only** (attributes,
`<head>`, script/style and per-run dynamic values excluded) and additionally
scans the raw template source; the five forbidden terms are unchanged.

# Phase 11 - AI <-> Backend Integration (addendum)

Backend half of docs/01 Phase 11: `conversations/` now owns the single
Django -> AI service call path, per-turn persistence, and the Section 15.3
hard-failure fallback. AI model/provider integration stays on hold - the
AI service is still the deterministic Phase 10 mock pipeline, and no
provider SDK, key, or model hosting is touched.

## Call path

`POST /api/v1/conversations/turn/` (alias without trailing slash) ->
`conversations.views.ConversationTurnView` ->
`conversations.services.run_conversation_turn` ->
`conversations.ai_client.call_ai_turn` (httpx) -> `POST
/v1/conversation/turn` with `Authorization: Bearer $AI_SERVICE_INTERNAL_TOKEN`.

- `conversations/ai_contract.py` is the backend-owned mirror of the frozen
  Section 15 contract: `validate_ai_input()` self-checks the outbound
  payload, `validate_ai_output()` strictly checks the response (exactly
  the 8 fields + frozen enums; messages carry field names only, never
  values). It never imports `ai_service` code - drift is caught by tests
  written against the documented values.
- Settings: `AI_SERVICE_URL` (default `http://ai-service:8000`),
  `AI_SERVICE_INTERNAL_TOKEN` (empty default: the call fails closed, no
  development bypass), `AI_SERVICE_TIMEOUT_SECONDS` (default 30).
  All three are passed to the `django` service by Compose and documented
  in `.env.example`. `ai-service` remains unpublished on any host port and
  is deliberately NOT a `depends_on` of `django`.

## Phase 11 decisions (recorded 2026-10-03)

1. **Dual caller, one service path**: session (parent or admin) or device
   Bearer JWT, both resolving to `robot` + `child_profile` before the same
   orchestration runs. Ownership is owner-scoped for every role (admins
   are not elevated - the dashboard rule); a device token is scoped to its
   own robot; unknown/foreign/mismatched ids are indistinguishable `404`.
2. **Validation order**: auth `401` -> id syntax `400` -> ownership `404`
   -> remaining envelope `400` (Controller API ordering decision 4). A
   `400` only ever reflects the caller's own input. Unknown request
   fields are rejected (mirrors the strict AI contract), and
   client-supplied `context` can never be accepted: context is assembled
   server-side (AR-5b) from the conversation's own recent turns (bounded
   at 10, blank/voice turns skipped) with `memories: []` - retrieval is
   Phase 14.
3. **Transactions vs network**: conversation creation and the entire
   per-turn persistence run in `django_mongodb_backend.transaction.atomic()`
   blocks; the httpx call runs outside every transaction (network I/O
   never holds a Mongo transaction open - Phase 8 rule).
4. **Persistence**: child `Message` (text, or empty for a voice turn;
   `audio_reference` always empty - raw audio is forwarded once and never
   stored), robot `Message`, `AIInteraction` on the robot message
   (`latency_ms`, emotion, action, full safety object), `Memory` rows from
   `memory_writes` (keys over the 100-char column are skipped without
   failing the turn), and `Conversation.language` = latest output language.
5. **SafetyEvent**: written only when `safety.flagged` is true, linked to
   the *child* message (the flagged input - the robot reply is the
   approved redirect, not the trigger), `category`/`action_taken` verbatim
   from the frozen taxonomy, plus a derived display-only `severity`
   (`BLOCKED` high, `REDIRECTED` medium, `ALLOWED_WITH_CAUTION`/`ALLOWED`
   low). `reviewed_by` stays null; Section 30 access rules apply from the
   moment these rows are written.
6. **Hard-failure fallback** (Section 15.3 / Flow 19): any
   `AIServiceError` (`config`, `timeout`, `unavailable`, `http_error`,
   `invalid_response`) -> HTTP `200` with the canned fallback text,
   `CONCERNED`/`IDLE`, `safety: null` (not evaluated), `memory_writes: []`,
   and `language: en` (the canned text is English). The turn is still fully
   persisted and the `AIInteraction` is marked failed via
   `safety_flags={"failed": true, "reason": "<reason>"}`. No retry loop, no
   SafetyEvent, no memory writes. The internal token and child input are
   never logged or echoed.
7. **Audit**: parent turns are not audited (Phase 8 / Section 28
   convention); no new audit action was invented for this phase.

## Phase 11 out of scope (unchanged later-phase boundaries)

No real device audio path (Phase 12); no memory retrieval, context
injection, or memory CRUD API (Phase 14 - write-side acceptance only); no
conversation/safety-history read endpoints (Phase 17); no mode-specific
pipeline (Phase 15); no conversation rate limiting yet (Section 28
cost/abuse control still open - owner decision when to add it); no change
to the frozen AI contract, semantic-action set, MQTT contract, device
auth, ownership rules, or UI. AI model/provider integration remains
explicitly on hold.
