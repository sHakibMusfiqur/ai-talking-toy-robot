# AI Talking Toy Robot — Agent Rules

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

Implementation starts at **Part 2 — Phase 5: Django Foundation**.

Part 1 (ESP32 firmware) will be implemented separately using the agreed ESP32 development workflow and is not to be implemented during Part 2 Phase 5 unless explicitly requested.

Part 3 (AI Chat Service: STT → LLM → Safety → TTS) is externally owned and is integrated by API; do not implement its internals.

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

- **2026-09-29 — MongoDB replaces PostgreSQL** for Part 2 (owner-authorized; full record in `docs/01-MASTER-SYSTEM-PLAN.md`). All `docs/` PostgreSQL references are historical — do not "restore" PostgreSQL without a new authorized decision record.

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
- Phase 5 acceptance: `pytest` (41 tests) green + `manage.py check` clean before any phase report. No linter is configured yet (dev deps = pytest only) — add one and record its command here if introduced.

### Phase 8 additions (2026-09-30)

- Live services required before `pytest` (87 tests exercise the real Mosquitto broker; Mongo replica set needed for transactions): `docker compose --env-file .env -f infra/docker-compose.yml up -d mongo mosquitto`.
- Full suite after Phase 8: **223 tests** green (136 baseline + 87 new). Acceptance = `pytest` green + `manage.py check` clean + `git diff --check` clean + HEAD still `371e3f0` (never commit/stage/push; user's client commits).
- Bridge process (manual, not part of tests): `.\.venv\Scripts\python.exe backend\manage.py run_mqtt_bridge` (uses `MQTT_*` from `.env`). Mosquitto role bootstrap is idempotent via `infra/mosquitto/docker-entrypoint.sh`.

### Phase 9 additions (2026-10-01)

- AI service tests (run from `ai_service/`, host venv, no Docker needed): `..\.venv\Scripts\python.exe -m pytest` — **58 tests** green. Backend suite must stay at **223 tests** green.
- Deps: `ai_service/requirements.txt` (fastapi/uvicorn pins) + `ai_service/requirements-dev.txt` (httpx/pytest). No AI provider SDKs or keys are installed — Phase 9 is deterministic mocks only.
- `AI_SERVICE_INTERNAL_TOKEN` must be set in `.env` (Compose fails fast without it; `.env.example` holds a placeholder). Compose service `ai-service` is internal-only — never add a `ports:` mapping to it.
- Phase 9 acceptance: ai_service `pytest` (58) + backend `pytest` (223) green + `manage.py check` clean + `git diff --check` clean + HEAD still `b2e80ce` (never commit/stage/push; user's client commits).

### Phase 10 additions (2026-10-01, step 2 - architecture only)

- Provider-agnostic architecture landed: `interface.py`+`mock.py`+`providers/` (empty) for `stt|llm|tts`, `safety/{interface,pre,post,mock}.py`, additive `lang_detect/`, plus neutral `config.py`/`retry.py`/`errors.py` (env keys `AI_{STT,LLM,TTS}_*`, defaults `mock`/30s/retry 1/backoff 0.5). **Real providers intentionally deferred (owner decision) - no SDK, no keys, no provider names.**
- ai_service suite: **88 tests** (58 Phase 9 baseline unchanged + 30 architecture/failure tests), all offline. Backend stays **223**.
- Do not add a `providers/` adapter, provider package, or `AI_*` credential without a new approved provider decision record; never add `ports:` to `ai-service`.

### Phase 10 additions (2026-10-01, step 3 - safety package)

- Owner-approved interim scope (providers still deferred): `safety/rules.py` `RuleBasedSafety` is now the default `SafetyStage` in `default_stages()` — deterministic, offline, bilingual (en+bn), frozen 2-value taxonomy only (`PERSONAL_INFO_REQUEST` hard rule first, then `UNSAFE_TOPIC`); pre → `REDIRECTED`, post → `BLOCKED`. Extending the taxonomy = contract change = new owner decision record.
- Canonical fixture set: `docs/testing/child-safety-fixtures.json` (62 cases, required risk-area minimums, versioned) + pass/fail history in `docs/testing/SAFETY-FIXTURE-RESULTS.md`. Gate: **100% categorization** (Section 31 levels 6/14) — enforced by `ai_service/tests/test_safety_fixtures.py`, which also proves pre-flagged input never reaches the LLM and flagged drafts never reach TTS.
- ai_service suite: **206 tests** (88 step-2 baseline unchanged + 118 safety-package tests), all offline. Backend stays **223**.
- Acceptance for this step: ai_service `pytest` (206) + backend `pytest` (223) green + `manage.py check` clean + `git diff --check` clean + nothing staged (never commit/stage/push; user's client commits).

### Controller API additions (2026-10-03)

- Control-panel backend foundation (no UI, no Phase 11/AI-model work): semantic command gate on the existing `POST /api/v1/devices/{id}/command/` + new owner-scoped reads `GET /api/v1/devices/{id}/{state,events,sensors,configuration,summary}/` (full contract in `docs/api/endpoints.md`, decisions in `docs/backend/architecture.md` "Controller API").
- ONE central semantic-action definition: `backend/core/semantic_actions.py` — frozen AI `Action` enum + `DANCE` (required by existing Phase 8 tests / Phase 17 quick actions). Case-insensitive membership, verbatim pass-through, enforced in `RobotCommandView` (shared `DeviceCommandSerializer` stays mechanical per Phase 8 decision 5). Extending the set = frozen-contract change = new owner decision record.
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
  - `npm install` � installs devDependency `tailwindcss@^3.4` only (`package.json`, `package-lock.json`, `node_modules/` git-ignored).
  - `npm run build:css` � `frontend/styles/input.css` -> **`backend/static/css/app.css`** (minified, purged; commit this generated file, CI does not run npm).
  - `npm run watch:css` � dev watch. Config: `tailwind.config.js` scans `backend/templates/**/*.html` + `backend/static/js/**/*.js`; design tokens (primary `#C41E3A`, dark palette) live there - do not hardcode duplicate crimson values in templates.
  - Handwritten CSS is limited to `frontend/styles/input.css` (`@tailwind` layers + small component layer: glass, buttons, off-canvas sidebar, skeletons, reduced-motion). Old `tokens.css`/`dashboard.css` were retired.
- JS contract unchanged: `window.RobotAPI` (session cookie + `X-CSRFToken` meta, friendly errors, 401 redirect), 15s single-flight state polling paused on hidden tabs; only presentation copy changed ("Not available", relative last-seen) + time-of-day greeting (local time, no data).
- Static: `collectstatic --noinput` validated (31 files -> git-ignored `backend/staticfiles/`); no static *hosting* added - `DJANGO_DEBUG=true` for local viewing, deployment phase owns production serving.
- Tests: backend **306** (300 baseline + 5 premium-UI + 1 terminology-scanner meta-test), AI **216**. `test_controller_page_has_no_hardware_terminology` now scans extracted user-visible text (stdlib `html.parser`; attributes/`<head>`/dynamic values excluded) + raw template source - terms unchanged (`servo`, `pwm`, `gpio`, `motor`, `voltage`).
- Acceptance = backend `pytest` (306) + ai_service `pytest` (216) green + `manage.py check` + `collectstatic --noinput` + `npm run build:css` + `git diff --check` all exit 0 + nothing staged (never commit/stage/push; user's client commits).