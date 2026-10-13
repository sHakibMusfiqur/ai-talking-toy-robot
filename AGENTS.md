# AI Talking Toy Robot â€” Agent Rules

## Project State

This repository is being implemented from scratch.
Do not assume existing functionality unless it is present in the repository and verified.

## Source of Truth

Read these documents before implementation:

- `docs/01-MASTER-SYSTEM-PLAN.md`
- `docs/02-CODING-IMPLEMENTATION-PLAN.md`
- this `AGENTS.md`

Do not silently replace documented architecture or contracts with personal preferences.
If requirements conflict or are incomplete, stop and ask the project owner. Never guess.

## Current Starting Point

Implementation starts at **Part 2 â€” Phase 5: Django Foundation**.

Part 1 (ESP32 firmware) will be implemented separately using the agreed ESP32 development workflow and is not to be implemented during Part 2 Phase 5 unless explicitly requested.

Part 3 (AI Chat Service: STT â†’ LLM â†’ Safety â†’ TTS) is externally owned and is integrated by API; do not implement its internals.

## Phase Discipline

Work one phase at a time.

For each phase:
1. inspect current state;
2. plan the phase;
3. implement only the approved scope;
4. run tests and relevant checks;
5. verify acceptance criteria;
6. report files changed and test results;
7. stop and wait for explicit approval before the next phase.

Never jump ahead because a later feature appears convenient.

## Security Invariants

- Backend is the only component that directly communicates with the external AI service.
- Never put AI-provider secrets in ESP32 firmware.
- Never disable TLS certificate validation in production.
- Enforce user and device ownership boundaries from the beginning.
- Device credentials/tokens are device-scoped.
- Pairing is explicit and per robot.
- Do not bypass authorization for convenience.
- Firmware must not receive or trust raw servo-angle commands as an AI contract; semantic actions are translated on the device and hardware limits remain enforced.
- Do not persist raw child audio beyond the required processing lifecycle.
- MQTT ACL isolation must not be bypassed by wildcard command publishing.

## Part 2 Technology Direction

Use the architecture specified by the plans:

- Django
- Django REST Framework
- MongoDB 7.0 (django-mongodb-backend 5.2.4)
- Mosquitto / MQTT
- Django Templates
- HTML/CSS
- small Vanilla JavaScript

Do not introduce a heavy frontend framework or unrelated infrastructure without explicit approval.

## Phase 5 Boundary

Phase 5 is the Django foundation. It includes the project foundation, core health/configuration, accounts, authentication/session behavior, MongoDB/Docker Compose foundation, and ownership-boundary tests.

Do not implement devices, MQTT, AI integration, conversations, dashboards, OTA, or firmware functionality in Phase 5 unless the source-of-truth plan explicitly requires a minimal foundation for Phase 5.

## Quality

Prefer simple, maintainable, testable code.

Every phase must have automated tests appropriate to its scope. Security and ownership boundaries must be tested, not merely documented.


---

## Decision Records (appended 2026-09-29)

- **2026-09-29 â€” MongoDB replaces PostgreSQL** for Part 2 (owner-authorized; full record in `docs/01-MASTER-SYSTEM-PLAN.md`). All `docs/` PostgreSQL references are historical â€” do not "restore" PostgreSQL without a new authorized decision record.

## Local Commands & Conventions (project-authored, 2026-09-29)

Run from repo root (Python 3.11 venv at `.venv/`):

```
.\.venv\Scripts\python.exe backend\manage.py check
.\.venv\Scripts\python.exe backend\manage.py migrate
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
# tests (run from backend/):
..\.venv\Scripts\python.exe -m pytest
```

- Layout: `backend/` (Django project `config`, apps `core`, `accounts`, generated contrib migrations in `mongo_migrations/`), `infra/` (Compose + Mongo healthcheck), `docs/` (source plans + API/backend docs).
- Hard rules: ObjectIds as JSON strings (no integer counters); non-owned resources return 404, never 403; identical 401 bodies for login failures (no existence leak); `GET /health` (no trailing slash) returns 200 directly; session cookie + CSRF for authenticated writes; ownership never settable by client input; no secrets committed (`.env` git-ignored).
- Use `django_mongodb_backend.transaction.atomic()` for multi-document writes (Django's `django.db.transaction` is unsupported on this backend).
- Phase 5 acceptance: `pytest` (41 tests) green + `manage.py check` clean before any phase report. No linter is configured yet (dev deps = pytest only) â€” add one and record its command here if introduced.

### Phase 8 additions (2026-09-30)

- Live services required before `pytest` (87 tests exercise the real Mosquitto broker; Mongo replica set needed for transactions): `docker compose --env-file .env -f infra/docker-compose.yml up -d mongo mosquitto`.
- Full suite after Phase 8: **223 tests** green (136 baseline + 87 new). Acceptance = `pytest` green + `manage.py check` clean + `git diff --check` clean + HEAD still `371e3f0` (never commit/stage/push; user's client commits).
- Bridge process (manual, not part of tests): `.\.venv\Scripts\python.exe backend\manage.py run_mqtt_bridge` (uses `MQTT_*` from `.env`). Mosquitto role bootstrap is idempotent via `infra/mosquitto/docker-entrypoint.sh`.

### Phase 9 additions (2026-10-01)

- AI service tests (run from `ai_service/`, host venv, no Docker needed): `..\.venv\Scripts\python.exe -m pytest` â€” **58 tests** green. Backend suite must stay at **223 tests** green.
- Deps: `ai_service/requirements.txt` (fastapi/uvicorn pins) + `ai_service/requirements-dev.txt` (httpx/pytest). No AI provider SDKs or keys are installed â€” Phase 9 is deterministic mocks only.
- `AI_SERVICE_INTERNAL_TOKEN` must be set in `.env` (Compose fails fast without it; `.env.example` holds a placeholder). Compose service `ai-service` is internal-only â€” never add a `ports:` mapping to it.
- Phase 9 acceptance: ai_service `pytest` (58) + backend `pytest` (223) green + `manage.py check` clean + `git diff --check` clean + HEAD still `b2e80ce` (never commit/stage/push; user's client commits).

### Phase 10 additions (2026-10-01, step 2 - architecture only)

- Provider-agnostic architecture landed: `interface.py`+`mock.py`+`providers/` (empty) for `stt|llm|tts`, `safety/{interface,pre,post,mock}.py`, additive `lang_detect/`, plus neutral `config.py`/`retry.py`/`errors.py` (env keys `AI_{STT,LLM,TTS}_*`, defaults `mock`/30s/retry 1/backoff 0.5). **Real providers intentionally deferred (owner decision) - no SDK, no keys, no provider names.**
- ai_service suite: **88 tests** (58 Phase 9 baseline unchanged + 30 architecture/failure tests), all offline. Backend stays **223**.
- Do not add a `providers/` adapter, provider package, or `AI_*` credential without a new approved provider decision record; never add `ports:` to `ai-service`.

### Phase 10 additions (2026-10-01, step 3 - safety package)

- Owner-approved interim scope (providers still deferred): `safety/rules.py` `RuleBasedSafety` is now the default `SafetyStage` in `default_stages()` â€” deterministic, offline, bilingual (en+bn), frozen 2-value taxonomy only (`PERSONAL_INFO_REQUEST` hard rule first, then `UNSAFE_TOPIC`); pre â†’ `REDIRECTED`, post â†’ `BLOCKED`. Extending the taxonomy = contract change = new owner decision record.
- Canonical fixture set: `docs/testing/child-safety-fixtures.json` (62 cases, required risk-area minimums, versioned) + pass/fail history in `docs/testing/SAFETY-FIXTURE-RESULTS.md`. Gate: **100% categorization** (Section 31 levels 6/14) â€” enforced by `ai_service/tests/test_safety_fixtures.py`, which also proves pre-flagged input never reaches the LLM and flagged drafts never reach TTS.
- ai_service suite: **206 tests** (88 step-2 baseline unchanged + 118 safety-package tests), all offline. Backend stays **223**.
- Acceptance for this step: ai_service `pytest` (206) + backend `pytest` (223) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Controller API additions (2026-10-03)

- Control-panel backend foundation (no UI, no Phase 11/AI-model work): semantic command gate on the existing `POST /api/v1/devices/{id}/command/` + new owner-scoped reads `GET /api/v1/devices/{id}/{state,events,sensors,configuration,summary}/` (full contract in `docs/api/endpoints.md`, decisions in `docs/backend/architecture.md` "Controller API").
- ONE central semantic-action definition: `backend/core/semantic_actions.py` â€” frozen AI `Action` enum + `DANCE` (required by existing Phase 8 tests / Phase 17 quick actions). Case-insensitive membership, verbatim pass-through, enforced in `RobotCommandView` (shared `DeviceCommandSerializer` stays mechanical per Phase 8 decision 5). Extending the set = frozen-contract change = new owner decision record.
- Read policy: owner-scoped for everyone including admins (existing `RobotDetailView` rule; fleet identity stays on `GET /devices/`); admin commands audit-logged as `robot.commanded`. Configuration read-only with secret-key redaction; bounded lists `?limit=` 1..100 default 20; nulls = not reported (never fabricated).
- Backend suite: **280 tests** (223 baseline unchanged + 57 new in `devices/tests/test_controller_api.py`), all green; ai_service stays **216**. Acceptance = backend `pytest` (280) + ai_service `pytest` (216) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Controller UI additions (2026-10-03)

- Main Web Control Panel step 1 (owner-approved Option B): `GET /dashboard/` (parent-scoped, server-rendered robot list + counts) + `GET /dashboard/robots/<pk>/` (owner-resolved controller page), both `@login_required`; **no** `GET /api/v1/robots/`, **no** `/auth/me`, `GET /api/v1/devices/` stays admin-only (Option B = use existing parent-scoped Django views). Admins are deliberately not elevated in the UI (no fleet view; fleet identity remains the admin API).
- Files: `backend/devices/views.py` (`dashboard`, `robot_controller` page views; `_get_owned_robot` now raises `Http404` - DRF converts it to the identical API 404), `backend/config/urls.py`, `backend/config/settings.py` (`STATICFILES_DIRS`), `backend/templates/{base.html, dashboard/*}`, `backend/static/{css,js}/*` (`window.RobotAPI`: session cookie + `X-CSRFToken` meta, friendly status errors only), `backend/templates/registration/login.html` (`next` defaults to `/dashboard/`; root route unchanged).
- UI rules baked into tests (`devices/tests/test_dashboard.py`): ownership scoping/counts/recently-active admin-neutral, 404 non-owned/unknown/malformed with no traceback, seven semantic labels only (no `servo/pwm/gpio/motor/voltage`), no `<form` (config read-only), no config secrets in HTML, `X-CSRFToken` enforced on commands (`enforce_csrf_checks=True`).
- Backend suite: **300 tests** (280 baseline + 20 new), ai_service stays **216**. Acceptance = backend `pytest` (300) + ai_service `pytest` (216) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).
- Static serving caveat: `infra/docker-compose.yml` runs `DJANGO_DEBUG=false` + `runserver` (no static host yet) - for local panel viewing run with `DJANGO_DEBUG=true`; production static serving belongs to the deployment phase. Static files need no build step.
### Premium UI / Tailwind additions (2026-10-03)

- UI-only redesign of the panel (owner-approved full brief): Django Templates + **Tailwind CSS v3** + existing Vanilla JS. **No** React/Vue/Svelte/Angular/Next/Vite/SPA, no new REST endpoints, no browser-MQTT, no auth/localStorage changes; backend code, API contracts, ownership and command semantics untouched (only `settings.py` gained `STATIC_ROOT` for collectstatic validation).
- Build workflow (repo root, Node required):
  - `npm install` ï¿½ installs devDependency `tailwindcss@^3.4` only (`package.json`, `package-lock.json`, `node_modules/` git-ignored).
  - `npm run build:css` ï¿½ `frontend/styles/input.css` -> **`backend/static/css/app.css`** (minified, purged; commit this generated file, CI does not run npm).
  - `npm run watch:css` ï¿½ dev watch. Config: `tailwind.config.js` scans `backend/templates/**/*.html` + `backend/static/js/**/*.js`; design tokens (primary `#C41E3A`, dark palette) live there - do not hardcode duplicate crimson values in templates.
  - Handwritten CSS is limited to `frontend/styles/input.css` (`@tailwind` layers + small component layer: glass, buttons, off-canvas sidebar, skeletons, reduced-motion). Old `tokens.css`/`dashboard.css` were retired.
- JS contract unchanged: `window.RobotAPI` (session cookie + `X-CSRFToken` meta, friendly errors, 401 redirect), 15s single-flight state polling paused on hidden tabs; only presentation copy changed ("Not available", relative last-seen) + time-of-day greeting (local time, no data).
- Static: `collectstatic --noinput` validated (31 files -> git-ignored `backend/staticfiles/`); no static *hosting* added - `DJANGO_DEBUG=true` for local viewing, deployment phase owns production serving.
- Tests: backend **306** (300 baseline + 5 premium-UI + 1 terminology-scanner meta-test), AI **216**. `test_controller_page_has_no_hardware_terminology` now scans extracted user-visible text (stdlib `html.parser`; attributes/`<head>`/dynamic values excluded) + raw template source - terms unchanged (`servo`, `pwm`, `gpio`, `motor`, `voltage`).
- Acceptance = backend `pytest` (306) + ai_service `pytest` (216) green + `manage.py check` + `collectstatic --noinput` + `npm run build:css` + `git diff --check` all exit 0 + nothing staged (never commit/stage/push; user's client commits).

### Phase 11 additions (2026-10-03)

- Conversation-turn endpoint **`POST /api/v1/conversations/turn/`** (plus the no-trailing-slash alias for firmware) in `backend/conversations/`: `views.py` (dual caller â€” session or device Bearer JWT; auth 401 â†’ id syntax 400 â†’ ownership 404 â†’ envelope 400; owner-scoped for every role, admins not elevated), `serializers.py` (text XOR audio, base64-checked, **unknown fields rejected, client `context` never accepted**), `permissions.py`, `urls.py`, `services.py` (server-assembled context; all per-turn persistence in `django_mongodb_backend.transaction.atomic()`; httpx call outside every transaction), `ai_client.py` (httpx + Bearer internal token + coarse failure reasons), `ai_contract.py` (backend-owned frozen Section 15 mirror: `validate_ai_input` / `validate_ai_output` â€” never imports `ai_service`).
- Wiring: `httpx==0.28.1` in `backend/requirements.txt`; `AI_SERVICE_URL`, `AI_SERVICE_INTERNAL_TOKEN` (empty default = call fails closed, no bypass), `AI_SERVICE_TIMEOUT_SECONDS` (30) in `backend/config/settings.py`, `.env.example`, and the `django` service env in `infra/docker-compose.yml`. `ai-service` stays unpublished and is deliberately **not** a `depends_on`.
- Behavior: response is always the frozen 8-field Output Contract and always `200`; AI-service hard failure returns the Section 15.3 local fallback (`CONCERNED`/`IDLE`, `safety: null` = not evaluated) while still persisting the turn and marking `AIInteraction.safety_flags={"failed": true, "reason": timeout|unavailable|http_error|invalid_response|config}` (no retry loop); `SafetyEvent` only when flagged, linked to the **child** message, severity derived `BLOCKED`â†’high / `REDIRECTED`â†’medium / `ALLOWED_WITH_CAUTION`,`ALLOWED`â†’low; `Memory` rows from `memory_writes` (oversized keys skipped, turn never fails); raw child audio forwarded once and never stored (`audio_reference` stays blank until Phase 12).
- Tests: backend **364** (306 baseline + 58 new in `conversations/tests/`), ai_service stays **216**. AI calls in tests are fully offline (`httpx.MockTransport` for the client; `conversations.services.call_ai_turn` monkeypatched for the view) â€” no network, no provider, AI HOLD respected.
- Docs: `docs/api/endpoints.md` gained the Phase 11 reference; `docs/backend/architecture.md` gained the Phase 11 addendum (decisions 1â€“7 + out-of-scope, incl. **conversation rate limiting still open**).
- Acceptance = backend `pytest` (364) + ai_service `pytest` (216) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Phase 11 rate-limit additions (2026-10-13)

- Owner-approved conversation-turn throttle closing the documented open item: `backend/conversations/throttles.py` `ConversationTurnThrottle(SimpleRateThrottle)` attached only to `ConversationTurnView` - fixed window **`30/min` (30 per 60 s)** by default via `CONVERSATION_TURN_RATE_LIMIT` (settings + `.env.example`; DRF rate syntax; empty disables), keyed `robot:<pk>` for device Bearer callers and `user:<pk>` for session callers; anonymous requests get no bucket (still `401`, never IP-throttled). Exceeded limit = DRF default `429` (detail begins `Request was throttled.` + computed wait) with `Retry-After`; throttle runs **before** ownership resolution (exhausted bucket answers `429` identically for owned/foreign ids - no existence oracle). Storage is in-memory/locmem: process-exact for today's single-process `runserver`; multi-worker needs a shared cache (same accepted limitation as `ROBOT_COMMAND_COOLDOWN_SECONDS`). No new dependency; frozen AI contract/auth/ownership/validation order untouched; Phase 19 still owns auth-endpoint throttling.
- Docs: `docs/api/endpoints.md` (429/`Retry-After` on the turn reference), `docs/backend/architecture.md` (addendum subsection + open item removed).
- Tests: **8 new** in `backend/conversations/tests/test_throttling.py` (window boundary, 429 shape/`Retry-After`, no AI call when throttled, device-vs-session and robot-vs-robot bucket independence, anonymous 401s consume no bucket, empty-config disables, ownership 404 + no existence oracle under exhaustion; autouse cache-clear fixture; rate strings must be DRF syntax - `30/min` not `30/60s`).
- Acceptance = backend `pytest` (548 passed, 2 skipped = 540 baseline + 8 new) + ai_service `pytest` (218) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).
### Firmware / real-mode UI additions (2026-10-08)

- **Part 1 firmware implemented** as `firmware/` (ESP-IDF project, C): identity (NVS UUIDv4 + ECDSA P-256, challenge signing), backend registration/challenge/token client, Mosquitto client (LWT, exact four subscriptions, JWT-as-password), telemetry (status/heartbeat/lifecycle events), semantic-action whitelist with joint limits. TLS on by default in Kconfig; lab opt-out flags are explicit; pairing code printed once on serial. No AI-provider secrets; firmware receives semantic actions only.
- Docs: `docs/firmware/esp32-mqtt-integration.md` (architecture, contracts, provisioning, compose dev flow, troubleshooting, PASS vs NOT TESTED status table).
- **Controller real-vs-mock switch**: `controller.html` now declares `data-service-mode="real"`; `controller.js` reads live APIs by default, reports command failure truthfully ("could not be sent", no fake queued copy in real mode), disables buttons in-flight; mock assets remain shipped for local preview.
- Tests: backend **426 collected (425 passed, 1 skipped)** = 384 baseline + 42 new (5 acceptance-path in `mqtt_bridge/tests/test_acceptance_path.py` + 12 real-integration/static in `devices/tests/test_real_integration.py` + 25 firmware source contract in `mqtt_bridge/tests/test_firmware_contract.py`); the single skip is `test_firmware_builds_with_espidf` (`idf.py` toolchain absent). ai_service stays **216**. Live Mosquitto + Mongo required (compose).
- Integration review fixes (2026-10-08): credential-log scanner is now **statement-level** (wraps ESP_LOG/printf calls to their terminating `;`, closing the multi-line blind spot) + new `test_pairing_code_is_printed_exactly_once`; `controller.js` offline warning only for `online === false` (never-reported must not claim "offline"); removed the hardcoded fake uptime trend polyline from `controller.html` (mock values stay in the mock service); restored `docs/firmware/esp32-mqtt-integration.md` (was missing).
- NOT TESTED by design in this environment: `idf.py` build, physical ESP32 flash/servo motion.
- Acceptance = backend `pytest` (425 passed, 1 skipped) + ai_service `pytest` (216) green + `manage.py check` clean + `collectstatic --noinput` OK + `npm run build:css` OK + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Robot simulator (software-in-the-loop) additions (2026-10-10)

- **`backend/robot_sim/`** - hardware-free Python robot speaking the real device contracts: `POST /api/v1/devices/register/` + challenge (ECDSA-SHA256 P-256) + token exchange, JWT-as-MQTT-password, exact four subscriptions in one SUBSCRIBE packet, LWT + graceful-stop retained `{"online":false}`, status/heartbeat/event topics, frozen semantic-action whitelist mirroring `firmware/main/actions.c` (case-insensitive, canonical uppercase acks, `invalid_payload`/`unknown_action` rejects), every emitted payload stamped `"simulated": true`, no fabricated telemetry (`rssi`/`battery` never emitted). Identity in gitignored `sim_state/` (locally generated private key, delete to rotate); pairing code printed once; no new dependencies (`paho-mqtt`/`cryptography`/`httpx` already pinned). Reuses ONE definitions: `core.semantic_actions.SEMANTIC_ACTIONS`, `mqtt_bridge.topics`.
- Runbook: `docs/testing/robot-simulator.md` - requires host processes to preload `MQTT_*` from `.env` (settings only auto-load them under pytest); pairing via `manage.py shell` + `pair_robot` (no pairing UI yet); documents known divergences (Python JSON parser stricter than cJSON on trailing garbage; live token-refresh path untested) and NOT TESTED hardware claims.
- Tests: `backend/robot_sim/tests/` - **91 tests** (90 passed, 1 skipped on Windows: POSIX file-mode check). Full backend suite: **517 collected, 515 passed, 2 skipped** (426 baseline + 91). Integration test uses the live Mosquitto + real bridge subscriber (register/pair/auth, exact subscription grants, status+heartbeat ingestion, owner command loop with `simulated` acks, state BOOT->ACTION->IDLE, reject paths, graceful-offline, no-secrets-in-logs). ai_service stays **216**.
- Live CLI smoke (dev DB + real broker/bridge/runserver): register + pairing-banner-once + pair via documented shell flow + dashboard state `online`/`BOOT` + owner command 200 with `simulator.started`/`command.received`/`command.executed` events (`{"action":"DANCE","simulated":true}`) + state -> IDLE + hard-kill LWT -> `online=False`. Smoke DB rows and temp state deleted afterwards.
- Pre-existing latent flake fixed (test-only): `mqtt_bridge/tests/test_reconnect.py::test_bridge_survives_full_broker_restart` raced the post-restart dynsec bootstrap (shared control-topic response crossing -> spurious `ACL with this topic already exists`); the test now waits (bounded 30 s) until `ensure_device_client` succeeds after TCP-up. No production code changed.
- Acceptance = backend `pytest` (515 passed, 2 skipped) + ai_service `pytest` (216) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Security & Failure-Handling Phase A additions (2026-10-11)

- Owner-approved scope exactly A1-A4 (F-01..F-16 review outcome; no other finding implemented):
  - **A1 (F-02)**: `mqtt_bridge/subscriber.py` drops telemetry (status/heartbeat/event) from registered-but-unpaired devices ("mqtt message for unpaired device dropped") - registration issues broker credentials before a human pairs, so ingestion waits for explicit pairing. Command publishing (backend->device) is NOT pairing-gated. `_robot()` helpers in ingestion/lwt/reconnect tests now create PAIRED robots; 4 new unpaired-drop tests + resume-after-pairing test.
  - **A2 (F-08)**: `mqtt_bridge/client.py` fail-safe publish - fail-fast when not connected (never enqueue on a dead socket), poison the connection on any transport failure (rc!=SUCCESS / RuntimeError / ack timeout / not published); poisoning tears down completely (disconnect + loop_stop) so a queued QoS 1 copy can never be auto-re-delivered; poisoned connections refuse further publishes; `is_usable()` added. `publisher.get_publisher()` discards unusable singletons and builds a fresh connection. `devices/exceptions.py` `RobotCommandThrottled` (429, code `robot_command_throttled`); per-(user, robot) in-memory cooldown in `RobotCommandView` (`ROBOT_COMMAND_COOLDOWN_SECONDS`, env, default 1s; reservation-before-publish under a lock that is never held during MQTT I/O; a failed publish releases only its own reservation token so retries work and a slow failing publish cannot delete a newer reservation; pruned >1000 entries; single-process limitation documented). New live acceptance test `test_publish_no_late_delivery.py` (docker stop -> publish raises MqttError -> docker start -> observer proves "NEVER" is not delivered -> fresh publish recovers -> bridge ingestion-recovery gate). New unit tests `test_client_fail_safe.py` (fake paho client). Known documented limitation: QoS 1 ack-timeout race (bytes on the wire when the ack window expires) is inherent protocol ambiguity - the poison still guarantees the HTTP caller gets 503 and the queued copy dies with the connection.
  - **A3 (F-09)**: conversation payload bounds - `CONVERSATION_MAX_TEXT_LENGTH=4000`, `CONVERSATION_MAX_AUDIO_BASE64_LENGTH=1400000`, `DATA_UPLOAD_MAX_MEMORY_SIZE=5MB` (settings + env); enforced in `conversations/serializers.py` and mirrored as pydantic `max_length` in `ai_service/contract/schemas.py` (`MAX_TEXT_LENGTH`, `MAX_AUDIO_BASE64_LENGTH`). Django 5.2 raises `RequestDataTooBig` -> **400, not 413** (test accepts either; deviation documented). Frozen conversation response contract unchanged.
  - **A4 (F-05)**: `core/local_ui_bypass.py` returns early for every `/api/` path - the local-UI auth bypass can never apply to API routes (commands included); 401 tests added.
- **F-01 deferral (owner-approved)**: pre-pair MQTT credential provisioning is UNCHANGED - registration still issues broker credentials so the device can reach the broker before pairing; A1's subscriber pairing gate is the compensating control (defense in depth behind the per-device ACL). Full "credentials only after pairing" would be a provisioning-contract change = new owner decision record.
- **Test-infrastructure root-cause fix (no production code)**: the intermittent `test_simulator_full_loop_web_mqtt_device` flake ("command ack events did not reach the database", ~1/3 when run after `mqtt_bridge`) was caused by `robot_sim/tests/conftest.py` re-exporting `broker`/`bridge`: pytest registered a SECOND session FixtureDef, so `start_bridge()` ran twice and two connections sharing client_id `django-bridge-{pid}` took over each other's broker session every ~1s forever (mosquitto logs: "session taken over" storm). Fix: `broker`/`bridge` defined ONCE in root `backend/conftest.py`; `mqtt_bridge/tests/conftest.py` keeps only `oracle`/`make_device`/`_deprovision`; `robot_sim/tests/conftest.py` deleted. Verified 5/5 clean combo runs after the fix (previously ~1/3 flake). The bridge ingestion-recovery gate at the end of `test_publish_no_late_delivery.py` stays (honest suite hygiene after broker restart).
- Tests: backend **540 collected (538 passed, 2 skipped)** = 517 baseline + 23 new (4 pairing-gate + 7 cooldown in `test_controller_api.py` + 6 `test_client_fail_safe.py` + 1 live `test_publish_no_late_delivery.py` + 4 payload-bound + 2 bypass-command/summary-401); ai_service **218** (216 baseline + 2 schema-cap). Full-suite combo `mqtt_bridge robot_sim` verified 5/5 green after the fixture fix.
- Acceptance = backend `pytest` (538 passed, 2 skipped) + ai_service `pytest` (218) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).
- **Review follow-up hardening (2026-10-11)**: two review findings in the A2 cooldown were fixed. (1) Check-then-act race: `_reserve_command_slot` now atomically checks AND reserves the next slot under a module lock (lock held only for microsecond dict ops, never during MQTT publish); the view reserves before `publish_command` and a `MqttError` releases the reservation so 503 retries work. (2) Reservation-release ABA race: each reservation carries a unique monotonic token; `_release_command_slot(user, robot, token)` deletes the entry only while it still holds that token, so a slow publish that fails after its window expired can never delete a newer reservation that replaced it. Regression tests: `test_concurrent_duplicate_commands_publish_exactly_once` (4 simultaneous requests -> exactly one publish/200, three 429) and `test_slow_failing_publish_cannot_release_newer_reservation` (A reserves -> window expires -> B reserves -> A fails -> B's reservation intact -> third request still 429); both deterministic (barrier/event-gated fake publishes), both `django_db(transaction=True)`. Cooldown behavior otherwise unchanged (429/503/disabled/expiry/per-key semantics + all 7 earlier cooldown tests pass untouched). Backend now **542 collected (540 passed, 2 skipped)** = 540 Phase A baseline + 2 review regression tests (`test_concurrent_duplicate_commands_publish_exactly_once`, `test_slow_failing_publish_cannot_release_newer_reservation`); ai_service stays **218**. Single-process in-memory limitation and the documented QoS 1 ack-timeout ambiguity are unchanged.
