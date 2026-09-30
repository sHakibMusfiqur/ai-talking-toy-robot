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
