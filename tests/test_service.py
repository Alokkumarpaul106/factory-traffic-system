import pytest

from traffic.infrastructure.rest_controller_sim import RestSimulatorController
from traffic.services import TrafficService
from traffic.simulator import ack_pending

pytestmark = pytest.mark.django_db


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make():
    clock = Clock()
    return TrafficService(RestSimulatorController(), clock), clock


def drive(svc, clock, seconds, check=True):
    for _ in range(int(seconds / 0.5)):
        clock.t += 0.5
        svc.tick_all()
        ack_pending(svc, delay=0)
        if check:
            g = {d for d, v in svc.status("A")["desired_signals"].items() if v == "GREEN"}
            assert not (g & {"NORTH", "SOUTH"} and g & {"EAST", "WEST"}), "conflicting GREEN!"


def evt(i, d, v, vt, seq):
    return {"event_id": f"e{i}", "junction_id": "A", "direction": d, "event_type": "VEHICLE_ARRIVED",
            "vehicle_id": v, "vehicle_type": vt, "sequence_no": seq, "timestamp": "2026-10-08T10:00:00Z"}


def test_boot_reaches_green_via_acks():
    svc, clock = make()
    svc.create_junction("A")
    drive(svc, clock, 10)
    s = svc.status("A")
    assert s["step"] == "GREEN" and s["actual_signals"]["NORTH"] == "GREEN" and s["controller_status"] == "ONLINE"


def test_emergency_preempts_through_yellow_and_all_red():
    svc, clock = make()
    svc.create_junction("A")
    drive(svc, clock, 10)
    svc.ingest_sensor_event(evt(1, "EAST", "AMB", "EMERGENCY", 1))
    drive(svc, clock, 20)
    s = svc.status("A")
    assert s["phase"] == "EAST_WEST" and s["step"] == "GREEN" and s["mode"] == "EMERGENCY"
    steps = [h["to_step"] for h in reversed(svc.history("A")) if h["event_type"] == "TRANSITION_STARTED"]
    assert "YELLOW" in steps and "ALL_RED" in steps


def test_restart_recovery_distrusts_signals_but_keeps_queues():
    svc, clock = make()
    svc.create_junction("A")
    drive(svc, clock, 10)
    svc.ingest_sensor_event(evt(1, "EAST", "V1", "TRUCK", 1))
    svc.recover_all()                                    # simulated server restart
    s = svc.status("A")
    assert s["step"] == "ALL_RED" and s["queues"]["EAST"] == 1
    assert set(s["actual_signals"].values()) == {"UNKNOWN"}
    drive(svc, clock, 12)
    assert svc.status("A")["step"] == "GREEN"


def test_unanswered_command_ends_in_degraded_all_red():
    svc, clock = make()
    svc.create_junction("A")
    drive(svc, clock, 10)
    svc.set_auto_ack("A", False)
    svc.send_command("A", "MANUAL_GREEN_REQUEST", "WEST")
    drive(svc, clock, 40)
    s = svc.status("A")
    assert s["mode"] == "DEGRADED" and "GREEN" not in s["desired_signals"].values()
    assert any(h["event_type"] == "CONTROLLER_TIMEOUT" for h in svc.history("A"))
