# Factory Traffic Management System (Backend Intern Assessment V2)

Event-driven traffic-signal controller for factory junctions. **Django + DRF + SQLite**, plain HTML/JS dashboard (polling).

## Setup & run (two processes)
```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver            # terminal 1: API + dashboard at http://127.0.0.1:8000/
python manage.py run_ticker           # terminal 2: signal timers + controller simulator
pytest -v                             # tests (domain engine tests need no DB/HTTP)
```
Open the dashboard, click **+ Create junction** (id `A`). Without `run_ticker` nothing advances (timers live there, by design).
Schema: `docs/schema.sql` + `traffic/migrations/`. API: table below + `docs/postman_collection.json`.

## Architecture
```
REST (DRF views/serializers)   <- later: MQTT adapter
        |
Application service (traffic/services.py)   load state -> domain -> save -> send commands
        |                         |
Domain engine (traffic/domain)   ControllerPort -> REST simulator (traffic/infrastructure)
pure Python, time injected       Persistence: Django ORM (SQLite)
```
Decisions:
- **Domain is pure Python** (no HTTP/DB/MQTT imports). Time is passed in (`engine.tick(now)`), so there is no `sleep()` and tests fast-forward time.
- **Consistency:** every operation is load -> apply -> save with **optimistic concurrency** (`UPDATE ... WHERE version=v`, retry on conflict). Commands are sent to the controller only *after* commit. Production: PostgreSQL + `select_for_update`.
- **Timers** are a separate `run_ticker` process (500 ms). Phase deadlines are stored in DB, so timing survives restarts.
- **Desired vs actual:** `desired_signals` come from the engine; `actual_signals` change only on a controller ACK.
- **Polling (1.5 s)** for the dashboard: simplest and fault-tolerant; SSE/WebSocket is the next step.
- Clients send *intents* (`MANUAL_GREEN_REQUEST`, `RETURN_TO_AUTOMATIC`); they can never write a signal state.

## Traffic-control algorithm
Per phase (NORTH+SOUTH, EAST+WEST): `score = sum(queue size + vehicle weight) + 0.5 * oldest waiting seconds`.
Weights: EMERGENCY 100, TRUCK 3, FORKLIFT 2, EMPLOYEE_VEHICLE 1. Rules, in order:
1. Starvation: a waiting vehicle older than 90 s forces its phase next.
2. Nobody waiting on the other phase -> keep GREEN (no pointless switching).
3. Minimum green 10 s; maximum green 30 s (then switch if anyone waits).
4. In between, switch only if other score > 1.2 x current score (hysteresis).
Priority order of "who decides the next phase": **EMERGENCY > MANUAL > AUTOMATIC scheduler**.

## Traffic-state transitions
Every change, whatever the cause, uses one path: `GREEN(p) -> YELLOW(p, 5 s) -> ALL_RED (2 s) -> GREEN(other)`.
GREEN for the new phase is issued only when the other side's signals are **confirmed RED by ACK**. `assert_safe()` raises if desired/requested state ever has conflicting GREENs.
Modes: AUTOMATIC, MANUAL, EMERGENCY, DEGRADED (derived deterministically from state flags).
- ACK timeout 5 s -> retry same `command_id` (max 2 retries) -> give up: actual = UNKNOWN, **DEGRADED** (all RED, no automatic GREEN). Also DEGRADED on NACK, desired/actual mismatch, conflicting actual GREEN, controller OFFLINE.
- Leaving DEGRADED requires the controller ONLINE and an admin `RETURN_TO_AUTOMATIC`.
- **Restart recovery** (`run_ticker` start): queues/emergencies/manual/dedupe state kept; signal state is *not* trusted -> engine restarts in ALL_RED, re-sends every signal with new `command_id`s, waits for ACKs, then resumes. Works for a restart in YELLOW/ALL_RED/GREEN or with a pending ACK.

## API
| Endpoint | Purpose |
|---|---|
| `GET/POST /api/junctions`, `GET /api/junctions/{id}`, `GET .../status` | junction list/create/state |
| `POST /api/sensor-events` | 201 accepted, 200 duplicate/stale, 400 invalid, 404 unknown junction, 409 rejected |
| `POST /api/junctions/{id}/commands` | manual / return to automatic (409 if emergency/degraded) |
| `POST /api/controller-events` | ACK/NACK (`command_id`) or device status (`device_type`) |
| `GET /api/junctions/{id}/history?limit=` | audit log |
| `POST /api/junctions/{id}/simulator` | added: `{"auto_ack": false}` to simulate a silent controller |

## Demo scenarios (dashboard, both processes running, junction `A`)
1. **Normal:** Simulation -> arrive several vehicles on EAST/WEST/NORTH; watch the phase switch after min-green.
2. **Priority:** arrive 1 TRUCK on EAST vs 1 EMPLOYEE_VEHICLE on NORTH while NS is green; EAST is served first.
3. **Emergency:** while NS is GREEN, arrive an EMERGENCY from EAST: banner + YELLOW -> ALL_RED -> EAST/WEST GREEN. Click `x` on it to clear.
4. **Manual:** "Green for WEST" (same safe sequence), banner shows override; then "Return to AUTOMATIC".
5. **Duplicate:** arrive a vehicle, press "Resend last event": queue unchanged, history shows `SENSOR_EVENT_DUPLICATE`.
6. **Clearance:** click `x` next to a queued vehicle; queue decreases (never below 0).
7. **Controller failure:** untick auto-ACK, request manual WEST: pending command retries, `CONTROLLER_TIMEOUT`, then DEGRADED. Or press OFFLINE. Re-tick auto-ACK, ONLINE, Return to AUTOMATIC.
8. **Restart:** stop and restart `run_ticker` (and/or runserver): queues/history remain, signals go ALL_RED then recover (`RECOVERED_AFTER_RESTART`).
9. **Concurrency:** run `pytest tests/test_service.py`; plus fire parallel curl requests; optimistic locking serialises them per junction (`tests/test_engine.py` covers the safety invariant).

## Assumptions / Questions / Requirement Issues
- **Conflicting movements:** only NS vs EW phases; no turning movements/pedestrians (spec simplification).
- **Duplicate detection:** `event_id` is authoritative (stored, idempotent). `sequence_no` orders events **per vehicle**; a lower sequence than already seen for that vehicle is `STALE`. Spec is unclear whether sequence is global/per sensor; per-vehicle is the only one that works without sensor ids.
- **Timestamps:** server receive time drives waiting time, timeouts and TTLs (sensor clocks may drift); sensor timestamp is kept in the audit log only. Delayed events are accepted if newer by sequence, but their waiting time starts at receipt (under-estimates).
- **VEHICLE_CLEARED without arrival:** rejected (409) and remembered, so a late ARRIVED with lower sequence is ignored (out-of-order case). Queue can never go negative.
- **Emergency:** overrides manual (manual is suspended, new manual commands get 409). Competing emergencies: oldest first, others wait. Cleared by VEHICLE_CLEARED or expired after 60 s (stale). Mode returns to MANUAL/AUTOMATIC afterwards.
- **Manual:** expires after 120 s (auto-return); two admins -> last valid command wins, both audited; admin disconnect = TTL expiry.
- **Unsafe/ambiguous in spec:** "immediately begin" emergency preemption still costs 7 s (yellow + all-red) - safety wins. In DEGRADED the engine requests all RED directly (GREEN->RED without yellow) because we can't trust the device; a real controller should flash/force its own safe state (hardware business decision). No authentication on manual control is unsafe for production (bonus, not done).
- **Sensor offline:** only raises an alert (queue data may be stale); does not stop traffic. Business decision needed (fixed-time fallback?).
- **ACK:** timeout 5 s, 2 retries with same `command_id` (idempotent for the device), duplicate ACK ignored, ACK for unknown/superseded command ignored (audited).
- **Technical problems:** SQLite has no row locks (optimistic version check used); dedupe set is stored in the JSON state and grows unbounded (next: separate table with TTL pruning); the ticker is a single point of failure (health-check/alert needed); `controller-events` status events are not deduplicated.
- **API paths:** no trailing slash, exactly as in the PDF (`:id` written as `{id}`).

## AI / Tool Usage
Claude (Anthropic) was used to plan the architecture, generate code/tests/this README and explain design trade-offs. I reviewed the code and can explain/modify it. *(Edit this section so it reflects what you actually did.)*

## Not done / next steps
MQTT adapter (only the `ControllerPort` seam), SSE/WebSocket, Docker, authN/Z for manual control, audit of serializer-rejected events, dedupe-table pruning, metrics, multi-junction UI beyond selector (engine already supports many junctions).
