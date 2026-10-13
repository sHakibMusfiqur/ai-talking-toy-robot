# AI-Powered Talking Toy Robot — System Architecture & Functional Implementation Plan

**Document type:** Master Blueprint (Functional + Technical), for phased execution by an AI coding agent
**Owner of this document's scope:** Software Developer / Integration Engineer
**Status:** Architecture Freeze Candidate (Phase 0 output)

> **Note on source material:** The only source document provided is the planning brief itself (the prompt used to generate this plan). No separate hardware/product specification file was attached. Everything below therefore treats that brief as the source of truth; nothing in it has been silently dropped. Every place where the brief was silent and a decision was still required is explicitly tagged **ARCHITECTURAL RECOMMENDATION**, with reasoning, so you can accept, reject, or hand it to the team for a real decision before Phase 0 is frozen.

---

## 1. Executive Summary

This plan defines a three-tier product: an ESP32-S3 firmware tier (hardware control only), a Django/PostgreSQL backend tier (device management, identity, orchestration, dashboards), and a separate Python AI service tier (STT → LLM → Safety → TTS → semantic emotion/action output). The robot never talks to an LLM provider directly and never holds a provider API key. All AI-provider secrets live only in the AI service, server-side.

Two transport protocols are used deliberately, not interchangeably: **HTTPS/REST** for anything transactional, authenticated, and infrequent (registration, config, firmware metadata, conversation history), and **MQTT** for anything real-time, high-frequency, or event-driven (status, commands, state changes). Audio itself is transported over HTTPS as chunked/streamed upload+download tied to a conversation session, not over MQTT (MQTT brokers are not built for large binary streaming and QoS semantics don't fit audio well) — this is one of the deviations from the brief's suggested "MQTT for live communication," explained in Section 9.

The AI service returns **semantic** commands only (`emotion: HAPPY`, `action: WAVE`) — never raw servo angles — preserving hardware/AI separation. The system is designed to run in 22 dependency-ordered phases, each independently testable and each shippable as a self-contained brief to a coding agent, so no phase requires the agent to guess at, invent, or pre-build future-phase functionality.

Offline capability is intentionally limited: ESP32-S3 cannot run a general LLM. Offline mode provides canned/local responses, pre-cached stories, and simple local games/movement only. Full conversational AI requires connectivity. A Raspberry Pi (or similar edge compute) local-AI tier is documented as an **architectural alternative**, not adopted into the base architecture, per the brief's instruction not to silently change hardware requirements.

---

## 2. Source Requirements (As Given, Unmodified)

The following requirements are taken directly from the brief and preserved as-is; they are binding constraints on this architecture:

- Functional scope: listening, voice detection, STT, request understanding, AI response generation, TTS, emotion display, head/body/arm/eye movement, educational content, storytelling, Q&A, quizzes/games, Bangla + English support, conversation context, basic memory, online mode, limited offline mode, secure backend comms, configuration receipt, firmware updates (OTA), device status reporting, parent/admin controls.
- Team: 3 people — Electronics Engineer (hardware), Software Developer (firmware integration, backend, DB, API, device auth/mgmt, config, OTA, dashboard, AI-service integration, system integration — **this document's author's scope**), AI Developer (STT, LLM, conversation engine, TTS, safety filtering, personality, emotion/action decision, AI memory/context logic).
- Stack: ESP32-S3 / C++ / ESP-IDF; Django + DRF; PostgreSQL; Python AI service (FastAPI where appropriate, separate service); Wi-Fi + HTTPS/REST + MQTT; Mosquitto or EMQX; Django Templates + HTML/CSS/JS web dashboard; Docker/Docker Compose on Linux server/VPS/cloud; Git/GitHub.
- Hard rule: **no third-party AI provider secret/API key inside ESP32 firmware, ever.**
- Hard rule: AI must not return raw servo angles — only semantic emotion/action.
- Hard rule: no big-bang coding; one phase at a time; no invented/undocumented APIs, topics, or secrets; no AI logic inside ESP32.
- This is a children's product: dedicated child-safety, privacy, and data-retention architecture is mandatory.
- No mobile app developer currently on the team — a **premium web dashboard** (Django Templates/HTML/CSS/JS) replaces a mobile app for now, but backend APIs must remain mobile-app-ready for the future.
- Visual direction for dashboard: luxury/cinematic/modern-robotics aesthetic, black/white/soft-gray base with premium crimson/red accent, glassmorphism where useful, elegant typography, responsive, functionality prioritized over visual flourish.

All of the above are treated as fixed. Nothing below removes or contradicts them.

---

## 3. Assumptions & Architectural Recommendations

Everything in this section is a decision this plan had to make because the brief left it open. None of it is a "hidden requirement" — treat each line as a proposal to accept or override before Phase 0 freezes.

| # | Topic | ARCHITECTURAL RECOMMENDATION | Reasoning |
|---|---|---|---|
| AR-1 | Audio transport | Audio (mic upload, TTS download) travels over **HTTPS**, chunked, tied to a `conversation_session_id`; **not** over MQTT. | MQTT brokers are optimized for small, frequent control/event messages, not multi-second binary audio streams; forcing audio through MQTT adds broker load, QoS/retry complexity, and payload-size tuning with no benefit over a plain HTTPS stream upload/download. |
| AR-2 | Wake word | Wake-word detection ("Hi Robo" or similar) runs **on-device** (ESP32-S3), using a lightweight keyword-spotting model, not cloud STT, so the mic isn't streaming to the cloud continuously. | Privacy (a children's product must not stream raw audio 24/7), bandwidth, latency, and ESP32-S3 has enough headroom for a small KWS model. Exact wake-word model choice is an AI-Developer/Electronics decision outside this document's authority; this plan only fixes *where* it runs. |
| AR-3 | Voice Activity Detection (VAD) | Runs on-device, after wake word, to decide when the child has stopped talking (end-of-utterance), so ESP32 knows when to stop streaming audio to the backend. | Needed regardless of where STT runs; keeps upload bounded and reduces AI-service load. |
| AR-4 | STT/TTS execution location | STT and TTS run in the **AI service**, calling out to a cloud or self-hosted provider (provider choice is an AI-Developer decision) — not on ESP32. | ESP32-S3 cannot run production-grade multilingual (Bangla+English) STT/TTS at acceptable latency/accuracy; this is consistent with the brief's own diagram, just made explicit. |
| AR-5 | Session/state ownership | Django owns the **canonical** device/robot/user/conversation state (source of truth in PostgreSQL). MQTT broker and AI service are stateless from a persistence standpoint — they read/write through Django's API or a shared cache, never their own separate database. | Prevents split-brain state; matches "Backend as central device/application management layer" principle from the brief. |
| AR-5b | AI-service state access | AI service does **not** connect directly to PostgreSQL. It calls Django's internal API for context/memory, and Django is the only service with a DB connection. | Keeps a single, auditable data-access boundary; simplifies the security model (one service to harden against SQL injection, one place to enforce privacy/retention rules) and keeps the AI service swappable/stateless. |
| AR-6 | Caching / real-time pub-sub backing store | **Redis** is added, used only for: (a) MQTT-adjacent ephemeral state (e.g., "is robot currently online" flags), (b) short-lived conversation-turn context cache, (c) Django Channels backing store if live dashboard updates are wanted later, (d) Celery broker for async tasks (OTA push, firmware checksum jobs, log rotation). | *Not mandatory* for MVP (Phases 0–12 can run without it, using Postgres + MQTT retained messages instead), but becomes valuable at Phase 13+ (memory/context performance) and Phase 18 (OTA background jobs). Marked **optional now, recommended by Phase 13**. Increases complexity moderately; justified by avoiding home-grown polling/locking logic. |
| AR-7 | Async task runner | **Celery** (with Redis as broker) for background jobs: OTA rollout batches, firmware checksum verification, log/analytics rollups, safety-event notification emails to parents. | Django request/response cycle is wrong place for long-running or scheduled work (e.g., "check all robots for pending OTA every hour"). Optional until Phase 18; not required for MVP conversation pipeline. |
| AR-8 | MQTT broker choice | **Mosquitto** for development and initial production (simpler ops); **EMQX** noted as the upgrade path only if the fleet grows large enough to need clustering/horizontal scaling or built-in rule engine. | Brief allows either; Mosquitto is the lower-complexity default for a 3-person team and small initial fleet. |
| AR-9 | Device identity format | Each robot gets a UUID `device_id` provisioned at manufacturing/first-boot, paired with an asymmetric keypair (device holds private key in NVS-encrypted storage; public key registered with backend) used for device authentication, distinct from any end-user JWT. | Standard IoT device-identity pattern; avoids shared secrets that can't be revoked per-device; supports secure credential rotation requirement from the brief. |
| AR-10 | Device↔Backend auth scheme | mTLS is the *ideal* but is flagged **optional/stretch** given team size; the **mandatory baseline** is: device holds a long-lived device private key + short-lived rotating device access token (JWT, ~1h TTL) obtained via a signed-challenge exchange over HTTPS, used as a Bearer token for REST and as the MQTT username/password (or client-cert if EMQX is later adopted) for MQTT. | Full mTLS on ESP32-S3 with ESP-IDF is achievable but adds real complexity (cert provisioning, rotation tooling) that a 3-person team may not have bandwidth for at MVP; the signed-challenge + rotating JWT gives strong-enough security now with a clear upgrade path. |
| AR-11 | Multi-child / profile model | One `Robot` belongs to one `Parent` account; a `Parent` can have multiple `ChildProfile`s; a robot session is associated with whichever `ChildProfile` is "active" on that robot (selectable via dashboard or a simple on-robot flow), defaulting to a single "default child" if the family doesn't configure multiple. | Brief mentions "child" generally but also "parent/admin controls" implying multi-child households are plausible; this keeps the data model realistic without over-engineering. |
| AR-12 | Language switching | Language (Bangla/English) is detected per-utterance where feasible (via STT language hint / detection) but also explicitly settable in dashboard/config as a default; ESP32 does not decide language — AI service does. | Matches "AI decides semantic intent" principle; keeps ESP32 dumb. |
| AR-13 | Offline content storage | Offline stories/quizzes/canned responses are shipped as a **versioned asset bundle** downloaded over HTTPS during online periods and stored in ESP32-S3 flash/SPIFFS/LittleFS, refreshed via the same OTA-adjacent metadata-and-download pattern as firmware (but a separate "content package," not firmware itself). | Keeps offline content updatable without a full firmware reflash; reuses the secure-download pattern already required for OTA instead of inventing a new one. |
| AR-14 | Raspberry Pi / edge AI tier | **Not adopted.** Documented in Section 22 as a future alternative only, per explicit brief instruction not to silently change the hardware requirement (ESP32-S3 as the robot compute). | Brief requires this be marked as alternative, not default. |
| AR-15 | Firmware signing | Firmware images are signed (Ed25519 or ECDSA) by a build-time signing step (CI/CD held key, not on any dev laptop); ESP32 verifies signature before flashing, using ESP-IDF's Secure Boot + built-in OTA image verification where hardware allows. | Brief mandates firmware authenticity verification; this is the standard ESP-IDF mechanism, not an invented one. |
| AR-16 | Personality/prompt ownership | System prompt / persona definition ("robot's personality") is authored and owned by the AI Developer inside the AI service, but its *safety constraints* (Section 17) are enforced as a separate, non-bypassable layer the AI Developer cannot silently weaken via prompt changes alone. | Keeps a hard safety boundary independent of prompt engineering mistakes. |
| AR-17 | Dashboard real-time updates | For MVP, dashboard reflects robot status via periodic REST polling (e.g., every 10–15s) rather than WebSockets/Channels. WebSocket-based live updates are marked **optional, Phase 17+ enhancement**. | Polling is dramatically simpler to build/test/secure with a 3-person team; can be upgraded later without changing the data model. |

---

## 4. Complete System Architecture

### 4.1 Final architecture (reviewed and finalized)

The brief's suggested architecture is directionally correct but too linear — it implies every request passes through Django twice in a simple chain and doesn't show MQTT vs HTTPS split, or where the AI service actually sits relative to Django. The finalized architecture below keeps Django as the single integration hub (all traffic — including AI results — flows through it; nothing bypasses it) and makes the two transports explicit:

```
                         ┌────────────────────────┐
                         │        Child            │
                         └───────────┬─────────────┘
                                     │ voice / button
                         ┌───────────▼─────────────┐
                         │   ESP32-S3 Firmware      │
                         │  (wake word, VAD, mic,   │
                         │   speaker, servo, LED,   │
                         │   sensors, battery, OTA) │
                         └─────┬───────────────┬────┘
                    HTTPS/TLS  │               │  MQTT/TLS
                (audio, config,│               │(status, commands,
                 registration, │               │ events, heartbeat)
                 conversation) │               │
                         ┌─────▼───────────────▼────┐
                         │      Django Backend       │
                         │  (REST API, device mgmt,  │
                         │  auth, DB, orchestration,  │
                         │  parent/admin dashboard)   │
                         └─────┬───────────────▲─────┘
                   internal HTTP│               │ internal HTTP
                   (server-side)│               │ (result callback)
                         ┌─────▼───────────────┴─────┐
                         │        AI Service          │
                         │ STT → Lang detect → Context │
                         │ → LLM → Safety → Emotion/  │
                         │  Action → TTS               │
                         └────────────────────────────┘

                         ┌────────────────────────┐
                         │   MQTT Broker (Mosquitto)│◄── Django publishes commands
                         │                          │◄── ESP32 publishes status/events
                         └──────────┬───────────────┘
                                     │
                              ESP32 subscribes to its own
                              robots/{id}/command, /config, /ota topics
```

Key finalization decisions vs. the brief's draft:
1. **AI Service never talks to ESP32 directly, and never talks to MQTT directly.** All AI results return to Django first; Django decides what (if anything) to publish to MQTT and what to store. This preserves "Backend as central device/application management layer" and gives one place to enforce safety/logging before anything reaches the robot.
2. **Audio does not travel over MQTT** (see AR-1). Voice flow is HTTPS end-to-end (upload question audio → Django proxies/streams to AI service → AI service returns text+audio → Django returns to ESP32 over the same HTTPS request/response or a follow-up HTTPS download).
3. **MQTT is reserved for**: robot status/heartbeat, real-time commands that aren't part of the voice pipeline (e.g., "wave now" triggered from dashboard), lifecycle events (booted, error, low battery), and OTA/config *notifications* (the actual firmware/config *payloads* are fetched via HTTPS; MQTT just says "something changed, go fetch it" — see Section 11).
4. **The AI Service is a peer service to Django**, not a submodule of it — separate deployable container, separate codebase, called over an internal-only HTTP API (not exposed to the internet), so AI Developer and Software Developer can work independently per the team split.

### 4.2 Deployment topology

```
┌─────────────────────────── Docker Compose / VPS ───────────────────────────┐
│                                                                              │
│  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐  ┌────────────┐ │
│  │ nginx (TLS     │   │ django (gunicorn│  │ ai-service     │  │ mosquitto  │ │
│  │ termination,   │──▶│ /uvicorn)       │─▶│ (fastapi/uvicorn)│  │ (MQTT+TLS) │ │
│  │ reverse proxy) │   │                │◀─│                │  └────────────┘ │
│  └───────┬────────┘   └───────┬────────┘   └───────────────┘                │
│          │                     │                                             │
│          │              ┌──────▼──────┐   ┌────────────┐  ┌───────────────┐ │
│          │              │ postgresql   │   │  redis     │  │ celery worker │ │
│          │              └─────────────┘   └────────────┘  └───────────────┘ │
│          │                                                                   │
│  ┌───────▼────────┐                                                         │
│  │ static/media     │  (firmware images, content bundles, TLS certs volume) │
│  └─────────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────────┘
```
Internet-exposed: nginx (443 for HTTPS, 8883 for MQTT/TLS proxied or mosquitto directly with its own TLS listener). Django, AI service, Postgres, Redis, Celery are **not** directly internet-exposed — only reachable inside the Compose network.

---

## 5. Component Responsibilities

For every major component: what/why/where/language/owner/receives/sends/callers/protocol/data format/auth/error handling/failure behavior/testing — condensed into the table below, with the highest-risk components (ESP32 firmware core, Django API, AI service) expanded further in their own sections (7, 12, 14).

| Component | What it does | Why it exists | Where it runs | Language | Owns it | Receives | Sends | Called by | Protocol | Auth | Failure behavior |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ESP32 Firmware | Hardware control, audio capture/playback, wake word, local state machine, offline fallback | Physical interface to the child; only thing that touches hardware | On-device | C/C++, ESP-IDF | Electronics + Software (interface) | MQTT commands, HTTPS config/audio responses | HTTPS audio/status, MQTT status/events | Child (physically), Django (via config/OTA), self (state machine) | HTTPS+MQTT (both TLS) | Device JWT + device keypair | Falls to OFFLINE/ERROR state, retries with backoff, never bricks |
| Django Backend | Central orchestration: device mgmt, auth, DB, REST API, dashboard, proxies AI requests, publishes MQTT commands | Single source of truth; keeps AI and hardware decoupled | VPS/cloud container | Python/Django/DRF | Software Developer | HTTPS from ESP32 & browsers, internal HTTP from AI service | HTTPS responses, MQTT publishes, internal HTTP calls to AI service | ESP32, parents, admins, AI service (callback) | HTTPS (external), internal HTTP (to AI service), MQTT publish (via broker client) | Session/JWT (users), device JWT (robots), service token (AI service) | Returns proper HTTP error codes; queues retries for MQTT publish failures; never silently drops a request |
| AI Service | STT→understanding→LLM→safety→emotion/action→TTS pipeline | Isolates AI/ML logic and provider secrets from hardware and from public internet | Same VPS/cloud, internal network only | Python/FastAPI | AI Developer | Internal HTTP request (audio or text + context) from Django | Structured AI Response Contract (Section 15) | Django only | Internal HTTP (never public) | Internal service token/shared secret, not exposed externally | Returns a structured error object; Django decides fallback (e.g., "I didn't quite get that" canned response) |
| PostgreSQL | Persistent store for all entities in Section 13 | Durable source of truth | Same VPS/cloud (or managed DB) | SQL (via Django ORM) | Software Developer | Django ORM queries only | Query results | Django only | — | DB credentials (env/secrets manager), network-restricted to backend only | Django catches DB errors, returns 503, logs, does not crash |
| MQTT Broker (Mosquitto) | Real-time pub/sub for status, commands, events, change-notifications | Low-latency, persistent-connection channel robots need for near-real-time behavior | Same VPS/cloud | N/A (broker) | Software Developer | Publishes from Django + ESP32 | Delivers to subscribers per topic ACL | Django (publisher/subscriber), ESP32 (publisher/subscriber) | MQTT over TLS | Per-device username/password (from device JWT/token) + topic ACL | Retained messages preserve last-known state; ESP32 reconnect logic re-subscribes |
| Redis (optional, AR-6) | Ephemeral cache: online-flags, short-term context cache, Celery broker | Performance + async task backing store | Same VPS/cloud | N/A | Software Developer | Reads/writes from Django & Celery | — | Django, Celery workers | Internal TCP | Password-protected, network-restricted | Non-critical: system degrades to DB-only behavior if Redis is down (must be designed as a cache, not a source of truth) |
| Celery Workers (optional, AR-7) | Background/scheduled jobs: OTA batching, log rollups, safety-event emails | Keeps long-running work off the request/response cycle | Same VPS/cloud | Python | Software Developer | Tasks queued by Django | Task results, side effects (emails, DB writes) | Django (enqueue) | Internal (Redis broker) | Internal only | Failed tasks retried with backoff; alerts on repeated failure |
| Web Dashboard (Parent) | Robot pairing, status, controls, settings, history | Only interface team can ship without a mobile dev | Browser, served by Django Templates | HTML/CSS/JS + Django views | Software Developer | User input via forms/AJAX | HTTPS requests to Django REST/API | Parent | HTTPS | Session/JWT | Graceful error banners, no silent failures |
| Web Dashboard (Admin) | Fleet mgmt, logs, safety events, analytics, config, firmware | Operational control plane | Browser, served by Django Templates | HTML/CSS/JS + Django views | Software Developer | Admin input | HTTPS requests to Django REST/API | Admin/staff | HTTPS | Staff session/JWT + role check | Same as above |

---

## 6. Hardware ↔ Firmware Interface

This is the contract between the Electronics Engineer's board and the Software Developer's firmware modules. It does not specify component part numbers (Electronics Engineer's decision) — it specifies the **signal/data contract** each hardware element must expose to firmware.

| Hardware element | Firmware-facing interface | Data direction | Notes / constraints on Electronics side |
|---|---|---|---|
| Microphone | I2S (typical for ESP32-S3 digital MEMS mic) audio frames, fixed sample rate (e.g., 16kHz mono, confirm with AI STT requirements) | Hardware → Firmware | Must supply a stable, documented sample rate/bit-depth; firmware `audio/microphone` module assumes whatever electronics finalizes and exposes it as a config constant, not hardcoded in three places |
| Speaker/Amplifier | I2S or DAC output, firmware pushes PCM/decoded audio frames | Firmware → Hardware | Must confirm max buffer size / underrun behavior so `audio/speaker` module can size buffers correctly |
| Servo motors (head/arm) | PWM via ESP32-S3 LEDC peripheral, one channel per servo, firmware sends angle targets | Firmware → Hardware | Electronics must supply safe angle range per joint (min/max) so firmware `servo` module can clamp — **firmware never trusts backend-sent angles blindly; it clamps to hardware-safe ranges regardless of what AI/backend requested** |
| LEDs / eye display | GPIO (simple LEDs) or SPI/I2C (if a small display/LED matrix is used) | Firmware → Hardware | Electronics confirms which; firmware `led` module abstracts "show HAPPY" without callers caring about GPIO vs SPI underneath |
| Sensors (touch/IR/etc., if present) | GPIO/ADC/I2C, polled or interrupt-driven | Hardware → Firmware | Exact sensor set is Electronics' decision; firmware `sensors` module exposes a generic event bus (`sensor_event(type, value)`) so new sensors don't require touching unrelated modules |
| Battery / power monitoring | ADC voltage read or fuel-gauge IC via I2C | Hardware → Firmware | Electronics confirms whether a fuel-gauge IC (preferred, gives %) or raw ADC (firmware must implement voltage curve estimation) is used |
| Charging/protection circuit | GPIO status pin(s) (charging/charged/fault) | Hardware → Firmware | Firmware `battery` module reads status pins; does not control charging logic itself |
| Physical body / button (if any) | GPIO input, debounced in firmware | Hardware → Firmware | E.g., a manual "push to talk" button as wake-word fallback |

**Contract rule (binding on both sides):** any change to a physical pin mapping, sample rate, or angle range must be reflected in a single shared `hardware_config.h` (or equivalent) that Electronics and Software both review — firmware modules never hardcode hardware assumptions inline.

---

## 7. ESP32 Functional Architecture

Modules are conceptual (naming may change during implementation) but each module's boundary below is binding: a coding agent must not merge these responsibilities or leak one module's concern into another (e.g., no AI/business logic inside `audio/`, no direct hardware register access outside `hal/`).

```
firmware/
    hal/            (hardware abstraction: mic, speaker, servo, led, sensors, battery — thin drivers only)
    audio/          (capture pipeline, playback pipeline, buffering, codec)
    voice/          (wake word, VAD — decides WHEN to start/stop sending audio)
    network/
        wifi/       (provisioning, connect, reconnect, RSSI monitoring)
        http/       (REST client: registration, auth, config fetch, audio upload/download, firmware/content download)
        mqtt/       (connect, subscribe, publish, topic routing, reconnect/backoff)
    security/       (device keypair storage, token storage/refresh, TLS cert handling)
    identity/       (device_id, provisioning state)
    state/          (robot state machine — Section 8 — single owner of "what state am I in")
    actions/        (interprets semantic emotion/action commands into servo/LED sequences)
    configuration/  (local config cache, applies config received from backend)
    ota/            (firmware update check, download, verify, apply, rollback)
    content/        (offline content bundle: stories/quizzes/canned responses, versioned)
    diagnostics/    (heartbeat, status reporting, local logging, crash/error capture)
    app/            (top-level orchestrator wiring the above together; the only module allowed to call across domains)
```

| Module | Responsibility | Inputs | Outputs | Depends on | Comms | State held | Error handling | Testing |
|---|---|---|---|---|---|---|---|---|
| `hal/*` | Raw hardware I/O only | GPIO/I2S/PWM/ADC signals | Raw sensor values / actuator writes | Electronics interface (Sec. 6) | None (local bus only) | None (stateless drivers) | Return error codes on I2S/PWM failure; never crash the task | Bench test each driver in isolation with known hardware |
| `audio/capture` | Buffer mic frames into utterance chunks | I2S frames from `hal/mic` | Encoded audio chunks | `hal/`, `voice/` (told when to start/stop) | Internal queue to `network/http` | Current recording buffer | Buffer overflow → drop oldest + log, never block audio task | Unit test with recorded I2S sample fixtures |
| `audio/playback` | Decode + play TTS audio | Audio bytes from `network/http` | PCM to `hal/speaker` | `hal/` | Internal queue from `network/http` | Playback position | Underrun → insert silence, log, don't crash | Play known WAV fixture, verify output timing |
| `voice/wakeword` | Detect wake phrase locally | Continuous mic stream | "wake detected" event | `hal/mic`, small on-device model | Internal event to `state/` | Rolling audio window | False negative just means try again (no error state needed) | Test with wake/non-wake audio corpus |
| `voice/vad` | Detect end-of-utterance | Mic stream during LISTENING state | "utterance complete" event | `audio/capture` | Internal event to `state/` | Silence-duration counter | Timeout cap (e.g., 8s max) to avoid infinite listening | Test with varied pause lengths |
| `network/wifi` | Provision, connect, monitor, reconnect | SSID/password (from provisioning flow), backend config | Connection status events | `hal/` (if any Wi-Fi indicator LED) | Wi-Fi (802.11) | Current SSID, connection state, retry count | Exponential backoff reconnect; after N failures → OFFLINE state | Simulate AP loss, verify reconnect timing |
| `network/http` | All REST calls: register, auth, config fetch, audio up/down, firmware/content download | Requests from other modules (queued) | HTTPS responses routed back to caller | `network/wifi`, `security/` | HTTPS/TLS | Active request queue, retry state | Timeout/retry with backoff; surfaces failure to caller module, which decides fallback | Mock server integration tests per endpoint |
| `network/mqtt` | Connect, subscribe to own topics, publish status/events, route incoming commands | Backend-published messages | Status/event publishes | `network/wifi`, `security/` | MQTT/TLS | Subscription state, last-will config | Reconnect w/ backoff; re-subscribe on reconnect; queue outgoing publishes while disconnected (bounded) | Integration test against local Mosquitto with induced disconnects |
| `security/` | Store device keypair, manage token refresh, TLS trust | Provisioning-time keypair, token responses | Signed requests, valid Bearer tokens | ESP-IDF NVS encryption, `network/http` | — | Current token + expiry | Token expired → refresh proactively before expiry; refresh failure → re-authenticate flow | Test token refresh timing, expired-token rejection handling |
| `identity/` | Track device_id and provisioning status | Factory-provisioned data or first-boot flow | device_id to all other modules needing it | `security/` | — | Provisioned yes/no | Unprovisioned device refuses to leave BOOT/setup flow | Test fresh-flash boot path |
| `state/` | Robot state machine (Section 8), single authority on current state | Events from voice/, network/, actions/, sensors | State-change notifications to all modules | All modules (event bus) | — | Current state, previous state, error context | Unhandled event in a state = logged + ignored (never crash) | State-machine unit tests: every transition table row |
| `actions/` | Convert semantic emotion/action commands into concrete servo/LED sequences (clamped to hardware-safe ranges) | `{emotion, action}` from backend response | Servo PWM targets, LED patterns | `hal/servo`, `hal/led`, hardware-safe range config (Sec. 6) | — | Current action sequence in progress | Unknown action code → fallback to neutral/idle animation, log | Test each known action code produces expected servo/LED sequence; test unknown code fallback |
| `configuration/` | Cache and apply backend-pushed config (volume, language default, parental limits, etc.) | Config payload via HTTP fetch (triggered by MQTT notify) | Applied settings to relevant modules | `network/http`, `network/mqtt` (notify trigger) | — | Current config version | Invalid config payload → reject, keep previous config, log + report error status | Test apply of valid/invalid config payloads |
| `ota/` | Check, download, verify, apply, rollback firmware | Firmware metadata + binary via HTTP (triggered by MQTT notify) | New running firmware or rollback | `network/http`, `security/` (signature verify), ESP-IDF OTA partition APIs | — | Update-in-progress flag, target version | Verify failure → abort, do not flash, report error; boot failure post-flash → automatic rollback via ESP-IDF's OTA rollback support | Test with valid signed image, tampered image, and corrupted download |
| `content/` | Store/apply versioned offline content bundle | Bundle via HTTP (triggered by MQTT notify or periodic check) | Story/quiz/canned-response data to `state/`/offline logic | `network/http` | Bundle version | Corrupt bundle → keep previous, log | Test bundle apply/rollback |
| `diagnostics/` | Heartbeat, status publish, local log ring-buffer, crash capture | Internal events from all modules | MQTT status publishes, local log storage | `network/mqtt`, all modules (log calls) | — | Recent log ring buffer | Never let logging itself cause a crash/blocking | Verify heartbeat cadence, verify crash log survives reboot |
| `app/` | Top-level orchestrator; wires modules together at boot | Boot | Initialized system | All modules | — | Boot sequence progress | Any module init failure → log, attempt to continue in degraded mode where possible, else ERROR state | Full boot integration test |

---

## 8. Robot State Machine

### 8.1 States (finalized)

The brief's candidate list is adopted with one addition (`SETUP`, for first-time Wi-Fi/pairing flow, distinct from normal `CONNECTING`) and one clarification (`PROCESSING` vs `SPEAKING` vs `MOVING` are allowed to overlap conceptually — the robot can move *while* speaking — see 8.3):

```
BOOT → SETUP (first boot only) → CONNECTING → AUTHENTICATING → ONLINE
ONLINE splits into: IDLE ⇄ LISTENING ⇄ PROCESSING ⇄ SPEAKING (MOVING can layer on top of SPEAKING/IDLE)
Side states reachable from most ONLINE substates: OFFLINE, LOW_BATTERY, ERROR, OTA_UPDATE, SLEEP
```

| State | Meaning | Entered from | Robot behavior in this state |
|---|---|---|---|
| `BOOT` | Power-on init, hardware self-test | Power on / reset | Init HAL, run self-test, load NVS config |
| `SETUP` | First-time provisioning: Wi-Fi credentials + device pairing to a parent account | `BOOT` if unprovisioned | Local AP or BLE provisioning flow (Flow 3), LED indicates setup mode |
| `CONNECTING` | Establishing Wi-Fi | `BOOT` (if provisioned) / after Wi-Fi loss | LED "connecting" pattern; retry with backoff |
| `AUTHENTICATING` | Exchanging/refreshing device credentials with backend | `CONNECTING` success | Brief; no user-visible interaction expected |
| `ONLINE` (parent state) | Normal operating umbrella; children below | `AUTHENTICATING` success | — |
| `IDLE` | Awake, waiting for wake word or button | Entry to `ONLINE`, or return from `SPEAKING`/`PROCESSING` | Passive listening for wake word only; idle eye animation |
| `LISTENING` | Actively capturing an utterance | Wake word or button press, from `IDLE` | Recording indicator (LED), streaming/buffering audio, VAD watching for end-of-utterance |
| `PROCESSING` | Utterance sent, awaiting AI response | VAD "utterance complete", from `LISTENING` | "Thinking" animation; audio upload + wait for response |
| `SPEAKING` | Playing TTS response | Response received, from `PROCESSING` | Plays audio; `MOVING` may layer (Sec 8.3) if action included |
| `MOVING` | Executing a semantic action/emotion sequence | Action present in AI response, or MQTT-triggered direct command (e.g. dashboard "make it wave") | Runs servo/LED sequence; can run concurrently with `SPEAKING` |
| `OFFLINE` | No connectivity | Any `ONLINE` substate, after Wi-Fi/backend unreachable past threshold | Falls back to local content (Section 22); keeps retrying connection in background |
| `LOW_BATTERY` | Battery below threshold | Any state, on battery event | Reduced functionality (Flow 23), warns child/parent, may force `SLEEP` at critical threshold |
| `ERROR` | Unrecoverable local fault | Any state, on unhandled hardware/software fault | Safe-idle posture, LED error pattern, logs, publishes error status if connectivity allows, attempts recovery per Section 30 |
| `OTA_UPDATE` | Firmware update in progress | `IDLE` only (never interrupts an active conversation) — triggered by MQTT notify + user/parent-scheduled window | Pauses normal interaction, shows update animation, reboots on success |
| `SLEEP` | Low-power state | Idle timeout, parent-scheduled quiet hours, or critical low battery | Wake word detection may still run at reduced duty cycle, or fully off pending button press (config-dependent) |

### 8.2 Transition rules

- **Interruptions:** `LISTENING`/`PROCESSING`/`SPEAKING` can all be interrupted by: a new wake word (barge-in → restart at `LISTENING`, cancelling prior request), `LOW_BATTERY` critical threshold, or `ERROR`. `OTA_UPDATE` can **never** interrupt an active conversation — it only starts from `IDLE`.
- **After errors:** `ERROR` always attempts a bounded auto-recovery (module re-init, reconnect) before requiring a full reboot; if recovery fails N times, robot stays in `ERROR` and reports via any available channel (MQTT if connectivity permits, else local log for later report), and shows a persistent "help me" LED pattern for the parent.
- **After reboot:** always re-enters `BOOT` → checks provisioning (`SETUP` if unprovisioned) → `CONNECTING` → resumes `AUTHENTICATING`/`ONLINE`. No state is persisted across reboot except provisioning/config — the robot never "resumes" a mid-conversation state after a crash; it starts clean at `IDLE`.
- **Final/rest state:** `IDLE` is the canonical resting state whenever nothing else is happening; `SLEEP` is the canonical rest state during configured quiet hours or extended inactivity.

### 8.3 Concurrency note (clarifying the brief's flat state list)

Emotion/action display and speech playback are not mutually exclusive in a natural-feeling robot. This plan treats `MOVING` as an **overlay flag** the `actions/` module manages, not a strictly exclusive state — `state/` machine's *primary* state can be `SPEAKING` while `actions/` concurrently drives servos for the accompanying `action`. This is called out explicitly because the brief listed them as flat peers; a coding agent must not implement them as mutually exclusive without this note.

---

## 9. Network Architecture

### 9.1 Why two protocols, and the final split

The brief's draft correctly separates "HTTPS for transactional" vs "MQTT for real-time," but its example put "robot status" and "movement commands" under MQTT while leaving audio unassigned. This plan finalizes the full split:

**HTTPS/REST — used for:**
- Device registration & pairing (Flow 4)
- Device authentication / token issuance & refresh (Flow 5)
- Configuration payload fetch (full config body — MQTT only carries the "config changed" notification)
- Firmware metadata + binary download (OTA payload itself)
- Offline content bundle download
- Audio upload (child's utterance) and audio/text response download — the entire voice pipeline's payload transport
- Conversation history retrieval (dashboard)
- All parent/admin dashboard interactions
- Analytics/log submission (batched, non-real-time)

**MQTT — used for:**
- Robot heartbeat / online-offline presence (via MQTT Last Will and Testament + periodic heartbeat publish)
- Robot status snapshot publish (battery %, current state, Wi-Fi RSSI) — periodic + on-change
- Real-time direct commands not part of the voice pipeline (e.g., dashboard-triggered "wave," "say hello," "go to sleep now")
- Lifecycle/event notifications (booted, entered ERROR, low battery crossed threshold, OTA started/finished)
- Change-notifications that tell the robot to go fetch something over HTTPS: `config changed → fetch /api/config`, `firmware available → fetch /api/firmware/metadata`, `content bundle updated → fetch bundle`

This "MQTT notifies, HTTPS delivers the payload" pattern (used for config, OTA, and content) keeps MQTT messages small and keeps the large-payload/authenticated-download logic in one place (the existing HTTPS client) instead of building large-file transfer over MQTT.

### 9.2 Connectivity resilience

- ESP32 maintains Wi-Fi connection independently of both HTTPS and MQTT sessions; MQTT reconnects automatically on top of an existing Wi-Fi link; HTTPS requests are made on-demand and don't hold a persistent connection.
- MQTT keep-alive interval and reconnect backoff are configuration-driven (delivered via the config payload), not hardcoded, so operations can tune them without a firmware release.

---

## 10. HTTPS Architecture

- **TLS:** TLS 1.2+ required; ESP32-S3 (via ESP-IDF `esp-tls`/mbedTLS) validates the backend's certificate against an embedded root CA (or a small trusted CA bundle) — no certificate validation is disabled in production builds under any circumstance (a debug-only, clearly flagged build flag may relax this for lab bench testing but must never ship).
- **Base URL / versioning:** All REST endpoints versioned (`/api/v1/...`) so firmware and backend can evolve independently — a coding agent must not silently change or remove a versioned endpoint's contract without a new version.
- **Auth header:** `Authorization: Bearer <device_access_token>` on every request after `AUTHENTICATING` succeeds (see Section 26 for how the token is obtained).
- **Idempotency:** State-changing endpoints (e.g., status report) accept a client-generated request ID so a retried request after a timeout doesn't double-apply. Concretized for the Phase 12 audio upload (2026-10-13 decision record below): the ID travels in an `Idempotency-Key` header (keyed per robot) with a retention TTL of **600 seconds (10 minutes)**; within that window a duplicate with an identical payload replays the stored response instead of reprocessing the clip, and the replay response is marked with **`Idempotency-Replayed: true`**, while the same robot/key reused with a different payload is **`409 Conflict`**; a replay shares the caller's normal conversation-turn rate limit (it is not a throttle-free path); the store is process-local (in-memory) in the synchronous MVP — exact only for a single-process deployment, and a multi-worker deployment needs a shared store (same accepted limitation as the turn throttle).
- **Timeouts & retry:** Every `network/http` call has a bounded timeout (config-driven default, e.g. 10s for small payloads, longer for audio/firmware) and a bounded retry count with exponential backoff; callers (not the HTTP module itself) decide what "give up" means for their use case (e.g., voice pipeline gives up and shows a "try again" cue; config fetch gives up and keeps prior config).
- **Payload format:** JSON for all control/data endpoints; audio endpoints use `multipart/form-data` with a single `audio` file part plus a JSON sidecar carried in an HTTP header (`Conversation-Sidecar`) with `conversation_id`, `language_hint`, `child_profile_id` (Phase 12 settles the earlier "header or preceding small JSON request" choice to the header, and deliberately **omits `mode`** — feature flows remain a text-turn concern until the mode-specific pipeline exists). Firmware/content downloads are raw binary with a checksum returned in metadata for post-download verification.

---

## 11. MQTT Architecture

### 11.1 Topic hierarchy (finalized)

```
robots/{device_id}/status          — ESP32 publishes, Django subscribes   (retained, QoS 1)
robots/{device_id}/heartbeat       — ESP32 publishes (LWT-backed), Django subscribes  (QoS 0, not retained)
robots/{device_id}/event           — ESP32 publishes (lifecycle/error/safety-relevant events), Django subscribes (QoS 1)
robots/{device_id}/command         — Django publishes, ESP32 subscribes   (QoS 1, not retained — commands are momentary)
robots/{device_id}/config/notify   — Django publishes ("config changed, new version=N"), ESP32 subscribes (QoS 1, retained = latest version marker)
robots/{device_id}/ota/notify      — Django publishes ("firmware available, version=N"), ESP32 subscribes (QoS 1, retained)
robots/{device_id}/content/notify  — Django publishes ("content bundle updated, version=N"), ESP32 subscribes (QoS 1, retained)
```

### 11.2 Behavior details

- **Publish/Subscribe ownership:** ESP32 publishes only to its own `status`, `heartbeat`, `event` topics — never to another device's topic (enforced by ACL, not just convention). Django publishes only to `command`, `config/notify`, `ota/notify`, `content/notify` — Django never publishes fake `status`/`event` on a robot's behalf.
- **QoS:** QoS 1 (at-least-once) for anything that must not be silently lost (status, event, command, notify topics). QoS 0 for heartbeat, since heartbeats are frequent and a single missed one is meaningless (the next one arrives shortly).
- **Retained messages:** `status` is retained so a newly-subscribing dashboard/backend session immediately sees last-known state without waiting for the next publish. `config/notify`, `ota/notify`, `content/notify` are retained (as a version marker) so a robot that was offline when the notify was first sent still sees it on reconnect/subscribe.
- **Acknowledgment:** QoS 1 gives broker-level ack; **application-level** acknowledgment for `command` (e.g., "did the robot actually wave?") is handled by the robot publishing a corresponding `event` (e.g., `action_completed`) — MQTT ack alone only proves delivery to the broker, not execution, and this plan does not conflate the two.
- **Reconnect behavior:** On reconnect, ESP32 re-subscribes to all its topics, re-publishes current `status` (not just waits for the next periodic tick), and checks retained `*/notify` topics for anything missed while offline.
- **Authorization / topic permissions:** Broker ACL (Mosquitto `acl_file` or EMQX rule engine) restricts each device's MQTT credentials to only `robots/{its_own_device_id}/#`; Django's service-level MQTT credentials have broker-wide publish rights to `robots/+/command`, `robots/+/config/notify`, `robots/+/ota/notify`, `robots/+/content/notify` and subscribe rights to `robots/+/status`, `robots/+/heartbeat`, `robots/+/event`. No client ever gets wildcard `#` publish rights.
- **Last Will and Testament (LWT):** Each device connects with an LWT message on `robots/{device_id}/status` set to `{"online": false}`, so an ungraceful disconnect (crash, power loss, Wi-Fi drop) is immediately visible to Django/dashboard without waiting for a heartbeat timeout.

---

## 12. Django Backend Architecture

### 12.1 App/module structure (conceptual, Django apps)

```
backend/
    accounts/        (User, Parent, ChildProfile, auth views, permissions)
    devices/         (Robot, DeviceCredential, registration, pairing, status ingestion)
    conversations/   (Conversation, Message, AIInteraction — orchestrates calls to AI service)
    memory/           (Memory entries, context retrieval logic exposed to AI service via internal API)
    configuration/    (Configuration model + versioning + push-notify trigger)
    firmware/         (Firmware, OTAUpdate models, signing metadata, rollout logic)
    content/          (Offline content bundle versioning/delivery)
    safety/           (SafetyEvent logging, parent notification triggers)
    analytics/        (UsageLog, AuditLog, aggregation views for dashboard)
    dashboard_parent/ (Django Template views for parent-facing pages)
    dashboard_admin/  (Django Template views for admin-facing pages)
    mqtt_bridge/      (Django-side MQTT client: publisher + subscriber, translates DB/API events ⇄ MQTT topics)
    core/             (shared settings, middleware, common permissions/auth classes, health checks)
```

### 12.2 Responsibilities boundary

- **REST API (`devices/`, `conversations/`, `configuration/`, `firmware/`, `content/`) is the only write path into the DB from outside Django itself.** The MQTT bridge does not write to the DB directly on receipt of a message without going through the same model/service layer the REST API uses — this avoids two different code paths for "device status changed" (one from HTTP, one from MQTT) drifting apart.
- **`mqtt_bridge/` is a thin translation layer**: on inbound MQTT (`status`, `heartbeat`, `event`), it calls the same internal service functions the REST API would call (e.g., `devices.services.update_status(...)`); on outbound, REST/admin actions call `mqtt_bridge.publish_command(...)` etc. rather than any view touching the MQTT client directly.
- **AI orchestration lives in `conversations/`**: a view receives the child's audio upload, creates a `Conversation`/`Message` record, calls the AI service's internal HTTP API with the audio + retrieved context (via `memory/`), receives the structured AI Response Contract (Section 15), persists it, runs a Django-side safety-event check on the returned `safety` field (belt-and-suspenders logging even though the AI service already filtered), and returns the TTS audio + emotion/action payload to the robot in the same HTTPS response cycle (or a short-poll follow-up if AI latency requires async handling — see Flow 7).
- **Django never calls an external LLM/STT/TTS provider directly.** All of that is behind the AI service's internal API, keeping provider secrets out of the Django codebase entirely.

---

## 13. Database Architecture

### 13.1 Entities, purpose, ownership, lifecycle

| Entity | Purpose | Key relationships | Important fields (conceptual) | Ownership | Lifecycle | Retention / privacy note |
|---|---|---|---|---|---|---|
| `User` | Login identity for parents/admins | 1—1 `Parent` (if role=parent) | email, password hash, role, is_staff | accounts | Created at signup; soft-deleted on account closure | Standard auth PII |
| `Parent` | Parent/guardian account | 1—N `ChildProfile`, 1—N `Robot` | display name, contact prefs | accounts | Tied to `User` lifecycle | Contact info only shared with own children's data |
| `ChildProfile` | A child using the robot | N—1 `Parent`, 1—N `Conversation` | first name only (no full PII required), age band (not exact birthdate unless parent opts in), language preference | accounts | Created/edited by parent; deletable by parent (cascades per Section 30 privacy rules) | **High sensitivity — minimize fields collected; age band not exact DOB where avoidable** |
| `Robot` | A physical device | N—1 `Parent` (once paired), 1—N `Conversation`, 1—1 current `Configuration` | device_id (UUID), nickname, pairing status, current firmware version, last_seen | devices | Created at registration (Flow 4); pairing changes ownership | Not child PII itself, but links to it |
| `DeviceCredential` | Device's auth material (public key, current token metadata) | 1—1 `Robot` | public key, token hash/expiry, rotation history | devices | Rotated per Section 26 | Security-sensitive; private key never stored server-side |
| `Conversation` | One session of interaction (may span multiple turns) | N—1 `Robot`, N—1 `ChildProfile`, 1—N `Message` | started_at, ended_at, language | conversations | Created on first utterance of a session; closed on timeout/explicit end | Retention policy per Section 30 |
| `Message` | One turn (child utterance or robot reply) | N—1 `Conversation` | role (child/robot), text transcript, audio reference (if retained), timestamp | conversations | Immutable once created | Audio retention is opt-in/limited — see Section 30 |
| `AIInteraction` | Metadata about one AI service call (not the content itself, which lives in `Message`) | 1—1 `Message` (the robot's reply) | latency, model/version used, safety flags, emotion/action returned | conversations | Created per AI call | Used for debugging/analytics, not shown verbatim to parents unless relevant to a safety event |
| `Memory` | Durable facts AI can recall across conversations (e.g., "child's favorite animal is elephant") | N—1 `ChildProfile` | key, value, source_conversation, created_at, expires_at (nullable) | memory | Written by AI service (via Django's internal API) after a conversation; readable at context-retrieval time | Parent-visible and parent-deletable (Section 30) |
| `RobotState` | Latest known device state snapshot | 1—1 `Robot` | current firmware state-machine state, battery %, online flag, RSSI, last_updated | devices | Upserted on every status/heartbeat | Not PII |
| `RobotEvent` | Discrete lifecycle/error events | N—1 `Robot` | event_type, payload, timestamp | devices | Append-only log | Retained per operational log policy |
| `Configuration` | Versioned config payload for a robot | 1—1 `Robot` (current), historical versions kept | volume, language default, quiet hours, parental limits, version number | configuration | New version created on any change; old versions retained for audit | Not PII beyond settings |
| `Firmware` | A firmware build/version | referenced by `OTAUpdate` | version, checksum, signature, release notes, min-hardware-rev | firmware | Created by CI/CD publish step | Not PII |
| `OTAUpdate` | A specific rollout of firmware to a specific robot | N—1 `Robot`, N—1 `Firmware` | status (pending/downloading/applied/failed/rolled_back), started_at, completed_at | firmware | Created when rollout triggered; updated by status events from robot | Not PII |
| `SafetyEvent` | A flagged unsafe interaction | N—1 `Conversation`/`Message`, N—1 `ChildProfile` | category, severity, action_taken, reviewed_by | safety | Created by AI service's safety filter output being logged by Django | **High sensitivity — restricted access, longer retention for safety/audit purposes even if conversation content is otherwise purged (Section 30)** |
| `UsageLog` | Aggregate usage stats (for parent dashboard "learning topics," time spent, etc.) | N—1 `ChildProfile` | date, minutes_active, topics_covered, quiz_scores | analytics | Rolled up (daily) by Celery job or on-write aggregation | Parent-visible; not shared beyond own account |
| `SensorData` | Optional raw sensor readings if a feature needs history (e.g., battery curve over time) | N—1 `Robot` | sensor_type, value, timestamp | devices | Append-only, may be downsampled/pruned over time | Not PII |
| `AuditLog` | Security/admin-relevant actions (who changed what config, who accessed a safety event) | N—1 `User` (actor) | action, target, timestamp, ip | core | Append-only | Retained per compliance policy, access restricted to admins |

### 13.2 ER-style relationship summary

```
User ──1:1── Parent ──1:N── ChildProfile ──1:N── Conversation ──1:N── Message ──1:1── AIInteraction
                 │                                     │
                 └──1:N── Robot ──1:1── DeviceCredential │
                             │                            │
                             ├──1:1── RobotState           │
                             ├──1:N── RobotEvent            │
                             ├──1:N── Configuration (versioned)
                             ├──1:N── OTAUpdate ──N:1── Firmware
                             └──1:N── SensorData

ChildProfile ──1:N── Memory
ChildProfile ──1:N── SafetyEvent ──N:1── Conversation/Message
ChildProfile ──1:N── UsageLog
User ──1:N── AuditLog (as actor)
```

---

## 14. AI Service Architecture

### 14.1 Pipeline (as specified, adopted with one clarification)

```
Audio/text in
   ↓
STT (if audio in)
   ↓
Language detection
   ↓
Conversation understanding (intent parsing)
   ↓
Context retrieval (calls Django's internal memory/conversation-history API)
   ↓
LLM (generates response, grounded in context + persona + safety-constrained system prompt)
   ↓
Safety filter
   ↓
Response finalization (text)
   ↓
Emotion classification + Action selection (semantic, not physical)
   ↓
TTS
   ↓
Structured AI Response Contract returned to Django
```

### 14.2 Safety placement: before or after the LLM, and why

**Both — this is deliberately a two-stage filter, not a single placement choice:**
1. **Pre-LLM (input-side) safety/guardrail check** on the *understood intent* (post-STT, post-understanding, pre-generation): catches requests that should never even be sent to the LLM for a response (e.g., a request for personal information, or a request the system should redirect rather than answer). This reduces the chance the LLM generates something risky in the first place and lets the system respond with a pre-approved redirect immediately.
2. **Post-LLM (output-side) safety filter** on the *generated response text* before it's finalized/spoken: catches anything the LLM produced that shouldn't reach a child regardless of how well-guarded the prompt was — this is the non-negotiable last gate, because prompt-based steering alone is not a reliable safety boundary (this is also why AR-16 makes the safety layer independent of persona/prompt authorship).

If either stage flags content, the AI service does **not** return the LLM's raw text — it returns a pre-approved, age-appropriate fallback response plus a `safety` object describing what was flagged (category, not the unsafe content itself, to avoid persisting unsafe text unnecessarily — see Section 30).

### 14.3 AI service internal structure (conceptual)

```
ai_service/
    stt/            (provider integration, language hints)
    lang_detect/
    understanding/  (intent parsing)
    context/        (client for Django's internal memory/history API)
    llm/            (provider integration, persona/system-prompt management)
    safety/         (pre- and post-generation filters — Section 17)
    emotion/        (maps response content → emotion label)
    action/         (maps response content/intent → action label)
    tts/            (provider integration)
    contract/       (assembles the final structured response — Section 15)
    api/            (the internal HTTP surface Django calls)
```

---

## 15. STT Architecture (and the AI Response Contract)

### 15.1 STT
- **What:** Converts uploaded child-utterance audio into text, with language hint support (Bangla/English).
- **Why:** Required before any understanding/LLM step can operate on the child's speech.
- **Where:** AI service (`stt/` module), calling a provider (cloud or self-hosted — AI Developer's choice; this plan only fixes placement, not vendor).
- **Receives:** Audio bytes + `language_hint` (from device config or auto) + `conversation_id`, from Django's internal call.
- **Sends:** Transcript text + detected language + confidence score, to `understanding/`.
- **Who calls it:** Only `ai_service/api/` (the internal endpoint Django hits), never called directly by Django's own code.
- **Failure behavior:** Low-confidence or failed transcription → `contract/` assembles a fallback response ("I didn't quite catch that, can you say it again?") with `safety: null`, `action: LISTENING_RETRY` (or similar), without attempting LLM generation on garbage input.

### 15.2 AI Input Contract (Django → AI service)
Conceptual fields (not code):
- `conversation_id`, `child_profile_id` (or anonymized reference), `robot_id`
- `audio` (binary, if voice turn) or `text` (if a text-based test/dashboard-triggered turn)
- `language_hint` (optional)
- `context` — recent conversation turns + relevant `Memory` entries, pre-fetched by Django and included so the AI service doesn't need direct DB access (AR-5b)
- `mode` (e.g., `conversation`, `story`, `quiz` — see Sections 21/23) if the child is in a specific feature flow

### 15.3 AI Output Contract (AI service → Django)
```
{
  conversation_id,
  text,          // the spoken reply, in the response language
  language,      // language actually used in the reply
  emotion,       // one of a fixed enum, e.g. HAPPY / CURIOUS / THINKING / CONCERNED / NEUTRAL ...
  action,        // one of a fixed enum, e.g. WAVE / NOD / TILT_HEAD / IDLE / POINT ...
  safety: {
      flagged: bool,
      category: enum | null,     // e.g. PERSONAL_INFO_REQUEST, UNSAFE_TOPIC — category only, never the raw unsafe content
      action_taken: enum         // e.g. REDIRECTED, BLOCKED, ALLOWED_WITH_CAUTION
  },
  audio,         // inline TTS reply as a base64 string, or null when TTS is unavailable — capped at 1,400,000 characters and validated on both sides (backend `conversations/ai_contract.py` and AI service `contract/schemas.py`, 2026-10-13 record); a follow-up download URL is deferred to a later phase (synchronous MVP)
  memory_writes: [ {key, value} ]  // optional: new durable facts to persist, applied by Django (AI service does not write DB directly, AR-5b)
}
```
- **Created by:** `contract/` module inside the AI service, as the single assembly point (prevents partial/inconsistent responses).
- **Consumed by:** Django's `conversations/` view — persists `Message`/`AIInteraction`, applies `memory_writes` via `memory/`, logs `SafetyEvent` if `safety.flagged`, forwards `text`+`emotion`+`action`+`audio` to the robot.
- **Failure behavior:** If the AI service errors or times out entirely (not a content-safety flag, but a hard failure — provider down, timeout), Django itself falls back to a locally-stored generic "I'm having trouble thinking right now, ask me again in a bit" response with `emotion: CONCERNED`, `action: IDLE`, so the robot never hangs waiting silently (Flow 19).
- **Explicit non-goal, restated:** this contract **never** contains a servo angle, PWM value, or any hardware-specific instruction — only the semantic `emotion`/`action` enum values. The `actions/` firmware module owns translating these into physical motion (Section 7), preserving the brief's mandated separation.

---

## 16. LLM Architecture
- **What:** Generates the child-facing natural-language response given the transcript, context, and persona.
- **Why:** Core conversational intelligence.
- **Where:** AI service `llm/` module, calling a provider (AI Developer's choice of model/vendor — out of this document's scope) via the AI service's own outbound HTTPS call (never from ESP32, never from Django).
- **Receives:** Transcript + context + system prompt/persona (AR-16) + safety constraints reference, from `understanding/`+`context/`.
- **Sends:** Draft response text, to `safety/` (post-generation stage) before anything downstream sees it.
- **Who calls it:** Only `llm/`'s caller within the AI service pipeline — never exposed as a standalone endpoint.
- **Data format:** Provider-specific request/response (opaque to Django/ESP32); internally normalized to plain text + optional structured intent hints for `emotion/`/`action/` modules.
- **Authentication:** Provider API key stored only in AI service's secret store (Section 26/30) — **never** in Django, **never** in firmware, **never** in a repo.
- **Error handling:** Provider timeout/error → `contract/` returns the same generic fallback as Section 15's hard-failure path; retried once with backoff before falling back, to avoid failing on a transient blip.
- **Testing:** Prompt-response unit tests against a fixed set of known child-appropriate and known-risky inputs (paired with Section 17's safety tests), latency benchmarks.

## 17. Safety Architecture (AI, Backend, Device, Privacy — separated)

This is a children's product; safety is treated as four distinct, independently-testable layers, not one blob:

### 17.1 AI safety (content-level)
Covers: inappropriate/adult content, violence, dangerous instructions, self-harm-adjacent content, requests for personal information, external links, unsafe "challenges," manipulation/grooming-pattern detection, age-appropriate phrasing.
- Implemented as the two-stage pre/post-LLM filter (Section 14.2).
- Maintains a fixed, versioned taxonomy of flag categories (not free-text) so `SafetyEvent.category` is queryable/reportable, not a pile of unstructured strings.
- **Never** invents new unsafe content to demonstrate/log — flags are categorical, and raw flagged text is not persisted beyond what's operationally necessary (Section 30).
- Any response involving personal information (address, school name, exact location) is **always blocked/redirected**, regardless of how the request is phrased, as a hard rule independent of general "is this unsafe" scoring.
- External links are **never** included in any robot response (the robot has no way to "show a link" anyway, but this is stated explicitly as a contract rule so a coding agent doesn't add URL text to TTS output).

### 17.2 Backend security (Section 26 has full detail; summarized here)
Request validation, authentication/authorization on every endpoint, rate limiting (especially on conversation endpoints to bound cost and abuse), audit logging of admin/safety-event access.

### 17.3 Device security (Section 26 has full detail; summarized here)
Device identity/auth, firmware signing, no secrets in firmware, MQTT topic ACL, hardware-safe action clamping (Section 6/7) so even a compromised backend response can't command unsafe physical motion (e.g., servo range clamps are enforced on-device, not trusted from network input).

### 17.4 Privacy (Section 30 has full detail; summarized here)
Minimal data collection on `ChildProfile`, parent visibility/control over stored data and memory, retention limits, and `SafetyEvent`s retained longer than ordinary conversation content specifically for audit/accountability purposes even after a parent purges routine history.

### 17.5 Parental controls tie-in
Parents can (via dashboard, Section 23): view flagged `SafetyEvent`s (category + when, not necessarily full raw content unless they explicitly drill in), set quiet hours, set topic restrictions (feeding into the pre-LLM guardrail as additional constraints), and receive notifications on safety events above a configurable severity.

## 18. TTS Architecture
- **What:** Converts the finalized, safety-approved response text into speech audio.
- **Why:** Robot must speak the response.
- **Where:** AI service `tts/` module, after `safety/` has approved the final text — TTS never runs on unapproved text, preventing a class of bugs where a flagged response could accidentally be spoken due to pipeline reordering.
- **Receives:** Approved text + target language + (optionally) an emotion hint for expressive synthesis if the provider supports it.
- **Sends:** Audio bytes (or reference), to `contract/` for inclusion in the AI Response Contract.
- **Who calls it:** Only the AI service pipeline itself, post-safety.
- **Authentication:** Provider key in AI service secret store only.
- **Error handling:** TTS failure → Django falls back to sending `text` only, robot can optionally display text-as-subtitle if a display exists, or use a small set of pre-recorded local "trouble speaking" audio clips as last resort (Flow 19 territory).
- **Testing:** Voice output sampled for both Bangla and English, latency benchmarked, and text/emotion-hint pairing spot-checked for tone appropriateness.

## 19. Emotion & Movement Architecture

Restating and detailing the brief's explicit requirement:

- The AI service selects **semantic** `emotion` and `action` enum values only — e.g. `emotion: HAPPY`, `action: WAVE` — based on response content/intent (Sections 14–15).
- Django passes these values through unchanged to the robot (it may log/validate that the enum values are within the known set, rejecting/neutralizing unknown values defensively, but does not translate them into motion).
- **ESP32's `actions/` module (Section 7) owns all physical translation**: it maps `HAPPY`+`WAVE` to a specific servo/LED sequence, using hardware-safe ranges (Section 6) that are configured on-device, never trusted verbatim from the network.
- This separation means: (a) the AI Developer can add/tune emotions without any firmware change as long as the enum is in the agreed shared list; (b) the Electronics/Software side can redesign the physical wave motion without any AI-service change; (c) a network-compromised or buggy AI response can at worst request an unknown/no-op action, never an out-of-range physical motion, because clamping is enforced in `hal/servo` regardless of input.
- The shared enum list (`emotion` values, `action` values) is a **versioned contract file** both sides reference — treated the same way as the AI Response Contract itself (Section 15), reviewed jointly by AI Developer and Software Developer before any change ships.

---

## 20. Memory & Context Architecture

- **Conversation context (short-term):** Recent turns within an active `Conversation` are fetched by Django and passed in the AI Input Contract (Section 15.2) on every call — the AI service holds no session state itself between calls (stateless service, per AR-5). This makes the AI service horizontally scalable/restartable without losing context.
- **Basic memory (long-term):** `Memory` entries (Section 13) are durable, per-`ChildProfile` facts (e.g., favorite animal, ongoing story character names) written via `memory_writes` in the AI Response Contract and applied by Django. On the next relevant conversation, Django's `context/` retrieval includes applicable `Memory` entries (simple relevance selection for MVP — e.g., most-recent N entries, or keyword match; a full vector/embedding retrieval system is **ARCHITECTURAL RECOMMENDATION, optional, Phase 14+ enhancement**, not required for MVP memory).
- **Context retrieval performance:** For MVP, a direct Postgres query per conversation turn is sufficient. Redis-backed short-term context caching (AR-6) is recommended once conversation volume makes repeated DB reads for the same active conversation a measurable latency contributor — not required before that.
- **Parent visibility/control:** All `Memory` entries are visible and individually deletable by the parent via dashboard (Section 23), satisfying both the "basic memory" feature requirement and the child-privacy requirement (Section 30) simultaneously.
- **Expiry:** `Memory.expires_at` is optional — most entries persist until explicitly deleted, but the model supports auto-expiry for anything that shouldn't be remembered indefinitely (e.g., transient session facts), decided case-by-case by the AI Developer's memory-write logic.

## 21. Educational Features (Stories, Q&A, Quizzes/Games)

- **Architecture pattern:** All three (storytelling, educational Q&A, quiz/games) are treated as **modes** within the existing conversation pipeline, not separate systems — the AI Input Contract's `mode` field (Section 15.2) tells the AI service which behavior to apply, and the same STT→LLM→Safety→TTS→Emotion/Action pipeline handles all of them, with mode-specific prompt/behavior differences owned entirely inside the AI service (`understanding/` + `llm/`).
- **Story mode:** AI service tracks story-in-progress state as part of conversation context (via `Memory`/recent-message context, not a separate story engine) — e.g., "we are on chapter 2 of the dragon story," retrieved the same way as any other context.
- **Quiz/game mode:** Quiz questions/scoring logic can be either fully LLM-driven (simplest, MVP default) or backed by a small curated question bank stored in Django (`content/` or a dedicated `quizzes/` app) for consistency/quality control — **ARCHITECTURAL RECOMMENDATION**: start LLM-driven for MVP (Phase 15), add a curated bank as an enhancement if quiz quality/consistency needs improve, since a curated bank is extra content-authoring work not stated as required in the brief.
- **Educational Q&A:** No architectural difference from general conversation — the LLM's persona/prompt is simply tuned by the AI Developer to answer factually and age-appropriately; the pipeline doesn't need to "know" a question is educational vs. casual.
- **UsageLog tie-in:** `mode` and outcome (e.g., quiz score) are recorded into `UsageLog` (Section 13) for the parent dashboard's "learning topics" / progress view.
- **Bangla/English support:** Handled entirely by `language_hint`/detected `language` flowing through the same contract (Section 15) — no separate pipeline per language; STT/LLM/TTS providers chosen by the AI Developer must support both languages, which is a provider-selection constraint noted here but not solved by this architecture document.

## 22. Offline Mode

### 22.1 What works without internet
- Wake word + local button interaction (on-device, Section 7)
- A bounded set of **pre-cached canned responses** (greetings, simple acknowledgments, "I'm offline right now" explanations)
- **Pre-downloaded story bundle** playback (audio or TTS-pre-rendered content downloaded during a prior online session, per AR-13)
- **Simple local games** that don't require generative understanding (e.g., a fixed quiz question bank stored locally, "repeat after me" style interaction, simple movement games driven entirely by local logic)
- Movement/emotion responses tied to the above local content (using the same `actions/` module, since local content can still carry semantic emotion/action tags baked in at bundle-authoring time)
- Local configuration application (whatever was last successfully synced)
- Battery/status display remains fully functional (no network needed)

### 22.2 What does NOT work without internet
- Open-ended conversation (no LLM available locally)
- New STT of arbitrary speech beyond wake-word/simple command matching (full STT requires the AI service)
- Real-time TTS of dynamic content
- New OTA/config/content updates (last-synced versions are used until connectivity returns)
- Dashboard-triggered live commands
- New `Memory` writes (nothing to write to — memory is server-side; **ARCHITECTURAL RECOMMENDATION**: queue any offline-relevant local events for sync on reconnect is explicitly out of scope for MVP, since offline mode intentionally has no generative interaction to generate memory-worthy content from)

### 22.3 Why not run the LLM on ESP32-S3 directly
ESP32-S3 (even with PSRAM) has, at most, a few MB of usable RAM and no dedicated NPU capable of running a general-purpose multilingual LLM at acceptable latency/quality — this is a hardware capability limit, not a design preference. Wake-word/keyword-spotting models (tens of KB to low-MB) are within its capability; a conversational LLM (even a "small" one, typically hundreds of MB to GB+ for usable quality) is not. STT/TTS at production quality for two languages face the same constraint.

### 22.4 Edge-compute alternative (explicitly marked, not adopted)

**ARCHITECTURAL RECOMMENDATION (alternative, not part of the base architecture):** If deeper offline conversational capability is desired in the future, a Raspberry Pi (or similar SBC) could be added as a local edge-compute tier *inside the robot or as a companion base station*, running a small local LLM + local STT/TTS, with the ESP32 talking to the Pi over UART/I2C/local Wi-Fi instead of (or in addition to) the cloud backend when offline. This would require: additional hardware/power budget (Electronics Engineer decision), a new firmware communication module (`network/edge` alongside `network/http`/`network/mqtt`), and a scaled-down AI pipeline runnable on Pi-class hardware. **This is not adopted into the current architecture** — it is documented here only because the brief requires any such alternative be marked explicitly rather than silently folded into the "current" design. The base architecture assumes ESP32-S3-only compute, with the offline behavior described in 22.1–22.2.

---

## 23. Parent Web Dashboard

Built with Django Templates + HTML/CSS/JS (per stack constraint), served from `dashboard_parent/`, calling the same REST API (`/api/v1/...`) that other clients would use (satisfies "future mobile app can use same backend APIs" principle — the dashboard is a REST API consumer, not a special-cased server-side-only integration).

**Pages/features (from the brief, organized functionally):**
- **Auth:** login, password reset, (optional MFA — **ARCHITECTURAL RECOMMENDATION**, not in brief, flagged optional for Phase 19 security hardening)
- **Robot pairing:** enter/scan device pairing code generated during Flow 4, associate robot with parent account
- **Robot status:** online/offline, battery %, current firmware version, last-seen — via polling `GET /api/v1/robots/{id}/status` (AR-17)
- **Controls:** volume, language default, quiet hours/schedule, parental content limits (feeds into safety pre-filter constraints, Section 17.5) — via `PATCH /api/v1/robots/{id}/config`
- **Usage:** time spent, learning topics covered, quiz scores/history — from `UsageLog`
- **Conversation settings:** what modes are enabled (story/quiz/free conversation), memory management (view/delete `Memory` entries)
- **Firmware update:** view current version, available updates, trigger/schedule update window (respecting the "OTA never interrupts active conversation" rule, Section 8.2)
- **Wi-Fi configuration:** re-trigger provisioning flow if the robot needs new Wi-Fi credentials
- **Activity history:** conversation history (subject to retention/privacy settings, Section 30), safety events relevant to their child

**Design direction (as specified):** black/white/soft-gray base, premium crimson/red accent, glassmorphism where useful, elegant typography, responsive, functionality-first — implemented via a shared Django template base (`base.html`) + a small design-token CSS file, not a heavy JS framework, keeping it buildable by a 2–3 person team without introducing a frontend build pipeline unless the team later chooses to (noted as optional complexity, not required).

## 24. Admin Dashboard

Also Django Templates, served from `dashboard_admin/`, restricted to staff/admin roles (Section 25).

**Pages/features:**
- **Users:** list/search parent accounts, support actions (password reset trigger, account status)
- **Robots/Devices:** fleet list, per-device status/history, force-disconnect, credential rotation trigger
- **Firmware:** upload new `Firmware` build (with signature verification metadata), manage rollout (Section 27), view rollout status across fleet
- **Logs:** application/device/security logs (Section 28), filterable
- **System health:** service status (Django, AI service, MQTT broker, DB, Redis/Celery if adopted) — via internal health-check endpoints (Section 33)
- **Safety events:** review flagged interactions (category, severity, affected child — access-restricted, audit-logged per Section 30), mark reviewed
- **Analytics:** fleet-wide usage aggregates (not individual child-level detail beyond what's needed operationally)
- **Configuration:** manage default configuration templates, push config changes

---

## 25. Authentication & Authorization

Three distinct principal types, three distinct auth mechanisms — not one shared scheme:

| Principal | Mechanism | Token type | Scope |
|---|---|---|---|
| Parent/Admin (human, dashboard) | Django session auth (dashboard pages) + issued JWT/token for any AJAX calls to the REST API | Session cookie (HttpOnly, Secure) or short-lived API token | Parent: own account's robots/children only. Admin: role-gated fleet-wide access, enforced via Django permission classes, not just UI hiding |
| Robot (ESP32) | Device keypair + signed-challenge exchange → rotating device access JWT (AR-9/AR-10) | Bearer JWT (HTTPS) / username-password derived from token (MQTT) | Scoped to its own `device_id` only — enforced both at Django's DRF permission layer and at the MQTT broker ACL layer (Section 11.2) |
| AI Service (internal) | Shared internal service token (not a user-facing credential), plus network-level isolation (not internet-exposed at all) | Internal bearer token | Can only call Django's internal context/memory-write endpoints, not the full admin API |

**Authorization rules (binding):**
- A parent can only ever read/modify `Robot`/`ChildProfile`/`Conversation`/`Memory` rows where the foreign key traces back to their own `Parent` row — enforced at the queryset level (not just view-level checks), so a coding agent implementing any parent-facing endpoint must filter by `request.user`'s parent ownership as a baseline queryset, never trust a client-supplied ID alone.
- Admin endpoints require `is_staff` (or a dedicated `is_admin` role) and are audit-logged (`AuditLog`) on any write action.
- A robot's JWT authorizes actions only for its own `device_id`; Django rejects any request where the JWT's embedded device_id doesn't match the path/payload's referenced device_id.

## 26. Device Identity & Security

- **Device identity:** UUID `device_id` provisioned at first boot (or factory-time), paired with an on-device-generated asymmetric keypair; private key stored in ESP32-S3's encrypted NVS (leveraging ESP-IDF's flash encryption where the hardware/build supports it); public key registered with Django during Flow 4 (registration).
- **Device authentication flow:** Device requests a challenge → signs it with its private key → backend verifies against stored public key → issues a short-lived (e.g., 1h) device access JWT → used as Bearer token for HTTPS and translated to MQTT username/password (or reused as a client identifier + token-derived password) for the MQTT connection. Refreshed proactively before expiry (`security/` module, Section 7).
- **Credential rotation:** Device access JWTs rotate automatically via the refresh flow above; the underlying device keypair itself can be rotated via an explicit admin-triggered re-provisioning flow (rare, e.g., suspected compromise) — documented as an admin dashboard action (Section 24).
- **Replay protection:** Challenge-response includes a server-issued nonce with short validity, preventing replay of a captured signed challenge.
- **Request validation:** All DRF endpoints use serializers with strict field validation; no endpoint trusts client-supplied IDs without ownership/scope checks (Section 25).
- **Rate limiting:** Applied per-device and per-user (DRF throttling classes) especially on conversation-turn endpoints (cost + abuse control) and auth endpoints (brute-force protection).
- **CSRF/XSS/SQLi:** Django's built-in CSRF protection enabled on all dashboard forms; DRF API (token-authenticated, not cookie-authenticated for device/AJAX calls) exempted per Django REST Framework's standard pattern for token auth; all DB access via Django ORM (no raw SQL string interpolation) to prevent SQLi; templates use Django's auto-escaping (no `|safe` on user-generated content) to prevent XSS.
- **Command authorization:** Any MQTT `command` publish (Section 11) originates only from an authenticated, ownership-checked Django action (parent/admin triggering it, or the conversation pipeline) — never accepted as a raw pass-through from an unauthenticated source.
- **MQTT topic ACL:** As detailed in Section 11.2 — per-device scoped credentials, broker-enforced.
- **OTA security:** Firmware signing (AR-15) + on-device signature verification before flashing (Section 27 has full detail).
- **Secret management:** All secrets (DB credentials, MQTT service credentials, AI provider API keys, Django `SECRET_KEY`, JWT signing keys, firmware signing key) live in environment variables / a secrets manager (e.g., Docker secrets, or a vault if the team adopts one later) — **never committed to Git**, never embedded in firmware, never in the AI service's or Django's source code. The firmware signing private key in particular lives only in the CI/CD environment, not on any developer's laptop or in the repo.
- **Data encryption:** TLS in transit everywhere (HTTPS, MQTT); at-rest encryption for the database is an **ARCHITECTURAL RECOMMENDATION** (disk-level encryption on the VPS/cloud volume, or Postgres-native column encryption for the most sensitive fields like `ChildProfile` name) — not explicitly required by the brief but recommended given this is a children's product; flagged optional for Phase 19.
- **Backup:** Regular automated Postgres backups (e.g., daily), encrypted at rest, retention policy aligned with Section 30's data-retention rules (backups don't become a loophole around a parent's deletion request — deletion must propagate to backup retention policy, at minimum via backup expiry).

## 27. OTA Architecture

1. **Backend knows new firmware exists:** Admin uploads a `Firmware` record (binary + version + release notes + min-hardware-rev) via the admin dashboard (Section 24); CI/CD can also publish directly via an internal admin API endpoint if the team automates builds later.
2. **Robot checks for updates:** Triggered by receiving `robots/{device_id}/ota/notify` (Section 11) — robot then calls `GET /api/v1/firmware/latest?current_version=...` over HTTPS.
3. **Update metadata delivery:** Response includes version, checksum, signature, download URL, size — over HTTPS, authenticated.
4. **Firmware download:** `ota/` module downloads the binary over HTTPS (chunked, resumable if interrupted) into the inactive OTA partition (ESP-IDF's standard dual-partition OTA scheme).
5. **Authenticity verification:** Before marking the new partition bootable, firmware signature is verified against an embedded public key (AR-15) and checksum is confirmed; any mismatch aborts and reports `OTAUpdate.status = failed` via MQTT `event`.
6. **Installation:** ESP-IDF's OTA APIs mark the new partition as boot target and reboot into `OTA_UPDATE` → post-reboot self-check.
7. **Installation failure:** If the new firmware fails to report a successful post-boot health check within a bounded window, ESP-IDF's rollback mechanism automatically reboots back into the previous known-good partition; `OTAUpdate.status = rolled_back` reported once connectivity is available again.
8. **Rollback strategy:** Dual-partition scheme means the previous firmware is never erased until the new one has proven itself healthy over N boots/a time window — this is the primary rollback safety net, not a separate custom mechanism.
9. **Version management:** `Firmware`/`OTAUpdate` models (Section 13) track full history per device; admin dashboard shows current + rollout status fleet-wide.
10. **Update history:** `OTAUpdate` rows are append-only per attempt (not overwritten), giving a full audit trail per device.

**Rule (binding):** OTA only ever begins from `IDLE` state (Section 8.2) — never interrupts `LISTENING`/`PROCESSING`/`SPEAKING`, and a scheduled update window (parent-configurable quiet hours, Section 23) is preferred over an immediate forced update.

## 28. Logging & Monitoring

| Log type | What it captures | Where stored | Child-safety note |
|---|---|---|---|
| Application logs (Django, AI service) | Request/response metadata, errors, timing | Centralized log storage (files + optionally shipped to a log aggregator later) | Never log full conversation text at INFO level by default — only at a restricted DEBUG level gated for engineering troubleshooting, time-boxed |
| Device logs | Firmware-side ring buffer (Section 7 `diagnostics/`), surfaced via `event`/`status` MQTT or a dedicated log-upload endpoint on connectivity | Django DB (`RobotEvent`) + optional raw log blob storage | No audio/raw child speech logged on-device beyond what's needed for the immediate request buffer |
| AI logs | Latency, model/provider used, safety flag outcomes (`AIInteraction`) | Django DB | **Never logs raw flagged content in plaintext application logs** — category-only, per Section 17.1; full content (if retained at all) lives only in the tightly-access-controlled `SafetyEvent`/`Message` tables, not general-purpose logs |
| Security logs | Auth attempts/failures, token issuance/rotation, admin actions | `AuditLog` + infra-level logs | Standard security logging; access restricted to admins |
| Audit logs | Who changed what config/firmware/safety-event-review | `AuditLog` | Immutable/append-only |
| Metrics | Request latency, error rates, AI pipeline stage timings, MQTT connection counts, DB query timing | Metrics backend (e.g., Prometheus-compatible endpoint — **ARCHITECTURAL RECOMMENDATION**, optional Phase 20 addition, not explicitly required by brief) | N/A |
| Health checks | Per-service `/health` endpoint (Django, AI service) checked by orchestration/monitoring | N/A | N/A |
| Device heartbeat | MQTT heartbeat (Section 11) | `RobotState.last_updated` | N/A |
| Error tracking | Unhandled exceptions | Centralized error log; **ARCHITECTURAL RECOMMENDATION**: an error-tracking tool (e.g., Sentry-style) is optional, not required, given team size — plain structured logs are sufficient for MVP | N/A |

**What must NOT be logged (binding, children's-product rule):** raw child audio beyond the immediate processing buffer (not persisted to long-term logs), full conversation transcripts at default log verbosity, any personal information a child might have stated (even if the safety filter blocked the response, the *fact* that PII was mentioned is logged categorically — the PII content itself is not echoed into logs).

## 29. Error & Recovery Architecture

Failure matrix (detection → handling → user behavior → logging → recovery):

| Failure | Detection | Handling | User (child) behavior | Logging | Recovery |
|---|---|---|---|---|---|
| Mic hardware failure | `hal/mic` I2S error/no data | `state/` → `ERROR` | Robot shows error LED pattern, no crash | `RobotEvent` on next connectivity | Auto-retry driver re-init once; else stays `ERROR` until power cycle/support |
| Speaker failure | `hal/speaker` I2S error | Fallback: no audio, but still logs interaction attempt | Silent robot, possibly LED/text fallback if display exists | `RobotEvent` | Same as above |
| Servo failure | PWM write error or stall detection (if electronics provides feedback) | `actions/` aborts current sequence, returns to neutral pose | No movement for that action, speech continues if unaffected | `RobotEvent` | Retry next action attempt; persistent failure → `ERROR` |
| Wi-Fi failure | Disconnect event / repeated connect failure | `state/` → `CONNECTING` retry w/ backoff → `OFFLINE` after threshold | Offline-mode behavior (Section 22) | Local ring buffer, uploaded on reconnect | Auto-reconnect; LWT fires on broker side |
| MQTT failure | Broker disconnect | `network/mqtt` reconnect w/ backoff | No immediate user-visible effect if HTTPS still works (voice pipeline degrades gracefully but continues) | `RobotEvent` on reconnect | Auto re-subscribe on reconnect |
| HTTPS failure | Request timeout/5xx | Bounded retry per caller's policy (Section 10) | Voice pipeline: "trouble understanding" fallback; config/OTA: keep last-known-good | Django-side error logs; device-side local log | Retry next attempt/next connectivity window |
| Backend (Django) failure | 5xx / unreachable | Same as HTTPS failure from device perspective; infra-level restart via container orchestration | Same fallback as above | Infra logs, alerting | Container auto-restart; health check gates traffic |
| Database failure | Django DB connection error | Django catches, returns 503, does not crash worker | Robot sees HTTPS failure behavior | Infra logs | DB restart/failover (infra-level, outside this document's scope beyond noting it must exist) |
| AI service failure | Internal call timeout/error | Django's hard-failure fallback (Section 15.3) | Generic "trouble thinking" response, not silence | `AIInteraction` marked failed | Retry once w/ backoff, then fallback response |
| STT failure | Low confidence / provider error | AI service's `contract/` returns retry-prompt response | "Didn't catch that, try again" | `AIInteraction` | Child re-attempts; no system-level recovery needed |
| LLM failure | Provider error/timeout | Section 16 fallback | Generic fallback response | `AIInteraction` | Retry once, then fallback |
| TTS failure | Provider error | Section 18 fallback (text-only / local clip) | Degraded but non-silent response | `AIInteraction` | Retry once, then fallback |
| OTA failure | Signature/checksum mismatch or failed boot | Abort/rollback (Section 27) | No visible disruption (update simply doesn't apply) | `OTAUpdate.status=failed` reported on reconnect | Dual-partition rollback; admin notified via dashboard |
| Low battery | ADC/fuel-gauge threshold crossed | `state/` → `LOW_BATTERY`, reduced functionality, eventually `SLEEP` | Robot warns child, dims/reduces activity | `RobotEvent` | Resumes normal operation once charged past threshold |
| Invalid command (MQTT) | Command payload fails schema/enum validation | Rejected, not executed | No visible effect (silently ignored, logged) | `RobotEvent` | N/A — sender (Django) must fix the source of the bad command |
| Unauthorized command | JWT/device_id mismatch or ACL rejection | Rejected at broker/API layer, never reaches `actions/` | No visible effect | Security log + `AuditLog` if pattern suggests abuse | N/A |
| Corrupted data (config/content bundle) | Checksum mismatch on downloaded payload | Reject, keep previous version | No visible effect | `RobotEvent` | Retried on next notify/periodic check |

---

## 30. Privacy Architecture

- **Data minimization:** `ChildProfile` collects only what features require (first name, age band, language preference) — no unnecessary PII (no address, no school, no exact birthdate unless a parent explicitly opts into age-specific content tuning that needs it, and even then age band is preferred).
- **Audio retention:** Raw child audio is **not retained by default** beyond the processing window needed to produce STT text — only the resulting text (`Message.text`) is persisted, unless a parent explicitly opts in to audio retention for some future feature (not required by the brief; **ARCHITECTURAL RECOMMENDATION**: default to text-only retention, audio-retention opt-in is a Phase 21+ consideration, not MVP).
- **Conversation retention:** Configurable retention window (e.g., default 90 days — **ARCHITECTURAL RECOMMENDATION**, exact number is a policy decision for the team/legal, not fixed by the brief), after which `Conversation`/`Message` rows are purged or anonymized, except:
- **SafetyEvent exception:** `SafetyEvent` records are retained longer than ordinary conversation content (accountability/audit purpose), but stored with minimized content (category + minimal context, not necessarily full verbatim transcripts) per Section 17.1.
- **Parent control:** Parents can view and delete their child's `Memory` entries and conversation history via dashboard (Section 23) at any time, ahead of the default retention window if desired; deletion requests propagate through the app-level delete path (not just a DB row flag) so backups eventually age out the same data (Section 26 backup note).
- **Third-party data sharing:** Audio/text is sent to STT/LLM/TTS providers (AI service's provider integrations) as required for functionality — this is disclosed to parents (dashboard/terms) as an inherent part of how the product works; no data is sold or shared for advertising/marketing purposes (this plan assumes that's a product policy commitment, consistent with "ad-free, child-safe product" framing implied by the brief's overall safety emphasis).
- **Access control on child data:** Only the owning parent and role-gated admins (for support/safety purposes, audit-logged) can access a given `ChildProfile`'s data — enforced at the queryset level (Section 25).

---

## 31. Testing Strategy

| Level | What to test | Expected result | Pass/fail criteria |
|---|---|---|---|
| 1. Hardware integration | Each HAL driver against real hardware (mic, speaker, servo, LED, sensors, battery) | Correct raw signal capture/output | Matches Electronics Engineer's spec tolerances |
| 2. ESP32 firmware (module) | Each firmware module in isolation (Section 7 table's "testing" column) | Module behaves per its documented contract | Unit/bench tests pass; no crashes under fault injection |
| 3. Unit testing (backend/AI) | Django model/service functions, AI service pipeline stages | Correct outputs for known inputs | Standard unit-test pass/fail (pytest) |
| 4. API testing | Every REST endpoint (Section 32-style contract) | Correct status codes, payloads, auth enforcement | Schema validation + auth/ownership boundary tests pass |
| 5. Backend integration | Django ↔ DB, Django ↔ MQTT bridge, Django ↔ AI service (mocked) | End-to-end request handling within Django | Integration test suite green |
| 6. AI service testing | Full pipeline with fixture audio/text, including safety fixtures (known-safe and known-risky inputs) | Correct contract output, safety flags fire correctly | 100% of safety fixture cases correctly flagged/blocked; no regressions |
| 7. MQTT testing | Publish/subscribe, ACL enforcement, reconnect/backoff, LWT | Messages delivered to correct topics only, unauthorized publishes rejected | ACL boundary tests pass, reconnect within expected time window |
| 8. End-to-end testing | Real or simulated device through full voice pipeline (Flows 6-9) | Child utterance → correct spoken, emotive, moving response | Manual + scripted E2E pass on reference hardware |
| 9. Security testing | Auth bypass attempts, token expiry/replay, MQTT ACL bypass attempts, injection attempts on API | All attempts correctly rejected | Zero successful bypass in test suite |
| 10. OTA testing | Valid image, tampered image, corrupted download, forced boot failure | Valid installs; tampered/corrupted rejected; boot failure triggers rollback | All four scenarios behave exactly as specified in Section 27 |
| 11. Offline testing | Disconnect mid-session, extended offline period, reconnect | Graceful fallback per Section 22, clean resume on reconnect | No crash, no data loss beyond documented offline limitations |
| 12. Failure recovery testing | Each row of the Section 29 failure matrix, fault-injected | Matches documented detection/handling/recovery | Each row's expected behavior reproduced |
| 13. Performance testing | Voice pipeline latency (wake→response), MQTT message throughput, concurrent-device load on backend | Latency within acceptable range for a "conversational" feel (target defined by team, e.g., <3-4s turnaround for MVP) | Meets agreed latency/throughput targets under realistic fleet-size load |
| 14. Child safety testing | Curated adversarial prompt set (age-inappropriate requests, PII probing, unsafe instructions, manipulation attempts) run through the AI service | All correctly blocked/redirected per Section 17 | Zero unsafe responses reach the "approved for TTS" stage in the test set |
| 15. Production acceptance | Full system, staging environment, real network conditions | All flows (Section, "End-to-End User Flows") complete successfully | Stakeholder sign-off checklist (Section 40) passes |

## 32. Development Environment

- **Version control:** Git + GitHub, trunk-based or short-lived feature branches (team of 3 — avoid over-engineered branching strategy), protected `main`, required review before merge to `main` even with a small team (catches secret leaks, architecture drift).
- **Repo structure (ARCHITECTURAL RECOMMENDATION):** Monorepo with top-level `firmware/`, `backend/`, `ai_service/`, `docs/`, `infra/` (Docker Compose, deployment scripts) — simplest for a 3-person team to keep contracts (Sections 15, 19) synchronized across sub-teams without cross-repo version-pinning overhead. Alternative (multi-repo) is viable but adds coordination overhead not justified at this team size.
- **Local dev:** Docker Compose brings up Django + AI service + Postgres + Mosquitto (+ Redis/Celery once adopted) locally; ESP32 firmware built/flashed via ESP-IDF toolchain, tested against the local Compose stack (or a shared dev/staging backend) using a real or simulated device.
- **Environment separation:** `local` (dev machine) → `staging` (shared, used for E2E/production-acceptance testing) → `production`. Each with its own secrets, DB, and MQTT broker instance — staging never shares production child data.
- **Secrets in dev:** `.env` files (git-ignored) for local dev; never real production secrets used locally.
- **CI:** GitHub Actions (or equivalent) running unit/API tests, and firmware build checks, on every PR; firmware signing (Section 27) happens only in a protected CI job with access to the signing key, never locally.

## 33. Deployment Architecture

- **Target:** Linux server/VPS/cloud, Docker + Docker Compose (per stack constraint) — Kubernetes explicitly **not** adopted (would be an unjustified complexity increase for a 3-person team's initial scale; noted as a possible future migration if fleet/traffic scale demands it, Section 39).
- **Services deployed:** nginx (TLS termination + reverse proxy), Django (gunicorn/uvicorn), AI service (uvicorn), Postgres, Mosquitto, Redis + Celery (once adopted, AR-6/7) — topology diagrammed in Section 4.2.
- **TLS certificates:** Automated issuance/renewal (e.g., Let's Encrypt via certbot or nginx-integrated ACME) for the public HTTPS/MQTT-over-TLS endpoints.
- **Zero/low-downtime updates:** Rolling container restarts for Django/AI service (brief downtime acceptable at this scale, documented as a known limitation rather than over-engineering blue/green deployment for MVP).
- **Health checks:** `/health` endpoints (Django, AI service) wired into container orchestration restart policies.
- **Backups:** Automated Postgres backup job (Section 26), stored off the primary VPS (e.g., to object storage), tested restore procedure documented (not just "backups exist" — a restore drill is part of Phase 22 acceptance).
- **Monitoring/alerting:** At minimum, uptime checks on public endpoints and disk/memory usage alerts on the VPS; richer metrics (Section 28) are an optional Phase 20 enhancement.

---

## 34. Development Phases

The brief's suggested 22 phases (0–22) are dependency-sound and are **adopted as proposed**, with the ordering rationale made explicit below. No phase was reordered, but two phases were given tighter scoping notes (Phase 9 explicitly does *not* include Phase 10's provider integration; Phase 16 explicitly reuses Phase 18's secure-download pattern rather than inventing a new one — AR-13) to keep "no big-bang coding" honest at the phase-boundary level.

| # | Phase | Objective | Why here (dependency reason) | Key components touched |
|---|---|---|---|---|
| 0 | Requirements + architecture freeze | Lock this document as the binding reference; resolve all Section 3 ARCHITECTURAL RECOMMENDATIONS with team sign-off | Nothing can safely start until the contracts (data model, API shapes, MQTT topics, AI contract, emotion/action enum) are fixed enough that Phase 1+ isn't building on moving sand | This document, shared contract files |
| 1 | Hardware/software interface definition | Finalize Section 6's hardware↔firmware contract with the Electronics Engineer (pin map, sample rates, servo ranges) | Firmware modules in Phase 2+ need concrete values, not placeholders | `hardware_config.h`-equivalent, Electronics handoff doc |
| 2 | ESP32 foundation | Boot, `hal/` skeleton, `state/` machine skeleton, NVS config storage, basic logging | Every later firmware phase builds inside this skeleton | `hal/`, `state/`, `diagnostics/` (basic), `identity/` |
| 3 | ESP32 hardware control | Real mic/speaker/servo/LED/sensor/battery drivers wired to `hal/` | Needs Phase 1's finalized interface and Phase 2's skeleton | `hal/*` fully implemented |
| 4 | Wi-Fi + network layer | `network/wifi` provisioning + connect/reconnect; `SETUP`/`CONNECTING` states live | Nothing can reach the backend before this | `network/wifi`, `state/` (SETUP, CONNECTING) |
| 5 | Django foundation | Django project skeleton, `core/`, `accounts/` (User/Parent/ChildProfile models + auth), health check | Backend must exist before firmware can register against it | `core/`, `accounts/` |
| 6 | Database + device management | Full Section 13 schema migrated; `devices/` app (Robot, DeviceCredential) + registration/pairing endpoints | Needed before real device auth (Phase 7) can be tested | `devices/`, DB schema |
| 7 | ESP32 ↔ Django communication | `network/http`, `security/` (device auth flow), registration/auth REST endpoints exercised end-to-end (Flows 4–5) | First real device-to-backend integration point; validates Sections 10, 25, 26 | `network/http`, `security/`, `devices/` endpoints |
| 8 | MQTT real-time communication | Mosquitto deployed, `network/mqtt`, `mqtt_bridge/`, topic ACLs, heartbeat/status/event flow live (Section 11) | Needs Phase 7's device identity/auth to derive MQTT credentials | `network/mqtt`, `mqtt_bridge/`, Mosquitto config |
| 9 | AI service foundation | AI service skeleton (FastAPI app, internal API surface, `contract/` module with stubbed/mocked stage outputs) — **no real STT/LLM/TTS provider wired yet** | Establishes the internal API contract (Section 15) that Django will integrate against, independent of which providers get chosen | `ai_service/api/`, `ai_service/contract/` (stubs) |
| 10 | STT/LLM/TTS integration | Wire real providers into `stt/`, `llm/`, `tts/` (Sections 15/16/18), replacing Phase 9's stubs | Needs Phase 9's skeleton in place; provider selection/creds must exist | `stt/`, `llm/`, `tts/` |
| 11 | AI ↔ Backend integration | Django's `conversations/` calls the real AI service end-to-end; `AIInteraction`/`Message` persisted | Needs both Phase 6 (DB/conversations schema) and Phase 10 (real AI pipeline) | `conversations/`, AI service internal API (consumed) |
| 12 | Complete voice conversation pipeline | Full Flow 6–7: device audio upload → Django → AI service → response → device playback, end-to-end on real hardware | First point all three tiers work together for the core feature | `audio/`, `voice/`, `network/http` (device side), `conversations/` (backend) |
| 13 | Emotion + movement integration | `actions/` firmware module + `emotion/`/`action/` AI modules wired to the real contract fields; `MOVING` overlay behavior (Section 8.3) | Needs Phase 12's working pipeline to carry emotion/action fields meaningfully | `actions/`, AI `emotion/`/`action/` |
| 14 | Memory/context | `memory/` Django app, context retrieval wired into AI Input Contract, `Memory` CRUD + parent-visible list | Builds on Phase 11's working AI↔Backend link; needed before richer story/quiz continuity (Phase 15) is worth building | `memory/`, `context/` |
| 15 | Educational/story/quiz features | `mode` field wired through; story/quiz prompt behavior in AI service; `UsageLog` rollups | Needs Phase 14's memory/context for story continuity | AI service `understanding/`/`llm/` mode handling, `analytics/` (`UsageLog`) |
| 16 | Offline functionality | `content/` firmware + backend app, offline canned responses/local games, versioned bundle download reusing Phase 18's secure-download pattern (built slightly ahead here as a scoped subset — see note below) | Needs a working online pipeline first (to know what "degraded" looks like) and the general secure-download mechanism, which is otherwise formalized fully in Phase 18 | `content/` (firmware + backend), `state/` (`OFFLINE`) |
| 17 | Parent/Admin web dashboard | `dashboard_parent/`, `dashboard_admin/`, full Section 23/24 feature set against existing REST API | Needs essentially every backend app (accounts, devices, conversations, memory, configuration, firmware, safety, analytics) to have something to display | `dashboard_parent/`, `dashboard_admin/` |
| 18 | OTA | `ota/` firmware module, `firmware/` Django app, signing pipeline, full Section 27 flow | Deliberately late — needs a stable, tested firmware base (nothing to safely update before this) and Phase 16 already exercised the shared download pattern | `ota/`, `firmware/`, CI signing step |
| 19 | Security hardening | Rate limiting, credential rotation tooling, optional mTLS/MFA evaluation, penetration-style testing pass across Sections 25/26 | Run as a dedicated pass once all features exist to harden, rather than only incrementally — but note: baseline security (auth, TLS, ACLs, no-secrets-in-firmware) is **not deferred to this phase**, it's built-in from Phase 2 onward per principle 11; this phase is the *hardening/audit* pass, not the first time security is considered | Cross-cutting |
| 20 | Observability | Metrics, log aggregation, health-check dashboards, alerting (Section 28) | Most valuable once there's real traffic/behavior to observe | `diagnostics/` (device), infra monitoring |
| 21 | Full system testing | Execute Section 31 levels 8–15 comprehensively against a near-production staging environment | Needs the full system assembled | All |
| 22 | Production deployment | Go-live: final deployment (Section 33), backup/restore drill, stakeholder acceptance (Section 40) | Terminal phase | All |

**What must NOT be done in any phase:** any functionality belonging to a later phase (e.g., no OTA code in Phase 2, no dashboard code before Phase 17, no AI logic inside firmware ever, no hardcoded secrets at any phase). This is restated per-phase in Section 36's "DO NOT MODIFY / DO NOT BUILD YET" field.

---

## 35. Phase Dependency Graph

```
Phase 0 (Architecture Freeze)
        │
Phase 1 (HW/SW Interface)
        │
Phase 2 (ESP32 Foundation)
        │
Phase 3 (ESP32 Hardware Control)
        │
Phase 4 (Wi-Fi/Network Layer) ───────────────┐
        │                                     │
Phase 5 (Django Foundation)                   │
        │                                     │
Phase 6 (Database + Device Mgmt)              │
        │                                     │
Phase 7 (ESP32 ↔ Django Comms) ◄──────────────┘
        │
Phase 8 (MQTT Real-Time Comms)
        │
Phase 9 (AI Service Foundation)
        │
Phase 10 (STT/LLM/TTS Integration)
        │
Phase 11 (AI ↔ Backend Integration)
        │
Phase 12 (Complete Voice Conversation Pipeline)  ◄── critical integration milestone
        │
        ├──────────────┬───────────────────┐
        ▼               ▼                    ▼
Phase 13            Phase 14              Phase 16
(Emotion/Movement)  (Memory/Context)       (Offline Functionality)
        │               │                    │
        │               ▼                    │
        │          Phase 15                  │
        │          (Educational/Story/Quiz)   │
        │               │                    │
        └───────────────┴────────────────────┘
                         │
                Phase 17 (Parent/Admin Dashboard)
                         │
                Phase 18 (OTA)
                         │
                Phase 19 (Security Hardening)
                         │
                Phase 20 (Observability)
                         │
                Phase 21 (Full System Testing)
                         │
                Phase 22 (Production Deployment)
```

Phases 13, 14, and 16 can proceed **in parallel** once Phase 12 is complete (they touch largely independent modules), but all three must converge before Phase 17 (dashboard needs data from all of them) and Phase 18 (OTA should update a firmware base that already includes emotion/movement and offline behavior, not a stripped-down earlier build).

---

## 36. AI Agent Implementation Briefs

Each brief below is designed to be handed to a coding agent **on its own**, with this document as background reference. An agent should never need to infer scope from a later phase's brief — if it's not stated here, it's not in scope for this phase.

---

### PHASE 0 — Requirements + Architecture Freeze
**GOAL:** Produce a signed-off, version-controlled copy of this architecture document (with Section 3's ARCHITECTURAL RECOMMENDATIONS explicitly accepted/rejected/modified by the team) as `docs/architecture/master-plan.md`, plus the initial shared contract files.
**CONTEXT:** No code exists yet. This phase is documentation/agreement, not implementation.
**FILES/SERVICES INVOLVED:** `docs/architecture/`, `docs/api/`, `docs/mqtt/` skeletons; shared contract stub files: `contracts/ai_response_contract.md`, `contracts/emotion_action_enum.md`, `contracts/mqtt_topics.md`.
**FUNCTIONAL REQUIREMENTS:** Finalize the AI Response Contract fields (Section 15.3), the emotion/action enum values (Section 19), the MQTT topic list (Section 11.1), and resolve every Section 3 recommendation with an explicit accept/reject decision recorded in the doc.
**INTERFACES:** None yet (definitions only).
**INPUT/OUTPUT:** Input: this document. Output: frozen contract files + decision log.
**DEPENDENCIES:** None.
**SECURITY REQUIREMENTS:** None yet — but the secret-management approach (Section 26) must be agreed on paper here.
**IMPLEMENTATION ORDER:** Review doc → resolve open recommendations → freeze contract files → tag repo `v0.1-architecture`.
**TEST REQUIREMENTS:** None (no code).
**ACCEPTANCE CRITERIA:** All three team members have reviewed and signed off; contract files committed.
**DEFINITION OF DONE:** Section 37 applies generically; for this phase specifically: contract files exist, are committed, and are referenced (not duplicated) by all later phases.
**DO NOT MODIFY / DO NOT BUILD YET:** No firmware, backend, or AI service code.
**EXPECTED DELIVERABLES:** `docs/architecture/master-plan.md`, `contracts/*.md`.

---

### PHASE 1 — Hardware/Software Interface Definition
**GOAL:** Finalize `hardware_config` values (pin map, I2S sample rate/bit depth, PWM channels, servo safe-angle ranges per joint, sensor list, battery-read method) in writing, agreed with the Electronics Engineer.
**CONTEXT:** Builds on Phase 0's frozen contracts; nothing else exists yet.
**FILES/SERVICES INVOLVED:** `firmware/hardware_config.h` (values only, no logic), `docs/hardware-interface/`.
**FUNCTIONAL REQUIREMENTS:** Document every item in Section 6's table with concrete values (not placeholders).
**INTERFACES:** The `hardware_config.h` header is the interface — every later firmware module reads from it, never hardcodes hardware assumptions.
**INPUT/OUTPUT:** Input: Electronics Engineer's board spec. Output: `hardware_config.h` + `docs/hardware-interface/interface.md`.
**DEPENDENCIES:** Phase 0.
**SECURITY REQUIREMENTS:** Servo safe-angle ranges must be defined here — this is the value later clamped in Phase 3/13's `hal/servo`, a security/safety-relevant constraint even though this phase itself writes no logic.
**IMPLEMENTATION ORDER:** Collect hardware specs → draft config header → review with Electronics → commit.
**TEST REQUIREMENTS:** Values sanity-checked against datasheet/board bring-up notes (no automated test at this stage).
**ACCEPTANCE CRITERIA:** Electronics Engineer confirms every value matches the actual board.
**DEFINITION OF DONE:** `hardware_config.h` committed and referenced (not yet consumed by code).
**DO NOT MODIFY / DO NOT BUILD YET:** No driver logic, no state machine, no networking.
**EXPECTED DELIVERABLES:** `firmware/hardware_config.h`, `docs/hardware-interface/interface.md`.

---

### PHASE 2 — ESP32 Foundation
**GOAL:** Bootable firmware skeleton: `hal/` interfaces (stubbed), `state/` machine with `BOOT`/`SETUP`/`ERROR` states wired, NVS-based config storage, basic local logging (`diagnostics/` ring buffer), `identity/` provisioning-check.
**CONTEXT:** First real firmware code. Uses `hardware_config.h` from Phase 1.
**FILES/SERVICES INVOLVED:** `firmware/hal/`, `firmware/state/`, `firmware/diagnostics/` (basic), `firmware/identity/`, `firmware/app/` (boot orchestrator).
**FUNCTIONAL REQUIREMENTS:** Device boots, runs self-test stubs, determines provisioned/unprovisioned, enters `SETUP` or attempts `CONNECTING` (network itself not yet implemented — transition can be a no-op/log-only placeholder pending Phase 4), logs boot sequence locally.
**INTERFACES:** Internal module boundaries per Section 7's table only — no network interfaces yet.
**INPUT/OUTPUT:** Input: power-on. Output: boot log, state transitions.
**DEPENDENCIES:** Phase 1 (`hardware_config.h`).
**SECURITY REQUIREMENTS:** NVS config storage must use ESP-IDF's flash-encryption-compatible APIs even though no secrets are stored yet, so Phase 7/26 doesn't require re-architecting storage.
**IMPLEMENTATION ORDER:** `hal/` stub interfaces → `state/` machine skeleton (transition table from Section 8, unimplemented actions stubbed) → `identity/` provisioning check → `diagnostics/` ring buffer → `app/` boot wiring.
**TEST REQUIREMENTS:** State-machine unit tests for every Section 8 transition row (even if the entered state does nothing yet); boot integration test on real board.
**ACCEPTANCE CRITERIA:** Board boots, correctly detects unprovisioned state, logs are retrievable (e.g., via serial) after boot.
**DEFINITION OF DONE:** Section 37 generic criteria; state machine transition table fully covered by tests even though most states are still stubs.
**DO NOT MODIFY / DO NOT BUILD YET:** No real hardware drivers beyond stubs (Phase 3), no networking (Phase 4), no AI/OTA/content logic.
**EXPECTED DELIVERABLES:** Bootable firmware image, passing state-machine unit tests.

---

### PHASE 3 — ESP32 Hardware Control
**GOAL:** Replace Phase 2's `hal/` stubs with real drivers: mic (I2S in), speaker (I2S out), servo (PWM/LEDC with clamping to Phase 1's safe ranges), LED/eyes, sensors, battery read.
**CONTEXT:** Builds directly on Phase 2's skeleton.
**FILES/SERVICES INVOLVED:** `firmware/hal/mic`, `hal/speaker`, `hal/servo`, `hal/led`, `hal/sensors`, `hal/battery`.
**FUNCTIONAL REQUIREMENTS:** Each driver implemented per Section 7's table; servo driver clamps to `hardware_config.h` ranges unconditionally (not optionally).
**INTERFACES:** `hal/*` public functions as consumed by `audio/`, `actions/`, `diagnostics/` in later phases — interface signatures should be stable from here on.
**INPUT/OUTPUT:** Input: raw hardware signals. Output: normalized readings / actuator writes, per Section 7.
**DEPENDENCIES:** Phase 1, Phase 2.
**SECURITY REQUIREMENTS:** Servo clamping is a hard, non-bypassable check inside `hal/servo` itself (not relying on any caller to have already validated the angle) — this is the first enforcement point of the "network can't command unsafe motion" guarantee (Section 19).
**IMPLEMENTATION ORDER:** Mic → speaker → servo (with clamping) → LED → sensors → battery, each bench-tested against real hardware before moving to the next.
**TEST REQUIREMENTS:** Bench test each driver per Section 7's "testing" column; explicit test that servo clamping rejects/clamps out-of-range requests.
**ACCEPTANCE CRITERIA:** All drivers verified against real board by Electronics + Software jointly.
**DEFINITION OF DONE:** Generic Section 37 criteria; servo clamp test specifically included in the automated/bench suite.
**DO NOT MODIFY / DO NOT BUILD YET:** No `actions/` sequencing logic yet (that's Phase 13) — this phase only proves each driver works in isolation (e.g., "move servo to angle X," not "perform a wave").
**EXPECTED DELIVERABLES:** Working `hal/*` drivers, bench test results/log.

---

### PHASE 4 — Wi-Fi + Network Layer
**GOAL:** `network/wifi` module: provisioning flow (Flow 3), connect/reconnect with backoff, RSSI monitoring; `state/` machine's `SETUP`/`CONNECTING` states become fully functional.
**CONTEXT:** First real connectivity; still no backend to talk to (Django doesn't exist until Phase 5), so this phase's "success" is measured by reaching a real Wi-Fi AP, not reaching the backend.
**FILES/SERVICES INVOLVED:** `firmware/network/wifi/`, `firmware/state/` (updated transitions).
**FUNCTIONAL REQUIREMENTS:** First-boot provisioning (local AP or BLE — pick one, document choice), stored-credential reconnect on subsequent boots, exponential backoff on failure, RSSI available to `diagnostics/`.
**INTERFACES:** `network/wifi` exposes connection-status events consumed by `state/`.
**INPUT/OUTPUT:** Input: SSID/password (provisioning) or stored credentials. Output: connection status events.
**DEPENDENCIES:** Phase 2, Phase 3 (may use an LED pattern from `hal/led` to indicate setup mode).
**SECURITY REQUIREMENTS:** Provisioning credential exchange (if BLE/local-AP) must not be trivially sniffable in plaintext where avoidable (e.g., WPA2 local AP with a device-specific password, not an open AP) — flagged as a bench decision, not over-specified here.
**IMPLEMENTATION ORDER:** Provisioning flow → credential storage (NVS, encrypted) → connect logic → reconnect/backoff → RSSI reporting hook.
**TEST REQUIREMENTS:** Simulate AP loss, verify reconnect timing; test provisioning flow end-to-end on real hardware.
**ACCEPTANCE CRITERIA:** Device reliably connects, reconnects after simulated AP loss within documented backoff bounds, survives reboot with stored credentials.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No HTTPS/MQTT client logic yet (Phase 7/8) — this phase stops at "has an IP address and internet reachability," not "talks to our backend."
**EXPECTED DELIVERABLES:** Working Wi-Fi provisioning + reconnect, on real hardware.

---

### PHASE 5 — Django Foundation
**GOAL:** Django project skeleton: `core/` (settings, health check, common permission classes), `accounts/` (User, Parent, ChildProfile models + basic auth: signup/login/session).
**CONTEXT:** First backend code; independent of firmware phases, can proceed in parallel with Phase 3/4 by the Software Developer.
**FILES/SERVICES INVOLVED:** `backend/core/`, `backend/accounts/`.
**FUNCTIONAL REQUIREMENTS:** Signup/login/logout, `Parent`/`ChildProfile` CRUD (parent-scoped), `/health` endpoint, base Docker Compose service definition for Django+Postgres.
**INTERFACES:** REST endpoints under `/api/v1/auth/...`, `/api/v1/children/...`; Django Template pages for login (dashboard styling deferred to Phase 17, functional-only here).
**INPUT/OUTPUT:** Input: HTTP requests from browser/test client. Output: JSON/HTML responses per Section 10.
**DEPENDENCIES:** Phase 0 (contracts).
**SECURITY REQUIREMENTS:** Password hashing (Django default), CSRF on forms, ownership-scoped querysets from day one (Section 25) even though only `ChildProfile` exists so far.
**IMPLEMENTATION ORDER:** Project skeleton + Docker Compose (Django+Postgres only) → `core/` health check → `accounts/` models/migrations → auth views → `ChildProfile` CRUD.
**TEST REQUIREMENTS:** Unit tests for models; API tests for auth + ChildProfile ownership boundaries (a parent cannot read another parent's child).
**ACCEPTANCE CRITERIA:** Signup/login works; `/health` returns 200; ownership boundary tests pass.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No `devices/`, `conversations/`, dashboards, MQTT, or AI service integration yet.
**EXPECTED DELIVERABLES:** Running Django service in Docker Compose, passing auth/ownership tests.

---

### PHASE 6 — Database + Device Management
**GOAL:** Full Section 13 schema (all models, even those unused until later phases, so migrations aren't fragmented across phases) migrated; `devices/` app implementing Robot, DeviceCredential, registration, and pairing endpoints (Flow 4).
**CONTEXT:** Extends Phase 5's Django project.
**FILES/SERVICES INVOLVED:** `backend/devices/`, plus model definitions (not necessarily full business logic) for every Section 13 entity across their eventual apps (`conversations/`, `memory/`, `configuration/`, `firmware/`, `content/`, `safety/`, `analytics/`) so the schema is complete even though most apps' *views/logic* come later.
**FUNCTIONAL REQUIREMENTS:** `POST /api/v1/devices/register` (device self-registers with public key, gets a pairing code), `POST /api/v1/devices/pair` (parent claims a robot via pairing code, dashboard-side call), `RobotState`/`RobotEvent` models ready for Phase 8's ingestion.
**INTERFACES:** REST endpoints per above; DB schema is the interface every later phase builds on — treated as frozen-unless-migrated, not silently altered.
**INPUT/OUTPUT:** Input: device public key + pairing code exchange. Output: `Robot`/`DeviceCredential` records, pairing confirmation.
**DEPENDENCIES:** Phase 5.
**SECURITY REQUIREMENTS:** Pairing codes short-lived/single-use; device public key stored, private key never touches the backend (Section 26).
**IMPLEMENTATION ORDER:** Full schema migration → `devices/` models → registration endpoint → pairing endpoint → basic admin-only device list view (functional, not styled).
**TEST REQUIREMENTS:** Migration test (schema applies cleanly); API tests for registration/pairing flow including invalid/expired pairing code cases.
**ACCEPTANCE CRITERIA:** A simulated device can register and be paired to a test parent account via API calls alone (no real ESP32 needed yet).
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No real device-originated HTTPS calls yet (that's Phase 7 proving it works from actual firmware) — Phase 6 proves it works via test client/curl.
**EXPECTED DELIVERABLES:** Full DB schema migrated, `devices/` app with tested registration/pairing endpoints.

---

### PHASE 7 — ESP32 ↔ Django Communication
**GOAL:** Real device-originated HTTPS calls: `network/http` + `security/` (challenge-response device auth, Section 26) firmware modules, exercised against Phase 6's real registration/pairing/auth endpoints.
**CONTEXT:** First real integration between firmware and backend.
**FILES/SERVICES INVOLVED:** `firmware/network/http/`, `firmware/security/`, `backend/devices/` (auth challenge/token endpoints added if not already in Phase 6).
**FUNCTIONAL REQUIREMENTS:** Device generates/stores keypair (Phase 2's encrypted-NVS groundwork used here), registers, completes challenge-response, receives/stores/refreshes device access JWT; `AUTHENTICATING`→`ONLINE` state transition becomes real.
**INTERFACES:** Section 10's HTTPS contract (Bearer auth, JSON payloads) is now live end-to-end.
**INPUT/OUTPUT:** Input: backend responses. Output: authenticated device state, stored token.
**DEPENDENCIES:** Phase 4 (network), Phase 6 (backend endpoints).
**SECURITY REQUIREMENTS:** TLS certificate validation enforced (no disabled-validation builds shipped even for this test phase, per Section 10); token refresh logic tested for both happy path and expiry-forced-reauth path.
**IMPLEMENTATION ORDER:** `security/` keypair generation/storage → registration call → challenge-response auth call → token storage/refresh → `network/http` generalized for future endpoints → `state/` wiring for `AUTHENTICATING`/`ONLINE`.
**TEST REQUIREMENTS:** Integration test: real (or emulated) device completes registration→pairing(manual, via dashboard-less API call)→auth against a staging Django instance; token refresh timing test.
**ACCEPTANCE CRITERIA:** A real ESP32-S3 board reaches `ONLINE` state against a real (staging) backend.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No MQTT yet (Phase 8), no conversation/voice logic yet (Phase 9-12).
**EXPECTED DELIVERABLES:** Real device reaching `ONLINE` state end-to-end against staging backend.

---

### PHASE 8 — MQTT Real-Time Communication
**GOAL:** Mosquitto deployed with ACLs; `network/mqtt` (firmware) + `mqtt_bridge/` (Django) implementing Section 11's full topic set: heartbeat, status, event, command, and the three `*/notify` topics (payload delivery for those still pending in later phases, but the notify mechanism itself works now).
**CONTEXT:** Second real integration point, building on Phase 7's device identity.
**FILES/SERVICES INVOLVED:** `infra/mosquitto/` (broker config + ACL file), `firmware/network/mqtt/`, `backend/mqtt_bridge/`.
**FUNCTIONAL REQUIREMENTS:** Device connects to broker using token-derived MQTT credentials, publishes heartbeat/status/event, subscribes to its own `command`/`*/notify` topics; Django publishes test commands and receives status/events, applying them via the same service-layer functions Section 12.2 specifies (not a separate code path).
**INTERFACES:** Section 11's topic hierarchy, exactly as specified — no ad hoc topics invented.
**INPUT/OUTPUT:** Input/output per Section 11.2.
**DEPENDENCIES:** Phase 7 (device identity/token), Phase 6 (`RobotState`/`RobotEvent` models).
**SECURITY REQUIREMENTS:** Per-device ACL scoping (Section 11.2) verified with an explicit negative test (device A cannot publish/subscribe to device B's topics).
**IMPLEMENTATION ORDER:** Broker deployment + ACL config → firmware MQTT connect/subscribe/publish → LWT wiring → Django `mqtt_bridge/` subscriber (status/event ingestion via existing service functions) → Django publisher (command/notify) → reconnect/backoff testing both sides.
**TEST REQUIREMENTS:** ACL boundary test (cross-device negative test), reconnect/backoff timing test, LWT-triggers-offline-status test.
**ACCEPTANCE CRITERIA:** Real device shows correct online/offline status on backend within expected heartbeat/LWT timing; a test command published from Django is received and logged by the device.
**DEFINITION OF DONE:** Generic Section 37 criteria; ACL negative test specifically required.
**DO NOT MODIFY / DO NOT BUILD YET:** `command` topic exists mechanically but no real `actions/` execution yet (Phase 13) — commands received in this phase can just be logged/acked, not physically executed.
**EXPECTED DELIVERABLES:** Working MQTT layer both directions, ACL-tested.

---

### PHASE 9 — AI Service Foundation
**GOAL:** AI service skeleton (FastAPI), internal API surface matching Section 15's contract shapes, with **stubbed** pipeline stages (fixed/mock outputs) — no real provider integration yet.
**CONTEXT:** Can proceed in parallel with Phase 7/8 by the AI Developer.
**FILES/SERVICES INVOLVED:** `ai_service/api/`, `ai_service/contract/`, stub modules for `stt/`, `understanding/`, `context/` (client stub), `llm/`, `safety/`, `emotion/`, `action/`, `tts/`.
**FUNCTIONAL REQUIREMENTS:** Internal endpoint accepts the AI Input Contract shape (Section 15.2) and returns a syntactically valid AI Output Contract (Section 15.3) with placeholder/mock content — proves the contract shape and service boundary before real intelligence is wired in.
**INTERFACES:** The internal HTTP API itself — this is the interface Django's Phase 11 integration will consume; must not change shape without updating the frozen contract file (Phase 0).
**INPUT/OUTPUT:** Per Section 15.2/15.3, with mock values.
**DEPENDENCIES:** Phase 0 (frozen contract).
**SECURITY REQUIREMENTS:** Internal service token auth (Section 25) even at stub stage; service not exposed to public internet (Docker Compose internal network only) from day one.
**IMPLEMENTATION ORDER:** FastAPI skeleton → internal API route matching contract shape → stub each pipeline stage returning deterministic mock data → `contract/` assembly.
**TEST REQUIREMENTS:** Contract-shape validation tests (request/response schema); internal-auth rejection test for unauthenticated calls.
**ACCEPTANCE CRITERIA:** A test client can call the AI service internally and get a well-formed (if mock) AI Output Contract response.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No real STT/LLM/TTS provider calls (Phase 10), no Django integration yet beyond a test client (Phase 11).
**EXPECTED DELIVERABLES:** Running AI service container with mock pipeline, passing contract-shape tests.

---

### PHASE 10 — STT/LLM/TTS Integration
**GOAL:** Replace Phase 9's stubs with real provider calls in `stt/`, `llm/`, `tts/`, plus real `lang_detect/`, `understanding/`, and the two-stage `safety/` filter (Section 14.2/17.1) with an initial fixture-based test set.
**CONTEXT:** Builds on Phase 9's skeleton; provider accounts/credentials must exist (AI Developer's setup).
**FILES/SERVICES INVOLVED:** `ai_service/stt/`, `ai_service/llm/`, `ai_service/tts/`, `ai_service/lang_detect/`, `ai_service/understanding/`, `ai_service/safety/`.
**FUNCTIONAL REQUIREMENTS:** Real audio-in → real transcript (Bangla + English); real LLM-generated response using the agreed persona/system prompt (AR-16); pre- and post-generation safety filtering against an initial adversarial fixture set (Section 31 level 14, seeded here); real TTS audio-out.
**INTERFACES:** Same internal API shape as Phase 9 — no shape change, only real content behind it.
**INPUT/OUTPUT:** Per Section 15.2/15.3, now with real content.
**DEPENDENCIES:** Phase 9, provider account setup (outside this document's scope).
**SECURITY REQUIREMENTS:** Provider API keys in AI service secret store only (env/secrets manager) — explicit test/check that no key appears in logs or committed files.
**IMPLEMENTATION ORDER:** STT integration + test → lang_detect → understanding → safety (pre-stage) → LLM integration + test → safety (post-stage) → TTS integration + test → full pipeline wiring.
**TEST REQUIREMENTS:** Section 31 level 6 (AI service testing) fully applies here — safety fixture set (known-safe + known-risky inputs) must reach 100% correct categorization before this phase is considered done.
**ACCEPTANCE CRITERIA:** Real audio in both languages produces correct transcripts; safety fixture set fully passes; TTS output is intelligible in both languages.
**DEFINITION OF DONE:** Generic Section 37 criteria plus 100% safety-fixture pass rate.
**DO NOT MODIFY / DO NOT BUILD YET:** No Django integration yet (Phase 11) — tested via direct calls to the AI service.
**EXPECTED DELIVERABLES:** Fully functional AI pipeline (real providers), safety fixture suite committed and passing.

---

### PHASE 11 — AI ↔ Backend Integration
**GOAL:** Django's `conversations/` app calls the real AI service; `Message`/`AIInteraction`/`SafetyEvent` persistence wired; hard-failure fallback (Section 15.3) implemented.
**CONTEXT:** Connects Phase 6/8's backend with Phase 10's real AI service.
**FILES/SERVICES INVOLVED:** `backend/conversations/`, `backend/safety/` (event logging), `backend/memory/` (write path stub — full memory logic is Phase 14, but `memory_writes` field must be accepted and stored here even if retrieval/context-injection isn't built yet).
**FUNCTIONAL REQUIREMENTS:** `conversations/` view accepts audio+context, calls AI service internal API, persists results, applies `memory_writes`, logs `SafetyEvent` on flagged content, implements the generic fallback on AI-service hard failure.
**INTERFACES:** Django-to-AI-service internal call per Section 15; no public-facing conversation endpoint changes beyond what Phase 6 already scoped for devices.
**INPUT/OUTPUT:** Input: device audio (still via a test client at this phase, real device integration is Phase 12). Output: persisted `Message`/`AIInteraction`/`SafetyEvent` rows.
**DEPENDENCIES:** Phase 6, Phase 8 (for eventual real device path), Phase 10.
**SECURITY REQUIREMENTS:** Internal call uses the internal service token (Section 25); `SafetyEvent` access restricted per Section 30 from the moment the model is written to (not retrofitted later).
**IMPLEMENTATION ORDER:** `conversations/` models finalized (if not already in Phase 6) → AI service client wrapper → view wiring → persistence logic → fallback-on-failure logic → `SafetyEvent` logging hook.
**TEST REQUIREMENTS:** Integration test: simulated audio-in produces persisted `Message`+`AIInteraction`; forced AI-service-down test triggers correct fallback without crashing.
**ACCEPTANCE CRITERIA:** End-to-end (test-client-to-AI-service, not yet real device) conversation turn works and is fully persisted correctly, including a forced-failure fallback case.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No real device audio path yet (Phase 12), no memory *retrieval*/context injection yet (Phase 14 — only the write-side field acceptance is in scope here).
**EXPECTED DELIVERABLES:** Working Django↔AI-service integration, tested via test client.

---

### PHASE 12 — Complete Voice Conversation Pipeline
**GOAL:** Real device audio capture (`audio/`, `voice/wakeword`, `voice/vad`) → real HTTPS upload → Phase 11's Django/AI pipeline → real HTTPS response → real device playback (`audio/playback`) — Flow 6/7 fully working end-to-end on hardware.
**CONTEXT:** The first true three-tier integration milestone.
**FILES/SERVICES INVOLVED:** `firmware/audio/`, `firmware/voice/`, existing `network/http`, `backend/conversations/` (from Phase 11).
**FUNCTIONAL REQUIREMENTS:** Wake word triggers `LISTENING`, VAD detects end-of-utterance, audio uploaded, `PROCESSING` state shown, response received and played in `SPEAKING` state, robot returns to `IDLE`.
**INTERFACES:** Section 10's audio upload/download contract now exercised by real firmware.
**INPUT/OUTPUT:** Input: child's spoken utterance. Output: spoken robot reply (no emotion/movement yet — that's Phase 13, so the reply plays as speech only).
**DEPENDENCIES:** Phase 3 (mic/speaker drivers), Phase 4 (network), Phase 7 (auth), Phase 11 (backend pipeline).
**SECURITY REQUIREMENTS:** Audio upload authenticated with device's current token; token-refresh-mid-conversation edge case tested.
**IMPLEMENTATION ORDER:** `voice/wakeword` → `voice/vad` → `audio/capture` → HTTPS upload wiring → `PROCESSING` state → HTTPS response handling → `audio/playback` → full state-machine wiring for the `IDLE`→`LISTENING`→`PROCESSING`→`SPEAKING`→`IDLE` cycle.
**TEST REQUIREMENTS:** End-to-end test on real hardware (Section 31 level 8); latency measurement (feeds Section 31 level 13 baseline); barge-in interruption test (Section 8.2).
**ACCEPTANCE CRITERIA:** A child's spoken question, on real hardware, produces a correct, audible, timely spoken response.
**DEFINITION OF DONE:** Generic Section 37 criteria; this phase's E2E test is treated as a project milestone, not just a phase gate.
**DO NOT MODIFY / DO NOT BUILD YET:** No emotion/movement (Phase 13), no memory continuity beyond what Phase 11 already stores (Phase 14), no offline fallback yet (Phase 16).
**EXPECTED DELIVERABLES:** Working end-to-end voice conversation on real hardware.

---

### PHASE 13 — Emotion + Movement Integration
**GOAL:** `actions/` firmware module fully implemented; AI service's `emotion/`/`action/` modules produce real (not mock) values; `MOVING` overlay behavior (Section 8.3) working alongside `SPEAKING`.
**CONTEXT:** Extends Phase 12's working pipeline.
**FILES/SERVICES INVOLVED:** `firmware/actions/`, `ai_service/emotion/`, `ai_service/action/`.
**FUNCTIONAL REQUIREMENTS:** Each emotion/action enum value (frozen in Phase 0) maps to a concrete servo/LED sequence on-device, clamped per Phase 3's `hal/servo`; AI service selects contextually appropriate values from real response content.
**INTERFACES:** The emotion/action enum contract (Section 19) — binding, not extended without updating the frozen contract file.
**INPUT/OUTPUT:** Input: `{emotion, action}` from AI response. Output: physical motion + LED pattern, concurrent with speech playback.
**DEPENDENCIES:** Phase 3, Phase 12.
**SECURITY REQUIREMENTS:** Unknown/malformed action codes fall back to neutral/idle (never an undefined servo command) — explicit negative test required.
**IMPLEMENTATION ORDER:** Define action-to-sequence mapping table → implement `actions/` sequencer → wire concurrency with `SPEAKING` state → AI-side real emotion/action selection logic → end-to-end test.
**TEST REQUIREMENTS:** Each known action code tested for expected servo/LED sequence; unknown-code fallback test; concurrency test (movement + speech simultaneously, no audio glitching from PWM interference — a hardware-adjacent test the Electronics Engineer should co-verify).
**ACCEPTANCE CRITERIA:** Robot visibly emotes/moves appropriately alongside real conversational responses on hardware.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No new emotion/action values beyond the frozen enum without a contract update.
**EXPECTED DELIVERABLES:** Working emotion/movement on real hardware, synced with speech.

---

### PHASE 14 — Memory/Context
**GOAL:** `memory/` Django app fully implemented (CRUD, retrieval logic feeding Section 15.2's `context` field), AI service's `context/` client consuming it, parent-visible/deletable memory list (API only — dashboard UI is Phase 17).
**CONTEXT:** Extends Phase 11's write-only stub to full read/write.
**FILES/SERVICES INVOLVED:** `backend/memory/`, `ai_service/context/` (client-side retrieval call).
**FUNCTIONAL REQUIREMENTS:** Recent-conversation-turn context assembly + `Memory` entry retrieval (simple relevance selection per Section 20), included in every AI Input Contract call; `Memory` CRUD API with parent-ownership scoping.
**INTERFACES:** Section 15.2's `context` field now carries real assembled data.
**INPUT/OUTPUT:** Input: conversation history + child_profile_id. Output: assembled context object.
**DEPENDENCIES:** Phase 11.
**SECURITY REQUIREMENTS:** `Memory` API ownership-scoped exactly like `ChildProfile` (Section 25) from day one.
**IMPLEMENTATION ORDER:** `memory/` models (already migrated in Phase 6, logic now) → retrieval service function → context assembly in `conversations/` → AI service `context/` client wiring → `Memory` CRUD API.
**TEST REQUIREMENTS:** Retrieval correctness test (right entries surfaced for a given child); ownership boundary test; end-to-end test that a previously-stated fact is correctly recalled in a later conversation.
**ACCEPTANCE CRITERIA:** A fact mentioned in one conversation is correctly referenced in a later one, on real hardware.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No vector/embedding-based retrieval (flagged optional enhancement, Section 20) unless explicitly greenlit — simple relevance selection is sufficient scope here.
**EXPECTED DELIVERABLES:** Working memory/context, tested end-to-end.

---

### PHASE 15 — Educational/Story/Quiz Features
**GOAL:** `mode` field (Section 15.2) wired through the full pipeline; AI service prompt/behavior differentiation for story/quiz/educational modes; `UsageLog` rollups for dashboard consumption.
**CONTEXT:** Extends Phase 14's context-aware pipeline.
**FILES/SERVICES INVOLVED:** `ai_service/understanding/`, `ai_service/llm/` (mode-aware prompting), `backend/analytics/` (`UsageLog`).
**FUNCTIONAL REQUIREMENTS:** Story continuity via existing context mechanism (no new story engine, per Section 21); LLM-driven quiz mode for MVP; `UsageLog` entries created per conversation/mode with topic/score data.
**INTERFACES:** `mode` field now functionally affects AI behavior; no new external interface.
**INPUT/OUTPUT:** Input: `mode` + existing context. Output: mode-appropriate response + `UsageLog` entry.
**DEPENDENCIES:** Phase 14.
**SECURITY REQUIREMENTS:** Safety filter (Phase 10) applies identically regardless of mode — explicit test that quiz/story modes don't bypass safety filtering.
**IMPLEMENTATION ORDER:** Mode field plumbing → story-mode prompt behavior → quiz-mode prompt behavior + scoring extraction → `UsageLog` write-path → end-to-end test per mode.
**TEST REQUIREMENTS:** Mode-specific behavior tests; safety-filter-still-applies-in-every-mode test; `UsageLog` correctness test.
**ACCEPTANCE CRITERIA:** Story mode maintains continuity across turns; quiz mode produces sensible questions/scoring; usage data recorded correctly.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No curated quiz question bank (explicitly deferred per Section 21's recommendation) unless later greenlit as an enhancement.
**EXPECTED DELIVERABLES:** Working story/quiz/educational modes, `UsageLog` populated.

---

### PHASE 16 — Offline Functionality
**GOAL:** `content/` firmware + backend modules; versioned offline bundle (canned responses, story/quiz content) download and local playback; `OFFLINE` state fully functional (Section 22).
**CONTEXT:** Reuses the secure-download pattern that Phase 18 will formalize for firmware — here scoped specifically to content bundles, not firmware binaries.
**FILES/SERVICES INVOLVED:** `firmware/content/`, `backend/content/`, `state/` (`OFFLINE` transitions).
**FUNCTIONAL REQUIREMENTS:** Bundle versioning/download-on-notify (Section 11's `content/notify` topic, already mechanically available since Phase 8), local storage (SPIFFS/LittleFS), offline-mode canned responses/simple games/story playback per Section 22.1, graceful `ONLINE`→`OFFLINE` transition on connectivity loss.
**INTERFACES:** Reuses Section 10's HTTPS download pattern + Section 11's notify pattern — no new protocol invented.
**INPUT/OUTPUT:** Input: connectivity-loss event, or explicit bundle update. Output: local content available for offline interaction.
**DEPENDENCIES:** Phase 8 (notify mechanism), Phase 4 (network layer, to detect loss).
**SECURITY REQUIREMENTS:** Bundle checksum verification before applying (Section 29's "corrupted data" row) — corrupt bundle rejected, previous kept.
**IMPLEMENTATION ORDER:** Backend `content/` bundle model + versioning → download endpoint → firmware download/storage logic → checksum verification → `OFFLINE` state wiring → offline interaction logic (canned responses, local game logic).
**TEST REQUIREMENTS:** Forced disconnect mid-session test (Section 31 level 11); corrupted-bundle rejection test; offline interaction functional test.
**ACCEPTANCE CRITERIA:** Robot gracefully falls back to offline content on connectivity loss and resumes normal operation cleanly on reconnect.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No Raspberry Pi/edge-AI tier (Section 22.4 — explicitly not adopted); no firmware OTA logic yet (Phase 18, even though the pattern is similar, the firmware-specific signing/rollback logic is out of scope here).
**EXPECTED DELIVERABLES:** Working offline mode on real hardware.

---

### PHASE 17 — Parent/Admin Web Dashboard
**GOAL:** `dashboard_parent/` and `dashboard_admin/` Django Template apps implementing Sections 23/24 in full, against the existing REST API (no new backend business logic invented here beyond what's needed to serve/aggregate existing data).
**CONTEXT:** Needs essentially every backend app built so far to have real data to display.
**FILES/SERVICES INVOLVED:** `backend/dashboard_parent/`, `backend/dashboard_admin/`, shared template base + CSS design tokens.
**FUNCTIONAL REQUIREMENTS:** All bullet points of Sections 23 and 24; design direction per the brief's luxury/cinematic spec (Section 23's design note), implemented as static CSS tokens, not a heavy JS framework.
**INTERFACES:** Consumes existing `/api/v1/...` endpoints — the dashboard is architecturally just another API client (per the "future mobile app" principle).
**INPUT/OUTPUT:** Input: parent/admin browser interactions. Output: rendered pages + AJAX calls to existing API.
**DEPENDENCIES:** Phases 5, 6, 11, 14, 15, and Phase 8 (for live status), Phase 18 once it exists (firmware update UI — can be stubbed here and completed once Phase 18 lands, or this phase can be sequenced to slightly trail Phase 18 if the team prefers; documented dependency, not a hard blocker for the rest of the dashboard).
**SECURITY REQUIREMENTS:** Role-gated admin views (Section 25); CSRF on all forms; no sensitive `SafetyEvent` raw content exposed without appropriate access control (Section 30).
**IMPLEMENTATION ORDER:** Shared template base + design tokens → parent auth/pairing/status pages → parent controls/usage/memory/history pages → admin users/robots pages → admin firmware/logs/safety/analytics pages.
**TEST REQUIREMENTS:** View-level access-control tests (parent can't reach admin pages; parent A can't see parent B's data); functional UI smoke tests for each page.
**ACCEPTANCE CRITERIA:** All Section 23/24 features usable end-to-end by a real parent/admin test account.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** No new backend data models beyond what already exists — this phase is presentation-layer only.
**EXPECTED DELIVERABLES:** Working parent + admin dashboards.

---

### PHASE 18 — OTA
**GOAL:** `ota/` firmware module, `backend/firmware/` app, CI-based signing pipeline, full Section 27 flow implemented and tested including forced-failure rollback.
**CONTEXT:** Deliberately late — updates a firmware base that's already feature-complete through Phase 17.
**FILES/SERVICES INVOLVED:** `firmware/ota/`, `backend/firmware/`, `infra/ci/sign-firmware` job.
**FUNCTIONAL REQUIREMENTS:** Full Section 27 ten-step flow; dual-partition rollback verified; admin dashboard firmware upload/rollout UI (extends Phase 17's admin dashboard).
**INTERFACES:** Reuses Section 10 (download) + Section 11 (`ota/notify`) patterns already proven in Phase 16.
**INPUT/OUTPUT:** Input: admin-uploaded firmware binary. Output: updated device firmware, `OTAUpdate` status history.
**DEPENDENCIES:** Phase 16 (download pattern proven), Phase 17 (admin UI to trigger from), stable firmware base through Phase 15/13.
**SECURITY REQUIREMENTS:** Firmware signing key exists only in CI (Section 26); signature verification is a hard gate before flashing — explicit tampered-image negative test required.
**IMPLEMENTATION ORDER:** CI signing job → `backend/firmware/` upload/metadata endpoints → `firmware/ota/` download+verify+apply → rollback test scenario (forced bad-boot) → admin UI rollout controls.
**TEST REQUIREMENTS:** Section 31 level 10 in full: valid image, tampered image, corrupted download, forced boot failure/rollback — all four required to pass.
**ACCEPTANCE CRITERIA:** A real OTA update, and a real forced-rollback, both succeed as specified on real hardware.
**DEFINITION OF DONE:** Generic Section 37 criteria plus all four OTA test scenarios passing.
**DO NOT MODIFY / DO NOT BUILD YET:** Nothing beyond OTA scope — no unrelated firmware feature changes bundled into an OTA-testing firmware build.
**EXPECTED DELIVERABLES:** Working, tested OTA pipeline including rollback.

---

### PHASE 19 — Security Hardening
**GOAL:** Dedicated audit/hardening pass across Sections 25/26: rate limiting tuned, credential rotation tooling built/tested, optional mTLS/MFA evaluated (AR-10/23), penetration-style testing executed.
**CONTEXT:** Baseline security already exists from every prior phase (principle 11) — this phase audits and strengthens, it does not introduce security for the first time.
**FILES/SERVICES INVOLVED:** Cross-cutting — `core/` middleware, `devices/` (credential rotation admin action), all API throttle configs.
**FUNCTIONAL REQUIREMENTS:** DRF throttle classes tuned per-endpoint; admin-triggered device credential rotation flow (Section 26) built if not already present; documented decision on mTLS/MFA (adopt or defer, per AR-10/23).
**INTERFACES:** No new external interfaces — hardening of existing ones.
**INPUT/OUTPUT:** N/A (audit/hardening).
**DEPENDENCIES:** All prior phases.
**SECURITY REQUIREMENTS:** This entire phase *is* the security requirement — see Section 31 level 9 for the test plan.
**IMPLEMENTATION ORDER:** Threat-model review against Sections 17/25/26 → rate limiting pass → credential rotation tooling → auth bypass/injection penetration tests → remediate findings.
**TEST REQUIREMENTS:** Section 31 level 9 in full: auth bypass, token replay/expiry, MQTT ACL bypass, API injection attempts — all must be correctly rejected.
**ACCEPTANCE CRITERIA:** Zero successful bypass/injection in the test suite; findings from manual review remediated or explicitly accepted-risk documented.
**DEFINITION OF DONE:** Generic Section 37 criteria plus a signed-off security review note.
**DO NOT MODIFY / DO NOT BUILD YET:** No new user-facing features — this phase is hardening-only.
**EXPECTED DELIVERABLES:** Security test suite + review note, remediations applied.

---

### PHASE 20 — Observability
**GOAL:** Section 28's logging/monitoring fully implemented: metrics endpoint, health-check-driven alerting, device heartbeat dashboards, error tracking.
**CONTEXT:** Most valuable once the system has real behavior to observe (post Phase 19).
**FILES/SERVICES INVOLVED:** `core/` (metrics endpoint), `diagnostics/` (firmware, already exists, extended for richer reporting), `infra/monitoring/`.
**FUNCTIONAL REQUIREMENTS:** Per-service `/health`, request latency/error-rate metrics, AI pipeline stage timing metrics, MQTT connection count metrics, alerting thresholds configured.
**INTERFACES:** Metrics endpoint format (e.g., Prometheus-compatible, if adopted per AR/Section 28).
**INPUT/OUTPUT:** Input: service internals. Output: metrics/log streams, alerts.
**DEPENDENCIES:** All service phases (5-18) for meaningful signal.
**SECURITY REQUIREMENTS:** Metrics/monitoring endpoints not publicly exposed (internal network or admin-authenticated only); logging redaction rules from Section 28 enforced (no raw child data in logs) — explicit test.
**IMPLEMENTATION ORDER:** Metrics instrumentation across Django/AI service → device heartbeat aggregation view (admin dashboard) → alerting rules → log-redaction audit.
**TEST REQUIREMENTS:** Redaction test (attempt to log something sensitive, verify it's scrubbed/blocked by policy); alert-fires-correctly test on simulated failure.
**ACCEPTANCE CRITERIA:** Operators can see system health and get alerted on failure without manually SSHing to check logs.
**DEFINITION OF DONE:** Generic Section 37 criteria.
**DO NOT MODIFY / DO NOT BUILD YET:** N/A — this phase is additive/cross-cutting only.
**EXPECTED DELIVERABLES:** Working metrics/monitoring/alerting.

---

### PHASE 21 — Full System Testing
**GOAL:** Execute Section 31 levels 8-15 comprehensively against a near-production staging environment with the fully assembled system.
**CONTEXT:** Everything exists; this phase is verification, not construction.
**FILES/SERVICES INVOLVED:** All, plus `docs/testing/` test plans and results.
**FUNCTIONAL REQUIREMENTS:** Every Flow in the brief's "End-to-End User Flows" list executed and verified against staging; every failure-matrix row (Section 29) fault-injected and verified.
**INTERFACES:** N/A (verification).
**INPUT/OUTPUT:** Input: staging environment. Output: test report, defect list, remediations.
**DEPENDENCIES:** All prior phases.
**SECURITY REQUIREMENTS:** Section 31 level 9 re-run against the fully assembled staging system (not just isolated components).
**IMPLEMENTATION ORDER:** Flow-by-flow scripted E2E execution → failure-matrix fault injection → performance/load testing → child-safety adversarial testing (fresh pass, not reused fixtures only) → defect triage/fix cycle.
**TEST REQUIREMENTS:** All of Section 31.
**ACCEPTANCE CRITERIA:** All flows pass; no open critical/high-severity defects; safety test set at 100% pass rate.
**DEFINITION OF DONE:** Generic Section 37 criteria plus a completed test report.
**DO NOT MODIFY / DO NOT BUILD YET:** N/A.
**EXPECTED DELIVERABLES:** Full test report, staging environment proven production-ready.

---

### PHASE 22 — Production Deployment
**GOAL:** Go-live per Section 33: production environment stood up, backup/restore drill executed, stakeholder acceptance (Section 40) obtained.
**CONTEXT:** Terminal phase.
**FILES/SERVICES INVOLVED:** `infra/` production configs, DNS/TLS cutover.
**FUNCTIONAL REQUIREMENTS:** Production Docker Compose deployment live, TLS certificates issued, backups running and restore-tested, monitoring/alerting live in production, first real device(s) registered in production.
**INTERFACES:** N/A (deployment).
**INPUT/OUTPUT:** N/A.
**DEPENDENCIES:** Phase 21 sign-off.
**SECURITY REQUIREMENTS:** Production secrets distinct from staging/dev (Section 32); production `SafetyEvent`/child data access restricted from day one of go-live.
**IMPLEMENTATION ORDER:** Production infra provisioning → secrets setup → deploy → TLS cutover → backup/restore drill → first production device registration → final Section 40 checklist walkthrough.
**TEST REQUIREMENTS:** Backup restore drill must succeed; smoke test of core flows against production.
**ACCEPTANCE CRITERIA:** Section 40's Final Master Checklist fully passes.
**DEFINITION OF DONE:** Generic Section 37 criteria plus Section 40 checklist complete.
**DO NOT MODIFY / DO NOT BUILD YET:** N/A — project is now live.
**EXPECTED DELIVERABLES:** Live production system.

---

## 37. Definition of Done (Universal)

A phase is complete only when **all** of the following are true — this is a checklist a coding agent must satisfy before declaring any phase finished, and a human should verify before greenlighting the next phase:

- [ ] Functionality described in the phase's brief exists and runs (not just "written," but exercised and working).
- [ ] Architecture/interfaces touched by this phase are documented (updated `docs/` per Section on Project Documentation, Appendix A).
- [ ] Any contract file (AI Response Contract, emotion/action enum, MQTT topics, hardware config) referenced by this phase is unchanged, or if a change was genuinely required, it was updated with sign-off, not silently diverged from.
- [ ] Security requirements listed in the phase's brief are implemented, not deferred.
- [ ] Tests listed in the phase's brief pass, including negative/failure-path tests, not just the happy path.
- [ ] Failure cases relevant to this phase (per Section 29's matrix, where applicable) are handled, not left as unhandled exceptions/crashes.
- [ ] Logging exists where Section 28 calls for it, and nothing prohibited by Section 28's "must not log" list is logged.
- [ ] No secrets are present in any committed file, firmware image, or log output (explicit check, not assumption).
- [ ] The integration contract with adjacent components (what this phase's output looks like to the next consumer) is preserved exactly as documented.
- [ ] Explicit acceptance criteria from the phase's brief pass.
- [ ] Relevant documentation (Appendix A) is updated to reflect what was actually built, not left describing only the plan.

---

## 38. Risk Register

| Risk | Probability | Impact | Why it matters | Early warning | Mitigation | Fallback |
|---|---|---|---|---|---|---|
| ESP32 memory/compute limitations | High | High | Audio buffering, wake-word model, and network stacks all compete for limited RAM on ESP32-S3 | Frequent heap-allocation failures/crashes during Phase 2-3 bring-up | Keep AI/heavy compute entirely off-device (already architected); size audio buffers conservatively; profile memory early, not at the end | Reduce wake-word model size / buffer windows; as a last resort, revisit PSRAM sizing with Electronics |
| Audio processing limitations (mic quality, echo/noise) | Medium | Medium | Poor audio in → poor STT accuracy → frustrating child experience | Early bench testing shows high STT error rate even in quiet rooms | Electronics Engineer selects an adequate mic/enclosure early (Phase 1); consider basic noise gating in `audio/capture` | Lower expectations for noisy-environment accuracy in v1; document as known limitation |
| Network dependency (core conversation needs internet) | High | High | Product is materially degraded offline (by design, Section 22) | Field testing shows Wi-Fi coverage issues in typical home environments | Solid offline-mode fallback (Phase 16); clear parent-facing communication about offline limitations | Documented, not "fixed" — this is an inherent architecture trade-off (Section 22.3) |
| AI latency (STT→LLM→TTS round trip) | Medium | Medium | Slow responses break the "conversational" feel a child expects | Phase 12/21 latency benchmarks exceed target | Pick providers/models with acceptable latency; consider streaming TTS playback (start playing before full response is ready) as a later optimization | Show a "thinking" animation (already planned, `PROCESSING` state) to bridge perceived latency |
| API/provider cost (STT/LLM/TTS usage-based billing) | Medium | High | Cost scales with usage; a popular product could get expensive fast | Billing alerts trigger unexpectedly early in a month | Rate-limit conversation turns per device/child (Section 26); monitor cost per conversation from Phase 10 onward | Add usage caps/quiet-hours enforcement more aggressively if cost becomes a constraint |
| MQTT reliability at scale | Low (MVP), Medium (growth) | Medium | Broker becomes a bottleneck/single point of failure as fleet grows | Connection counts/latency degrade as more devices connect | Mosquitto is adequate for MVP fleet size (AR-8); monitor connection metrics (Phase 20) | Migrate to EMQX (clustering) if/when scale demands it — documented upgrade path, not urgent now |
| OTA failure bricking a device | Low | High | A bad OTA rollout could disable a physical product a child relies on | OTA test failures in Phase 18, or field reports of devices stuck post-update | Dual-partition + signature verification + staged rollout (not all devices at once) | Automatic rollback (already architected); admin can halt a rollout mid-fleet |
| Security breach (device or backend) | Low | Very High | Children's data + physical devices in homes — reputational and safety-critical | Unusual auth failure patterns, unexpected data access in audit logs | Layered security architecture from Phase 2 onward (principle 11), dedicated hardening pass (Phase 19) | Incident response: credential rotation (Section 26), affected-account notification, post-mortem |
| Child safety filter failure (unsafe content reaches child) | Low | Very High | Core trust requirement for a children's product | Adversarial test set (Phase 10/21) fails, or a real incident is reported | Two-stage safety filter (Section 14.2), continuously expanded adversarial fixture set, parent-visible safety event reporting | Immediate prompt/filter patch + incident review; conservative fallback responses when uncertain |
| Database growth (conversation/log volume over time) | Medium | Low-Medium | Unbounded growth increases cost and query latency over time | Slow queries/growing backup times noticed in Phase 20 monitoring | Retention policy (Section 30) actively enforced (scheduled purge job), not just documented | Add indexing/partitioning if growth outpaces retention-driven pruning |
| Privacy/regulatory exposure (children's data handling) | Low-Medium | Very High | Children's products often face specific regulatory scrutiny (region-dependent) | Legal/compliance review flags a gap | Data minimization + parent control architecture (Section 30) built in from the start, not bolted on | Team should have this reviewed by someone with relevant legal expertise before wide launch — **explicitly flagged as outside this document's authority** |
| Hardware/software mismatch (Electronics changes board after firmware assumptions are set) | Medium | Medium | Firmware built against assumptions that later change causes rework | Frequent `hardware_config.h` change requests after Phase 3 | Phase 1's explicit sign-off process for hardware interface, before heavy firmware investment | Isolate hardware assumptions to `hardware_config.h` (already architected) so changes are localized, not scattered |
| Power consumption (battery life shorter than expected) | Medium | Medium | Always-on Wi-Fi + wake-word listening + servo use drains battery faster than a toy-buyer expects | Field/bench battery-life testing falls short of target | `SLEEP` state + configurable quiet hours (already architected) reduce idle drain; Electronics Engineer tunes power budget | Adjust default idle/sleep timing; consider deeper sleep modes between interactions |
| Production scalability (fleet grows beyond current single-VPS assumption) | Low (near-term) | Medium | Single-server Docker Compose deployment has a ceiling | Resource utilization consistently high in Phase 20 monitoring | Documented as a known MVP-scale limitation; architecture (stateless AI service, Django as central hub) is horizontally-scalable-friendly if needed later | Move to multi-server/orchestrated deployment (Section 39) when metrics justify it |

---

## 39. Future Expansion

Explicitly out of scope for the phases above, but the architecture is designed not to preclude these:

- **Native mobile app:** Backend REST API (Section 12) is already consumer-agnostic — a future iOS/Android app would call the same `/api/v1/...` endpoints the dashboard uses, with no backend redesign required (this was an explicit design principle, not an afterthought).
- **WebSocket/live dashboard updates:** AR-17's polling approach can be upgraded to Django Channels + Redis pub/sub for real-time dashboard updates without changing the underlying data model.
- **Edge-compute (Raspberry Pi) offline AI tier:** Documented as an alternative in Section 22.4 — would require a new firmware communication module and a scaled-down AI pipeline, but doesn't conflict with the base architecture's contracts (the AI Response Contract shape could, in principle, be produced by an on-Pi service using the same shape).
- **Curated quiz/story content bank:** Section 21's LLM-driven MVP approach can be supplemented with authored content without changing the pipeline architecture (just an additional content source `understanding/`/`llm/` can draw from).
- **Vector/embedding-based memory retrieval:** Section 20 notes simple relevance selection is sufficient for MVP; a proper retrieval system is a drop-in upgrade to `context/`'s retrieval logic, not a data-model change.
- **mTLS device authentication:** AR-10 notes the rotating-JWT approach as the MVP baseline; full mTLS is a stronger, drop-in upgrade to the device auth layer if/when the team has bandwidth.
- **EMQX clustering:** AR-8 notes Mosquitto as the MVP broker; migration to EMQX for horizontal scaling is a broker-swap, not an architecture change, since the topic hierarchy/ACL model (Section 11) is broker-agnostic.
- **Multi-region/orchestrated deployment (Kubernetes):** Noted in Section 33 as unnecessary now; the containerized, stateless-service design (AI service, Django) doesn't preclude this later.
- **Additional languages beyond Bangla/English:** The `language_hint`/detected-`language` mechanism (Section 21) is not hardcoded to two languages — adding a third is a provider/prompt change, not an architecture change.

---

## 40. Final Master Checklist

Use this before declaring the project production-ready (Phase 22 acceptance):

**Architecture & Contracts**
- [ ] Section 3's recommendations all explicitly resolved (accepted/rejected/modified) and recorded
- [ ] AI Response Contract, emotion/action enum, MQTT topic list frozen and unchanged without sign-off since Phase 0
- [ ] Hardware interface (`hardware_config.h`) matches the actual production board

**Core Functionality**
- [ ] Full voice conversation pipeline works end-to-end (Phase 12) in both Bangla and English
- [ ] Emotion/movement synced correctly with speech (Phase 13)
- [ ] Memory/context correctly recalls facts across sessions (Phase 14)
- [ ] Story/educational/quiz modes function correctly (Phase 15)
- [ ] Offline mode gracefully degrades and recovers (Phase 16)

**Safety & Privacy**
- [ ] Adversarial child-safety test set passes at 100% (Phase 10/21)
- [ ] No raw unsafe content or PII persisted in general logs (Section 28/30)
- [ ] Parent can view/delete memory and conversation history (Phase 17)
- [ ] Data retention policy actively enforced, not just documented (Section 30)
- [ ] SafetyEvent access properly restricted and audit-logged (Section 30/25)

**Security**
- [ ] No secrets in firmware, repo, or logs (verified, not assumed)
- [ ] Device auth, MQTT ACLs, and API auth all pass boundary/negative tests (Phase 19)
- [ ] Firmware signing + OTA rollback tested and working (Phase 18)
- [ ] Rate limiting active on conversation and auth endpoints

**Reliability**
- [ ] Every row of the failure matrix (Section 29) verified via fault injection (Phase 21)
- [ ] Backup restore drill succeeded (Phase 22)
- [ ] Monitoring/alerting live and correctly redacting sensitive data (Phase 20)

**Dashboards**
- [ ] Parent dashboard covers all Section 23 features
- [ ] Admin dashboard covers all Section 24 features
- [ ] Access control correctly scoped (parent-to-own-data-only, admin role-gated)

**Deployment**
- [ ] Production environment stood up per Section 33
- [ ] TLS live and auto-renewing
- [ ] First production device registered and functioning end-to-end

**Sign-off**
- [ ] Electronics Engineer confirms hardware behavior matches spec
- [ ] AI Developer confirms AI pipeline behavior matches spec
- [ ] Software Developer confirms integration/backend behavior matches spec
- [ ] All three agree the Section 40 checklist is complete

---

## Appendix A — Project Documentation Set

Per the brief's request, this defines what each `docs/` document should *contain* (not the documents themselves):

- **`docs/architecture/`** — This master plan and any amendments; the authoritative source for "why" decisions were made, including the resolved Section 3 recommendations.
- **`docs/hardware-interface/`** — Pin maps, sample rates, servo ranges, sensor list — the living version of Section 6/Phase 1's output, updated whenever the board changes.
- **`docs/firmware/`** — Per-module documentation matching Section 7's table (responsibility, interfaces, state, error handling) kept in sync with actual code, plus the state machine diagram (Section 8).
- **`docs/backend/`** — Django app structure, service-layer function reference, how `mqtt_bridge/` maps to REST equivalents (Section 12.2).
- **`docs/api/`** — Full endpoint reference: method, auth, request/response schema, permissions, per Section "API Architecture" — kept current as the single source of truth for anyone integrating against the backend (including a future mobile app).
- **`docs/mqtt/`** — Topic hierarchy, QoS/retention rules, ACL model (Section 11) — the binding reference for both firmware and backend MQTT code.
- **`docs/ai/`** — AI service pipeline stages, the AI Response Contract, safety taxonomy/categories (Section 17.1), persona/prompt versioning notes (without exposing the actual system prompt publicly if that's a product decision).
- **`docs/database/`** — Schema reference (Section 13), migration history notes, retention/purge job documentation.
- **`docs/security/`** — Auth flows (Section 25/26), secret management locations, credential rotation procedure, incident-response notes.
- **`docs/deployment/`** — Docker Compose topology (Section 4.2), environment setup (Section 32), deployment/rollback procedure (Section 33), backup/restore procedure.
- **`docs/testing/`** — Test plans and results per Section 31's levels, including the child-safety adversarial fixture set and its pass/fail history over time.
- **`docs/troubleshooting/`** — Common failure symptoms mapped to Section 29's failure matrix, with resolution steps for support/on-call use.
- **`docs/roadmap/`** — Section 39's future expansion list, kept updated as priorities evolve post-launch.

---

*End of master plan. This document should be treated as version-controlled (Phase 0 output) and updated whenever a phase's implementation genuinely requires a contract change — never silently diverged from.*

---

## Decision Records (appended 2026-09-29 — original document above preserved unchanged)

### 2026-09-29 — Database: PostgreSQL → MongoDB (authorized)

- **Decision:** For Part 2 implementation (starting with Phase 5, Django Foundation), the database is **MongoDB 7.0** via `django-mongodb-backend` 5.2.4, replacing every PostgreSQL reference in this document (Sections 4.2, 13, 32, 33, 34, Appendix A and any others). Owner-approved before implementation.
- **Scope:** All other architecture, contracts, security rules, phase content, and test requirements in this document are unchanged. PostgreSQL references remain as historical text, superseded by this record for database selection only. Mosquitto/MQTT, Django, DRF, Django Templates, and the AI-service contracts are unaffected.
- **Consequences:** The Django ORM runs on the MongoDB backend; primary keys are MongoDB ObjectIds serialized as JSON strings (never integers); no integer counters anywhere; multi-document atomicity uses `django_mongodb_backend.transaction.atomic()` on a replica set (single-node `rs0` in dev/Compose); `auth`/`contenttypes` migrations are generated under `backend/mongo_migrations/`; tests run against a real MongoDB test database.

---

### 2026-10-13 — Phase 12 audio upload: contract, bounds, privacy (authorized; documentation only — implementation requires separate approval)

- **Decision (owner-approved design for Phase 12's device audio endpoint):**
  1. **Sidecar** travels in the `Conversation-Sidecar` HTTP header as JSON with `conversation_id`, `language_hint`, `child_profile_id` — `mode` is deliberately omitted (feature flows stay text-turn-only until Phase 15).
  2. **Endpoint & callers:** a new `POST /api/v1/conversations/turn/audio/` (plus the no-trailing-slash firmware alias), `multipart/form-data` with a single `audio` file part, **device Bearer JWT only** (session callers keep using the JSON turn endpoint; no session/CSRF path ever parses an audio body). The MVP is **synchronous**: one request answers with the same frozen 8-field Output Contract within `AI_SERVICE_TIMEOUT_SECONDS` (30 s); no polling or follow-up download URL (deferred). **Validation order:** the header-only pre-parse size gate may answer `400` for a missing, malformed, or oversized `Content-Length` **before authentication** (a transport-size rejection that reveals nothing about credentials — not every unauthenticated audio request receives `401`); otherwise the intended authenticated flow is authentication `401` → throttle `429` → request validation `400` → ownership `404` → idempotency conflict/replay → processing.
  3. **Bounds:** maximum accepted audio is **1 MiB (1,048,576 bytes)** enforced backend-side (`AUDIO_UPLOAD_MAX_FILE_BYTES`, serializer-authoritative); firmware additionally enforces a **15 s maximum duration** at capture (duration is not verified server-side in the MVP). Transport-level Content-Length gate `AUDIO_UPLOAD_MAX_BODY_BYTES = 1,048,576 + 16,384 = 1,064,960` (multipart envelope allowance) is a **pre-parse, middleware/ASGI-level** check that runs **before authentication** and before any body read — it is separate from view-level validation (sidecar/file/id checks), which runs after authentication and throttle; with `FILE_UPLOAD_MAX_MEMORY_SIZE` pinned to that body bound every gate-passing body stays in memory. Oversized/malformed uploads are **HTTP 400** (not 413 — approved explicitly).
  4. **Privacy:** within the application, accepted audio is held **in memory only** and raw child audio is **never persisted to the database or logs, and never written to disk by application-level handling** — forwarded to the AI service exactly once (extending the existing Phase 11 rule), with a request-scoped bounded in-memory upload handler re-enforcing the byte cap even if settings drift. **End-to-end disk-free handling is not yet verified**: it may only be claimed after the real deployment's proxy/server request-body buffering is inspected and configured (see Consequences) — the current repo deploys `runserver` directly behind no proxy, so no such verification exists today.
  5. **Idempotency:** `Idempotency-Key` header, keyed per robot, retention TTL **600 seconds (10 minutes)**. A duplicate with an identical payload within the TTL replays the stored response instead of reprocessing, and that replay response carries **`Idempotency-Replayed: true`**; the same robot/key reused with a different payload is **`409 Conflict`**. A replay **shares the caller's normal `CONVERSATION_TURN_RATE_LIMIT` bucket**; the store is process-local (locmem) — single-process exact, multi-worker requires a shared store (documented limitation, same class as the command cooldown and turn throttle). Per the validation order above, the check runs after authentication, throttle, request validation, and ownership — replay (`200`) or conflict (`409`) precedes processing only.
  6. **Output-contract bound:** the §15.3 `audio` field is capped at **1,400,000 base64 characters**, enforced in both `backend/conversations/ai_contract.py` and `ai_service/contract/schemas.py` (mirrors the Phase A input bound).
  7. **Process:** this record is the ADR review the owner required **before** implementation — backend/firmware code lands only after explicit approval of this record; verified Django/DRF behavior vs proposed application behavior is distinguished in `docs/backend/architecture.md`'s Phase 12 addendum.
- **Scope:** amends Section 10 (idempotency + payload/sidecar lines) and Section 15.3 (`audio` line) above; companion references updated in `docs/api/endpoints.md` and `docs/backend/architecture.md`. No code, no firmware, no endpoint exists yet at the time of this record.
- **Consequences:** the "header or preceding request" ambiguity in Section 10 is resolved to a header; raw-binary audio upload is not used for Phase 12; unauthenticated or throttled requests never parse an audio body — with the one documented exception that the header-only pre-parse gate may answer `400` (missing/malformed/oversized `Content-Length`) **before authentication**, which never reveals anything about credentials; deployment follow-up: a reverse proxy must (a) accept bodies of at least `AUDIO_UPLOAD_MAX_BODY_BYTES` (e.g. nginx `client_max_body_size 2m`) so the app's `400` contract is what clients see for app-detectable oversize, **and** (b) be configured for **request-body buffering** so the proxy itself does not spool raw audio to disk — nginx buffers request bodies larger than `client_body_buffer_size` (default ~8/16 KiB) to its temp files, so that must be set to at least `AUDIO_UPLOAD_MAX_BODY_BYTES` or its `client_body_temp_path` placed on tmpfs; end-to-end disk-free handling may not be claimed until that configuration is inspected in the real deployment (the current repo deploys `runserver` directly behind no proxy — noted as a deployment-phase concern). Accepted input stays 1 MiB and oversized stays `400`. Fixed inputs unchanged from Phase A: text 4000, input base64 1,400,000, `DATA_UPLOAD_MAX_MEMORY_SIZE` 5 MiB, rate `30/min`.
