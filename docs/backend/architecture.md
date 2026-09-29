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
