# AI Service 

FastAPI service holding the AI pipeline (Sections 14.3/15/16/17/18 of
`docs/01-MASTER-SYSTEM-PLAN.md`). **Phase 9 ships deterministic stub stages
only** — no real STT/LLM/TTS provider, no provider SDK, no API key. Real
providers are Phase 10; Django integration is Phase 11.

## Endpoints

- `GET /health` — readiness probe (Section 28), unauthenticated.
- `POST /v1/conversation/turn` — the internal AI endpoint. Requires
  `Authorization: Bearer $AI_SERVICE_INTERNAL_TOKEN` (Section 25). Body =
  AI Input Contract (Section 15.2), response = AI Output Contract
  (Section 15.3). Strict validation: unknown fields, wrong types, and
  requests with both/neither of `text`/`audio` are rejected with `422`.

## Contracts

- Input: `conversation_id`, `child_profile_id`, `robot_id`, exactly one of
  `text` (string) or `audio` (base64), optional `language_hint`
  (`en|bn|auto`), `context` (recent turns + memories, pre-fetched by
  Django — this service never touches the database), optional `mode`
  (`conversation|story|quiz`).
- Output: `conversation_id`, `text`, `language`, `emotion`, `action`,
  `safety {flagged, category, action_taken}`, `audio` (null while TTS is
  stubbed), `memory_writes []`. `emotion`/`action` are frozen semantic
  enums — the contract can never contain servo/PWM/GPIO instructions.

## Networking

Compose service `ai-service` on the internal Docker network only — **no
host port is published**. Django reaches it at `http://ai-service:8000`
(from Phase 11 on). The internal token is injected from
`.env` (`AI_SERVICE_INTERNAL_TOKEN`, see `.env.example` placeholder).

## Run tests (host venv, no Docker required)

```
# from repo root
.\.venv\Scripts\python.exe -m pip install -r ai_service\requirements-dev.txt
cd ai_service
..\.venv\Scripts\python.exe -m pytest -q
```

## Service (container)

```
docker compose --env-file .env -f infra/docker-compose.yml up -d --build ai-service
```

##  provider-agnostic architecture

Phase 10 currently provides provider-agnostic integration architecture
and deterministic implementations. Real provider selection and provider
implementation are intentionally deferred (owner decision).

- Provider-capable stages: `interface.py` (Protocol) + `mock.py`
  (deterministic) + `providers/` (empty, future adapters).
- `safety/` exposes the fixed order via `pre.py` (pre-LLM) and
  `post.py` (post-LLM, pre-TTS): unsafe LLM output never reaches TTS.
- `lang_detect/` resolves per-utterance language deterministically
  (hint first, then script detection); no library installed.
- `config.py` (neutral `AI_{STT,LLM,TTS}_*` env keys), `retry.py`
  (retry-once-with-backoff, offline-testable), `errors.py`
  (`ProviderError`) drive provider-neutral failure handling with
  contract-respecting fallbacks.

### Safety (deterministic v1)

- `safety/rules.py` `RuleBasedSafety` is the default safety stage:
  fully offline rule engine, no provider/model/credential.
- Two stages, fixed order (Section 14.2): pre-LLM check (flagged input
  never reaches the LLM, answered with the approved redirect) and
  post-LLM last gate before TTS (flagged draft replaced, never
  spoken).
- Frozen two-value taxonomy (Phase 0 contract): `PERSONAL_INFO_REQUEST`
  (Section 17.1 hard rule, always blocked/redirected) and
  `UNSAFE_TOPIC` (everything else in the Section 17.1 coverage list).
  Bilingual pattern coverage (English + Bangla).
- Fixture set and 100% gate: `docs/testing/child-safety-fixtures.json`
  with history in `docs/testing/SAFETY-FIXTURE-RESULTS.md`; run
  `pytest tests/test_safety_fixtures.py` from `ai_service/`.
