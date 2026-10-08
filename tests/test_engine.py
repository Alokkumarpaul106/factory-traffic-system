from traffic.domain.engine import JunctionEngine
from traffic.domain.models import Direction as D, Mode, Phase, Signal, Step


def ev(i, d="NORTH", t="VEHICLE_ARRIVED", v="VH-1", vt="TRUCK", seq=None):
    return {"event_id": f"evt-{i}", "junction_id": "A", "direction": d, "event_type": t,
            "vehicle_id": v, "vehicle_type": vt, "sequence_no": seq or i,
            "timestamp": "2026-10-08T10:00:00Z"}


def run(e, t0, t1, ack=True, dt=0.5):
    """Fast-forward fake time; simulated controller ACKs every command (no sleep)."""
    trace, t = [], t0
    while t <= t1:
        e.tick(t)
        for c in e.drain_commands():
            if ack:
                e.on_ack(c.command_id, "ACK", c.requested_state.value, t)
        greens = {d for d, s in e.desired().items() if s == Signal.GREEN}
        assert len({Phase.NORTH_SOUTH if d in (D.NORTH, D.SOUTH) else Phase.EAST_WEST for d in greens}) <= 1
        trace.append((t, greens, e.step))
        t += dt
    return trace


def started():
    e = JunctionEngine("A", now=0)
    run(e, 0, 20)
    assert e.step == Step.GREEN and e.phase == Phase.NORTH_SOUTH
    return e


def test_duplicate_event_changes_queue_once():
    e = JunctionEngine("A", now=0)
    assert e.handle_sensor_event(ev(1), 1).status == "ACCEPTED"
    assert e.handle_sensor_event(ev(1), 2).status == "DUPLICATE"
    assert len(e.queues[D.NORTH]) == 1


def test_cleared_without_arrival_never_negative():
    e = JunctionEngine("A", now=0)
    assert e.handle_sensor_event(ev(2, t="VEHICLE_CLEARED", seq=2), 1).status == "REJECTED"
    assert len(e.queues[D.NORTH]) == 0


def test_out_of_order_cleared_before_arrived():
    e = JunctionEngine("A", now=0)
    e.handle_sensor_event(ev(2, t="VEHICLE_CLEARED", seq=1502), 1)
    assert e.handle_sensor_event(ev(1, seq=1501), 2).status == "STALE"
    assert len(e.queues[D.NORTH]) == 0


def test_malformed_and_unknown_type_rejected():
    e = JunctionEngine("A", now=0)
    assert e.handle_sensor_event({"event_id": "x"}, 1).status == "REJECTED"
    assert e.handle_sensor_event(ev(3, vt="ROCKET"), 1).status == "REJECTED"


def test_emergency_preemption_goes_through_yellow_and_all_red():
    e = started()
    e.handle_sensor_event(ev(5, "EAST", v="AMB-1", vt="EMERGENCY"), 20)
    assert e.mode == Mode.EMERGENCY
    trace = run(e, 20, 40)
    first_east = next(t for t, g, _ in trace if D.EAST in g)
    assert first_east >= 20 + 5 + 2            # yellow + all-red first
    assert any(s == Step.YELLOW for _, _, s in trace)
    assert e.phase == Phase.EAST_WEST and e.step == Step.GREEN


def test_manual_then_return_to_automatic():
    e = started()
    assert e.command("MANUAL_GREEN_REQUEST", "WEST", 20).status == "ACCEPTED"
    run(e, 20, 40)
    assert e.mode == Mode.MANUAL and e.phase == Phase.EAST_WEST and e.step == Step.GREEN
    e.command("RETURN_TO_AUTOMATIC", None, 40)
    assert e.mode == Mode.AUTOMATIC


def test_manual_rejected_during_emergency():
    e = started()
    e.handle_sensor_event(ev(5, "EAST", v="AMB-1", vt="EMERGENCY"), 20)
    assert e.command("MANUAL_GREEN_REQUEST", "WEST", 21).reason == "EMERGENCY_ACTIVE"


def test_manual_expires():
    e = started()
    e.command("MANUAL_GREEN_REQUEST", "WEST", 20)
    run(e, 20, 20 + e.cfg.manual_ttl_s + 1)
    assert e.mode == Mode.AUTOMATIC


def test_no_ack_leads_to_degraded_all_red():
    e = started()
    e.command("MANUAL_GREEN_REQUEST", "WEST", 20)
    run(e, 20, 45, ack=False)
    assert e.mode == Mode.DEGRADED
    assert Signal.GREEN not in e.desired().values()
    assert any(r["event_type"] == "CONTROLLER_TIMEOUT" for r in e.drain_audit())


def test_duplicate_and_unknown_ack():
    e = JunctionEngine("A", now=0)
    c = e.drain_commands()[0]
    assert e.on_ack(c.command_id, "ACK", "RED", 1).status == "ACCEPTED"
    assert e.on_ack(c.command_id, "ACK", "RED", 2).status == "DUPLICATE"
    assert e.on_ack("cmd-999", "ACK", "RED", 2).reason == "UNKNOWN_COMMAND"


def test_signal_controller_offline_degrades_and_recovers():
    e = started()
    e.on_device_status("SIGNAL_CONTROLLER", "SOUTH", "OFFLINE", 20)
    assert e.mode == Mode.DEGRADED
    assert e.command("RETURN_TO_AUTOMATIC", None, 21).reason == "CONTROLLER_OFFLINE"
    e.on_device_status("SIGNAL_CONTROLLER", "SOUTH", "ONLINE", 22)
    assert e.command("RETURN_TO_AUTOMATIC", None, 23).status == "ACCEPTED"
    run(e, 23, 40)
    assert e.mode == Mode.AUTOMATIC and e.step == Step.GREEN


def test_restart_keeps_queues_but_distrusts_signals():
    e = started()
    e.handle_sensor_event(ev(1, "EAST"), 20)
    e2 = JunctionEngine.restore("A", e.snapshot(), now=100)
    assert len(e2.queues[D.EAST]) == 1
    assert all(s == Signal.UNKNOWN for s in e2.actual.values())
    assert len(e2.drain_commands()) == 4 and e2.step == Step.ALL_RED
