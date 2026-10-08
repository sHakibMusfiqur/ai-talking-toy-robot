# ESP32 Firmware — MQTT Integration

Source of truth for the Part 1 firmware that connects one physical ESP32
robot to Mosquitto and the Django backend. The backend side of every
contract below is implemented and tested in `backend/mqtt_bridge/`;
the firmware side is pinned by
`backend/mqtt_bridge/tests/test_firmware_contract.py`.

**Browser boundary:** the web dashboard never connects to MQTT or to the
ESP32. It only talks REST to Django (`window.RobotAPI`), Django publishes
commands through the bridge, and the robot receives them from Mosquitto.

## 1. Architecture

```
ESP32 (firmware/)                  Mosquitto              Django backend            Browser
┌────────────────────┐   MQTT   ┌──────────────┐  MQTT   ┌──────────────────┐  REST  ┌────────────┐
│ identity (NVS)     │─────────▶│ robots/{id}/ │◀────────│ mqtt_bridge      │◀───────│ controller │
│ wifi (STA)         │  QoS 0/1 │ status       │  QoS 1  │  subscriber      │        │ page       │
│ backend_client     │─────────▶│ heartbeat    │────────▶│  publisher       │────────▶│ RobotAPI   │
│  register/challenge│  HTTPS   │ event        │         │ devices/views    │  CSRF   │            │
│  token exchange    │◀─────────│ robots/{id}/ │◀────────│  command endpoint│  session│            │
│ robot_mqtt         │  REST    │ command      │  QoS 1  └──────────────────┘         └────────────┘
│ telemetry/tasks    │◀─────────│ */notify     │
│ actions/semantics  │  QoS 1   └──────────────┘
└────────────────────┘
        ▲ TLS (root CA / certificate bundle) — on by default, no bypass path
```

Command flow, one round trip:

```
Button click → POST /api/v1/devices/{id}/command/ {"action":"WAVE"}
            → validate semantic action (frozen set)
            → publish robots/{device}/command  (QoS 1, not retained)
            → ESP32 whitelist match (case-insensitive)
            → log "Received semantic action: WAVE"
            → event command.received → move program (clamped joints) → event command.executed
            → GET /api/v1/devices/{id}/events/ shows the acks
```

## 2. Repository layout

```
firmware/
├── CMakeLists.txt          ESP-IDF project (v5.1+)
├── .gitignore              build/, sdkconfig*, managed_components/
├── sdkconfig.defaults      tracked defaults — NO secrets
└── main/
    ├── Kconfig.projbuild   menuconfig options (Wi-Fi, backend, broker, TLS, intervals)
    ├── app_main.c          connection state machine (bounded backoff)
    ├── identity.c/.h       NVS-persisted UUIDv4 + ECDSA P-256 keypair, challenge signing
    ├── wifi.c/.h           station mode, exponential reconnect (1s → 30s cap), RSSI
    ├── backend_client.c/.h register / challenge / token over HTTPS
    ├── robot_mqtt.c/.h     broker session, LWT, exact subscriptions, publish helpers
    ├── telemetry.c/.h      heartbeat + status tasks, lifecycle events, status state
    └── actions.c/.h        frozen semantic whitelist, joint limits, move programs
```

## 3. Build & flash

Requires the ESP-IDF `idf.py` toolchain on PATH (v5.1+):

```
cd firmware
idf.py set-target esp32
idf.py menuconfig          # "RoboPanel Robot" menu — see section 4
idf.py build flash monitor
```

Without the toolchain the build test in
`backend/mqtt_bridge/tests/test_firmware_contract.py` reports
**skipped — firmware build NOT TESTED** (explicit, never silently green).

## 4. Configuration

All options live under **menuconfig → RoboPanel Robot**. Tracked defaults
(`sdkconfig.defaults`) contain no secrets; credentials go into the
untracked local `sdkconfig` (or `sdkconfig.local`), never into git.

| Option | Default | Notes |
|---|---|---|
| `ROBOT_WIFI_SSID` / `ROBOT_WIFI_PASSWORD` | empty | set locally before flashing |
| `ROBOT_BACKEND_HOST` / `ROBOT_BACKEND_HTTP_PORT` | `192.168.1.100` / `8000` | Django address on your LAN |
| `ROBOT_BACKEND_USE_TLS` | **y** | HTTPS required outside the lab |
| `ROBOT_BACKEND_CA_PEM` | empty | pinned root CA (single-line PEM); empty → certificate bundle |
| `ROBOT_MQTT_HOST` / `ROBOT_MQTT_PORT` | `192.168.1.100` / `1883` | Mosquitto address |
| `ROBOT_MQTT_USE_TLS` | **y** | TLS required outside the lab |
| `ROBOT_MQTT_CA_PEM` | empty | pinned broker CA; empty → certificate bundle |
| `ROBOT_MQTT_KEEPALIVE_S` | 30 | |
| `ROBOT_HEARTBEAT_INTERVAL_S` | 30 | 5–300 |
| `ROBOT_STATUS_INTERVAL_S` | 60 | 10–3600 |

Security invariants enforced in code and by tests:

- TLS verification always has a trust source (pinned CA or certificate
  bundle). There is no configuration that disables certificate
  validation; the forbidden-pattern scan fails the build test if one is
  ever introduced.
- Local lab only: flipping `*_USE_TLS` off is for the compose broker on
  your own network; the firmware logs `TLS disabled (lab mode only)`.
- The device never logs credentials (log copy avoids credential words
  entirely — scanned statement-by-statement by test).
- The pairing code is printed exactly once on the serial console at
  registration (single use, 900 s TTL).

## 5. First-boot provisioning

1. **Identity** — NVS stores a UUIDv4 `device_id` and the ECDSA P-256
   private key (generated on first boot, never regenerated afterwards;
   an inconsistent NVS state halts instead of silently re-keying).
2. **Register** — `POST /api/v1/devices/register/` with `device_id`,
   public key PEM and firmware version. The response's single-use
   **pairing code is printed on the serial console**.
3. **Pair** — a parent enters the code in the dashboard
   (`POST /api/v1/devices/pair/`); ownership is established server-side.
4. **Authenticate** — `POST /devices/auth/challenge/` → sign the
   challenge (ECDSA-SHA256 over the raw challenge, DER or raw r‖s,
   base64) → `POST /devices/auth/token/` → device JWT.
5. **Connect** — MQTT username = `device_id`, password = the JWT. The
   token exchange already provisioned that client in Mosquitto dynsec
   (`ensure_device_client`), so no manual broker setup is needed.
6. **Refresh** — the JWT is refreshed well before expiry while
   connected; the next reconnect always uses the current token.

## 6. MQTT contract (frozen)

Topics are always `robots/{device_id}/…`; the device only ever
subscribes to its **own** four topics (wildcards are rejected by the
broker ACL *and* scanned for in the firmware source).

| Topic | Direction | QoS | Retained | Payload |
|---|---|---|---|---|
| `robots/{id}/status` | device → backend | 1 | yes | partial `{"online":bool,"state":str≤32,"battery_percent":0-100,"rssi":int}` — omitted fields keep their previous value; identity comes from the topic only |
| `robots/{id}/heartbeat` | device → backend | 0 | no | exactly `{}` — any field is rejected |
| `robots/{id}/event` | device → backend | 1 | no | `{"event_type":"^[A-Za-z0-9_.\\-]{1,64}$","payload":{…}}` |
| `robots/{id}/command` | backend → device | 1 | no | `{"action":"…"}` — semantic names only |
| `robots/{id}/{config,ota,content}/notify` | backend → device | 1 | yes | `{"version":int}` |

Last-will (set by the firmware at connect): retained QoS 1
`{"online":false}` on the status topic — an unexpected drop marks the
robot offline immediately.

Firmware event types: `firmware.started`, `mqtt.connected`,
`mqtt.disconnected` (buffered across an outage and delivered after the
next connect), `command.received`, `command.executed`,
`command.rejected` (payload carries `action` + `reason`:
`invalid_payload` / `unknown_action`).

## 7. Semantic actions (frozen)

`IDLE`, `WAVE`, `DANCE`, `NOD`, `TILT_HEAD`, `POINT`, `LISTENING_RETRY`

Matching is case-insensitive; unknown actions are rejected with
`command.rejected` and never move hardware. Raw servo/PWM/GPIO/motor
values are not part of any contract — firmware translates each semantic
name into a symbolic move program whose every target is clamped through
the joint-limit table (`kJointLimits` in `actions.c`). The action set is
identical to `backend/core/semantic_actions.py`; the contract test fails
if the two ever diverge.

## 8. Connection state machine

`WIFI_DISCONNECTED → WIFI_CONNECTED → MQTT_DISCONNECTED → MQTT_CONNECTED`

- Wi-Fi reconnect: exponential 1 s → 30 s cap (timer-driven, never
  blocks the event loop).
- MQTT: manual reconnect with the same bounded backoff; on every
  connect the device re-subscribes (4 topics) and republishes its
  retained status before any queued events.
- Startup failures (register/token/connect) retry with bounded backoff;
  only an irrecoverable identity/NVS failure halts with a clear log.
- Status and heartbeat run in their own tasks and silently skip
  publishing while disconnected (no unbounded queue growth).

## 9. Local development against docker compose

1. Start the stack: `docker compose --env-file .env -f
   infra/docker-compose.yml up -d mongo mosquitto django`
2. In `idf.py menuconfig`, set the LAN IP of the machine running
   compose as `ROBOT_BACKEND_HOST` and `ROBOT_MQTT_HOST`.
3. The compose Mosquitto listener is plaintext: set
   `ROBOT_MQTT_USE_TLS=n` (and `ROBOT_BACKEND_USE_TLS=n` for the dev
   server) **in your untracked local `sdkconfig`** — lab only.
4. Flash, watch the serial monitor, copy the pairing code, pair the
   robot in the dashboard, then use the controller page. The page runs
   in real mode (`data-service-mode="real"`): state/events/sensors come
   from the REST APIs and commands go through
   `POST /api/v1/devices/{id}/command/` — success shows “sent”, failure
   shows “Command could not be sent”, and the buttons disable while a
   command is in flight. (`data-service-mode="mock"` keeps the
   self-contained preview used before hardware existed.)

## 10. Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `Wi-Fi SSID not configured, halted` | menuconfig not set | `idf.py menuconfig` → RoboPanel Robot |
| `registration retry in …` | backend address/TLS wrong | ping the host; lab TLS flags; Django logs |
| `HTTP 409` on boot | device already registered (normal) | proceeds automatically |
| `challenge/token exchange failed (HTTP 401)` | clock/signature issue | serial log; `POST /devices/auth/challenge/` manually |
| `connection refused` on MQTT | broker down or dynsec not provisioned | `docker compose ps mosquitto`; token exchange must succeed first |
| `not authorised` on MQTT | wrong/rotated JWT password | complete a fresh token exchange (step 5) |
| Commands never arrive | device not subscribed / wrong id | serial log after connect; `mosquitto_sub -t 'robots/+/command'` |
| Robot shows offline in UI | LWT fired or heartbeats stopped | serial log; status topic retention |

## 11. Verification status

| Item | Status |
|---|---|
| Backend MQTT contract, ACL, LWT, ingestion, publisher, provisioning (Phase 8 suite) | **PASS** — automated |
| Full path register→pair→exchange→JWT session→ingest→command→ack (`test_acceptance_path.py`, live broker) | **PASS** — automated |
| Firmware source contract: actions, topics, payloads, QoS/retain, TLS defaults & no-bypass, no credential logs, no tracked secrets, joint limits (`test_firmware_contract.py`) | **PASS** — automated |
| Controller real-mode switch, REST-only browser, failure copy, in-flight guard (`test_real_integration.py`) | **PASS** — automated |
| `idf.py build` of `firmware/` | **NOT TESTED** — ESP-IDF toolchain not available in this environment (test auto-skips) |
| Physical ESP32: flash, Wi-Fi join, live broker session, real servo motion | **NOT TESTED** — no hardware in this environment |
