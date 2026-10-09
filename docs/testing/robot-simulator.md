# Robot Simulator (software-in-the-loop)

Development-only Python robot that speaks the **real** device contracts on
the wire - registration/challenge-token auth, the Section 11 MQTT
topics/QoS/retain/LWT, and the frozen semantic-action whitelist - so the
full `Web Controller -> Django -> MQTT -> device -> events` path can be
exercised without an ESP32.

- **It is not firmware** and validates no hardware (no `idf.py`, no
  servos). Real-device validation stays with the ESP32 workflow.
- It never talks to an AI provider and **fabricates no telemetry**:
  status payloads carry only `online`/`state`, so `rssi`/`battery_percent`
  stay null ("Not available" in the dashboard).
- Every command/lifecycle event payload it emits carries
  `"simulated": true`, so simulated activity can never be mistaken for
  real hardware.

## Architecture

```
robot_sim (python -m robot_sim, from backend/)
  |-- REST:  POST /api/v1/devices/register/
  |          POST /api/v1/devices/auth/challenge/  -> ECDSA-SHA256 sign
  |          POST /api/v1/devices/auth/token/      -> device JWT
  |          (JWT = MQTT password; username = device_id)
  |-- MQTT:  publishes robots/<id>/{status,heartbeat,event}
  |          subscribes robots/<id>/{command,config/notify,ota/notify,content/notify}
  |          LWT + graceful stop: retained {"online":false} on status
  '-- state: gitignored sim_state/identity.json (locally generated
             EC P-256 key + UUID device id; delete the file to rotate)
```

The command-handling logic mirrors `firmware/main/actions.c`
(case-insensitive match against the frozen set, canonical uppercase in
acks, `invalid_payload` / `unknown_action` reject reasons) and reuses the
ONE existing definitions: `core.semantic_actions.SEMANTIC_ACTIONS` and
`mqtt_bridge.topics`.

## Prerequisites

- Repo venv at `.venv/` with backend requirements installed.
- Live services: `docker compose --env-file .env -f infra/docker-compose.yml up -d mongo mosquitto`.

## Quick start (host venv, three terminals)

Settings only auto-load `MQTT_*` from the repo-root `.env` under pytest,
so **host** Django processes need the credentials exported first. Run
this once per terminal (it does not print any values):

```powershell
Get-Content .env | Where-Object { $_ -match '^(MQTT_[A-Z_]+)=(.*)$' } |
  ForEach-Object { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
```

Terminal A - backend:

```powershell
.\.venv\Scripts\python.exe backend\manage.py runserver
```

Terminal B - MQTT bridge (ingestion):

```powershell
# same env preload as above, then
.\.venv\Scripts\python.exe backend\manage.py run_mqtt_bridge
```

Terminal C - simulator:

```powershell
# same env preload as above, then
cd backend
..\.venv\Scripts\python.exe -m robot_sim
```

On first run the simulator prints the device id and the **single-use
pairing code (shown once)** and then `simulator running ...`. Later runs
reuse `sim_state/` and stay silent (register returns 409 - already
registered - which is normal).

## Pairing (once per identity)

There is no pairing form in the web UI yet; pair through the API/ORM.
With a parent account present (create one via
`accounts.services.create_parent_account` if needed):

```python
# manage.py shell
from accounts.models import Parent
from devices.services import pair_robot

parent = Parent.objects.get(user__email="you@example.com")
robot = pair_robot(pairing_code="XXXXXXXX", parent=parent, nickname="Sim")
```

Then open `/dashboard/` - the robot card appears and the controller page
works against the live simulator.

## Using it

- Press the semantic-action buttons on the controller page. The simulator
  logs `command received: action=WAVE (simulated)` /
  `command executed: ...` (after a short simulated execution delay) and
  the robot's event log shows `command.received` / `command.executed`
  with `"simulated": true`.
- The state column mirrors firmware: `BOOT` -> `<ACTION>` -> `IDLE`.
- Ctrl+C stops gracefully (immediate retained offline). A hard kill
  triggers the LWT; the dashboard goes offline within a few seconds.
- Hostile/malformed payloads published onto the command topic come back
  as `command.rejected` (`invalid_payload` with empty action, or
  `unknown_action` with the raw value) - they are never executed.

## Configuration

All optional; CLI flags override the `ROBOT_SIM_*` environment.

| Flag | Env | Default |
| --- | --- | --- |
| `--backend-url` | `ROBOT_SIM_BACKEND_URL` | `http://127.0.0.1:8000` |
| `--mqtt-host` | `ROBOT_SIM_MQTT_HOST` | `127.0.0.1` |
| `--mqtt-port` | `ROBOT_SIM_MQTT_PORT` | `1883` |
| `--state-dir` | `ROBOT_SIM_STATE_DIR` | `sim_state` (gitignored) |
| `--heartbeat-seconds` | `ROBOT_SIM_HEARTBEAT_SECONDS` | `30` |
| `--status-seconds` | `ROBOT_SIM_STATUS_SECONDS` | `60` |
| `--exec-ms` | `ROBOT_SIM_EXEC_MS` | `300` |
| `--firmware-version` | `ROBOT_SIM_FIRMWARE_VERSION` | `sim-0.1.0` |

## Automated tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest robot_sim/tests -q
..\.venv\Scripts\python.exe -m pytest -q    # full backend suite
```

The integration test uses the same live Mosquitto broker and production
bridge subscriber as `mqtt_bridge` tests (Mongo + Mosquitto must be up).
It covers: register/pair/auth, exact subscription set with granted QoS,
status + heartbeat ingestion, the owner command loop with
`simulated: true` acks, state transitions, reject paths, graceful-offline
publication, and a no-secrets-in-logs check (pairing code/JWT/private key
never logged).

## Known divergences from firmware

- Python's JSON parser is stricter than cJSON about trailing garbage, so
  the simulator rejects a few malformed payloads the firmware might
  parse by prefix; both stay inside the frozen command contract.
- No radio/battery exists in software - `rssi`/`battery_percent` are
  never emitted (the firmware emits `rssi` when it has real Wi-Fi data).
- Token refresh follows the firmware rule (refresh at
  `expires_in - 300 s`, floored at 60 s); the refresh mechanics are
  exercised through the pure due-calculation tests, not a live expiry.

## Troubleshooting

- **Simulator: `broker rejected MQTT connection (Not authorized)`** and
  runserver log shows `mqtt admin password not configured` (or
  `mqtt provisioning skipped`): the Django process did not have
  `MQTT_ADMIN_PASSWORD`/`MQTT_BRIDGE_PASSWORD` - run the env preload
  step in every terminal. Token exchange is best-effort by design: it
  still returns a JWT, but the broker user was not (re)provisioned.
- **Pairing code lost**: codes are single-use and shown once. Delete
  `sim_state/` and start the simulator again to register a brand-new
  robot identity (then pair the new code), or look the robot up by
  device id and pair via the admin API.
- **`sim_state/identity.json` contains a locally generated private
  key**: it is gitignored, never logged, never transmitted (only
  challenge signatures are). Delete the file to rotate the identity.
- On Windows the state file's POSIX `0600` mode is not enforced; rely on
  the user profile permissions (the file is created per-user).
