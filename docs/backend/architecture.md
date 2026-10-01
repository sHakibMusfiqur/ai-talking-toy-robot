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
