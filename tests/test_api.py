import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def a(api):
    r = api.post("/api/junctions", {"id": "A", "name": "Junction A"}, format="json")
    assert r.status_code == 201
    return api


def evt(i, d="NORTH", t="VEHICLE_ARRIVED", v="VH-1", vt="TRUCK", seq=None, j="A"):
    body = {"event_id": f"evt-{i}", "junction_id": j, "direction": d, "event_type": t,
            "vehicle_id": v, "sequence_no": seq or i, "timestamp": "2026-10-08T10:00:00Z"}
    if t == "VEHICLE_ARRIVED":
        body["vehicle_type"] = vt
    return body


def status(api):
    return api.get("/api/junctions/A/status").json()


def test_create_duplicate_junction_conflict(a):
    assert a.post("/api/junctions", {"id": "A"}, format="json").status_code == 409


def test_unknown_junction_404(api):
    assert api.get("/api/junctions/ZZ").status_code == 404
    assert api.post("/api/sensor-events", evt(1, j="ZZ"), format="json").status_code == 404


def test_sensor_event_idempotent(a):
    assert a.post("/api/sensor-events", evt(1), format="json").status_code == 201
    r = a.post("/api/sensor-events", evt(1), format="json")
    assert r.status_code == 200 and r.json()["status"] == "DUPLICATE"
    assert status(a)["queues"]["NORTH"] == 1


def test_malformed_sensor_events_400(a):
    assert a.post("/api/sensor-events", evt(1, d="UP"), format="json").status_code == 400
    assert a.post("/api/sensor-events", evt(2, vt="ROCKET"), format="json").status_code == 400
    assert a.post("/api/sensor-events", {"event_id": "x"}, format="json").status_code == 400


def test_cleared_without_arrival_409(a):
    r = a.post("/api/sensor-events", evt(2, t="VEHICLE_CLEARED"), format="json")
    assert r.status_code == 409
    assert status(a)["queues"]["NORTH"] == 0


def test_arrive_then_clear(a):
    a.post("/api/sensor-events", evt(1, seq=1), format="json")
    r = a.post("/api/sensor-events", evt(2, t="VEHICLE_CLEARED", seq=2), format="json")
    assert r.status_code == 201 and status(a)["queues"]["NORTH"] == 0


def test_manual_and_return(a):
    bad = a.post("/api/junctions/A/commands", {"command": "MANUAL_GREEN_REQUEST"}, format="json")
    assert bad.status_code == 400
    ok = a.post("/api/junctions/A/commands", {"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"}, format="json")
    assert ok.status_code == 200 and status(a)["mode"] == "MANUAL"
    a.post("/api/junctions/A/commands", {"command": "RETURN_TO_AUTOMATIC"}, format="json")
    assert status(a)["mode"] == "AUTOMATIC"


def test_emergency_blocks_manual(a):
    a.post("/api/sensor-events", evt(1, "EAST", v="AMB-1", vt="EMERGENCY"), format="json")
    assert status(a)["mode"] == "EMERGENCY"
    r = a.post("/api/junctions/A/commands", {"command": "MANUAL_GREEN_REQUEST", "direction": "WEST"}, format="json")
    assert r.status_code == 409


def test_ack_flow(a):
    cmd = status(a)["pending_commands"][0]
    ack = {"junction_id": "A", "command_id": cmd["command_id"], "status": "ACK",
           "actual_state": cmd["requested_state"]}
    assert a.post("/api/controller-events", ack, format="json").json()["status"] == "ACCEPTED"
    assert a.post("/api/controller-events", ack, format="json").json()["status"] == "DUPLICATE"
    bad = dict(ack, command_id="cmd-9999")
    assert a.post("/api/controller-events", bad, format="json").status_code == 409


def test_controller_offline_degrades(a):
    r = a.post("/api/controller-events", {"junction_id": "A", "device_type": "SIGNAL_CONTROLLER",
                                          "direction": "SOUTH", "status": "OFFLINE"}, format="json")
    assert r.status_code == 200
    s = status(a)
    assert s["mode"] == "DEGRADED" and s["controller_status"] == "OFFLINE"


def test_history(a):
    a.post("/api/sensor-events", evt(1), format="json")
    types = [h["event_type"] for h in a.get("/api/junctions/A/history").json()]
    assert "VEHICLE_DETECTED" in types
