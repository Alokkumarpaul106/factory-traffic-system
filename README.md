# Factory Traffic Management System (TMS)

Event-driven traffic-signal controller for factory junctions (Backend Developer Intern Assessment V2).
**Stack:** Django + DRF, SQLite, plain HTML/JS dashboard (polling). Junction A is created from the dashboard; more junctions use the same engine.

## Run
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver          # terminal 1 -> http://127.0.0.1:8000/
python manage.py run_ticker         # terminal 2 (signal timers + controller simulator)
pytest -v                           # 27 tests
```
Open the dashboard and click **+ Create junction** (id `A`).

## Architecture
```
REST API (DRF)  ->  Application service  ->  Domain engine (pure Python)
                          |                       |
                      SQLite (ORM)       ControllerPort -> REST simulator (MQTT later)
```
- Domain has no HTTP/DB/MQTT imports; time is injected (`engine.tick(now)`), so no `sleep()` anywhere.
- **Consistency:** load state -> apply -> save with optimistic locking (`UPDATE ... WHERE version=v`, retry). Commands are sent only after commit.
- **Desired vs actual:** desired comes from the engine; actual changes only on controller ACK.
- Clients send intents (`MANUAL_GREEN_REQUEST`, `RETURN_TO_AUTOMATIC`), never raw signal states.

## Algorithm
`score(phase) = queue size + vehicle weights + 0.5 x oldest wait (s)`; weights: EMERGENCY 100, TRUCK 3, FORKLIFT 2, EMPLOYEE 1.
Starvation (>90 s) forces a phase; min green 10 s, max green 30 s; switch early only if the other score is 1.2x higher; no switch if nobody waits.
Priority: EMERGENCY > MANUAL > AUTOMATIC.

## State transitions
Always `GREEN -> YELLOW (5 s) -> ALL_RED (2 s) -> GREEN(other)`, for auto, manual and emergency alike.
GREEN is issued only after the other side is **confirmed RED**. No ACK in 5 s -> retry twice -> **DEGRADED** (all RED).
**Restart:** queues/history kept, signal state distrusted: restart in ALL_RED, re-send all commands, wait for ACKs.

## API
| Endpoint | Purpose |
|---|---|
| `GET/POST /api/junctions`, `GET /api/junctions/{id}`, `/status` | junctions + state |
| `POST /api/sensor-events` | vehicle arrived/cleared (idempotent) |
| `POST /api/junctions/{id}/commands` | manual / return to automatic |
| `POST /api/controller-events` | ACK/NACK or device status |
| `GET /api/junctions/{id}/history` | audit log |
| `POST /api/junctions/{id}/simulator` | `{"auto_ack": false}` simulates a silent controller |

Postman: `docs/postman_collection.json`. Schema: `docs/schema.sql`.

## Demo (dashboard, both processes running)
1. **Normal / priority:** arrive vehicles on different directions (TRUCK vs EMPLOYEE_VEHICLE).
2. **Emergency:** arrive EMERGENCY from EAST while NS is green -> YELLOW -> ALL_RED -> EAST/WEST green.
3. **Manual:** "Green for WEST", then "Return to AUTOMATIC".
4. **Duplicate:** "Resend last event" -> queue unchanged.
5. **Clearance:** click `x` on a queued vehicle.
6. **Controller failure:** untick auto-ACK, request manual -> timeout/retry -> DEGRADED; or press OFFLINE.
7. **Restart:** stop/start `run_ticker`; queues and history remain, signals go ALL_RED then recover.

## Assumptions / Questions / Requirement Issues
- **Conflicts:** only NS vs EW phases (no turns/pedestrians).
- **Duplicates/ordering:** `event_id` is authoritative; `sequence_no` orders events per vehicle (older = STALE). CLEARED without arrival is rejected (409) and remembered, so a late ARRIVED is ignored. Queue never goes below 0.
- **Timestamps:** server time for waiting/timeouts; sensor time only in audit.
- **Emergency:** overrides manual; oldest emergency first; cleared by VEHICLE_CLEARED or after 60 s. Preemption still takes about 7 s (yellow + all-red): safety over speed.
- **Manual:** expires after 120 s; last valid command wins; admin disconnect = expiry; rejected (409) during emergency.
- **Failures:** ACK timeout 5 s, 2 retries with the same `command_id`; duplicate/unknown ACKs ignored. NACK, mismatch, controller offline -> DEGRADED, which needs an admin `RETURN_TO_AUTOMATIC`. Sensor offline only raises an alert (business decision needed).
- **Unsafe/open points:** DEGRADED requests all RED directly (a real controller should enforce its own safe state); no auth on manual control; SQLite has no row locks (optimistic locking used; PostgreSQL + `select_for_update` in production); dedupe IDs grow unbounded (needs pruning table); ticker is a single point of failure.

## AI / Tool Usage
Claude (Anthropic) was used for architecture planning and generating code, tests and this README. I reviewed the code and can explain and modify it. *(Edit to match what you actually did.)*

## Not done / next steps
MQTT adapter, SSE/WebSocket, Docker Compose, authentication, audit of serializer-rejected events, dedupe pruning, metrics.
