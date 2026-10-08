"""
JunctionEngine: deterministic, time-injected state machine for ONE junction.
It never calls the network/DB. It only fills `outbox` (commands for the controller)
and `audit` (history); the application layer drains them.
Time is passed in as `now` (seconds), so tests can fast-forward without sleep().

Cycle:  GREEN(p) -> YELLOW(p) -> ALL_RED -> GREEN(other)   (auto, manual, emergency: same path)
"""
from dataclasses import replace
from enum import Enum

from . import scheduler
from .models import (Config, Direction, Mode, Phase, PHASE_DIRS, Result, Signal,
                     SignalCommand, Step, Vehicle, VehicleType, opposite, phase_of)


class SafetyViolation(Exception):
    pass


class JunctionEngine:
    def __init__(self, junction_id, cfg=None, now=0.0):
        self.id = junction_id
        self.cfg = cfg or Config()
        self.queues = {d: [] for d in Direction}
        self.step = Step.ALL_RED            # boot in the safe state
        self.phase = None                   # phase last/currently served
        self.target = None                  # phase to serve after ALL_RED
        self.step_started = now
        self.step_deadline = now + self.cfg.all_red_s
        self.emergencies = {}               # vehicle_id -> (direction, server_time)
        self.manual_phase = None
        self.manual_until = None
        self.degraded = False
        self.failed_devices = set()         # {(device_type, direction)}
        self.actual = {d: Signal.UNKNOWN for d in Direction}     # CONFIRMED by controller
        self.requested = {d: None for d in Direction}            # last command sent
        self.pending = {}                   # command_id -> SignalCommand
        self.acked = set()
        self.seen_events = set()
        self.vehicle_seq = {}               # vehicle_id -> highest sequence_no seen
        self.outbox, self.audit = [], []
        self._n = 0
        self._last_mode = Mode.AUTOMATIC
        self._apply_desired(now)

    # ---------- read model ----------
    @property
    def mode(self):
        if self.degraded:
            return Mode.DEGRADED
        if self.emergencies:
            return Mode.EMERGENCY
        if self.manual_phase:
            return Mode.MANUAL
        return Mode.AUTOMATIC

    @property
    def controller_online(self):
        return not any(t != "SENSOR" for t, _ in self.failed_devices)

    def desired(self):
        sig = {d: Signal.RED for d in Direction}
        if self.step in (Step.GREEN, Step.YELLOW) and self.phase:
            s = Signal.GREEN if self.step == Step.GREEN else Signal.YELLOW
            for d in PHASE_DIRS[self.phase]:
                sig[d] = s
        return sig

    def alerts(self):
        a = [f"{t}_OFFLINE" + (f":{d}" if d else "") for t, d in sorted(self.failed_devices, key=str)]
        if self.degraded:
            a.append("DEGRADED_MODE")
        a += [f"COMMAND_PENDING:{c.command_id}" for c in self.pending.values()]
        if any(v == Signal.UNKNOWN for v in self.actual.values()):
            a.append("UNKNOWN_DEVICE_STATE")
        return a

    def drain_commands(self):
        out, self.outbox = self.outbox, []
        return out

    def drain_audit(self):
        out, self.audit = self.audit, []
        return out

    # ---------- inputs ----------
    def handle_sensor_event(self, ev, now):
        try:
            eid, vid = str(ev["event_id"]), str(ev["vehicle_id"])
            direction, etype, seq = Direction(ev["direction"]), ev["event_type"], int(ev["sequence_no"])
            if etype not in ("VEHICLE_ARRIVED", "VEHICLE_CLEARED"):
                raise ValueError("event_type")
            vtype = VehicleType(ev["vehicle_type"]) if etype == "VEHICLE_ARRIVED" else None
        except (KeyError, ValueError, TypeError):
            self._log(now, "SENSOR_EVENT_REJECTED", reason="MALFORMED", event_id=str(ev.get("event_id")))
            return Result("REJECTED", "MALFORMED")
        if eid in self.seen_events:                      # idempotency: event_id is authoritative
            self._log(now, "SENSOR_EVENT_DUPLICATE", event_id=eid)
            return Result("DUPLICATE")
        self.seen_events.add(eid)
        if seq <= self.vehicle_seq.get(vid, -1):         # sequence_no orders events per vehicle
            self._log(now, "SENSOR_EVENT_STALE", event_id=eid, vehicle_id=vid, sequence_no=seq)
            return Result("STALE")
        self.vehicle_seq[vid] = seq

        if etype == "VEHICLE_ARRIVED":
            if any(v.id == vid for q in self.queues.values() for v in q):
                self._log(now, "SENSOR_EVENT_REJECTED", reason="ALREADY_QUEUED", event_id=eid)
                return Result("REJECTED", "ALREADY_QUEUED")
            self.queues[direction].append(Vehicle(vid, vtype, now))   # server time for waiting
            if vtype == VehicleType.EMERGENCY:
                self.emergencies.setdefault(vid, (direction, now))
                self._log(now, "EMERGENCY_DETECTED", direction=direction, vehicle_id=vid)
            self._log(now, "VEHICLE_DETECTED", direction=direction, vehicle_id=vid,
                      vehicle_type=vtype, sensor_ts=str(ev.get("timestamp")))
        else:
            found = next((d for d, q in self.queues.items() for v in q if v.id == vid), None)
            if found is None:
                # tombstone (vehicle_seq set above) so a late ARRIVED with lower seq is ignored
                self._log(now, "SENSOR_EVENT_REJECTED", reason="NO_MATCHING_ARRIVAL", event_id=eid)
                return Result("REJECTED", "NO_MATCHING_ARRIVAL")
            self.queues[found] = [v for v in self.queues[found] if v.id != vid]
            self._log(now, "VEHICLE_CLEARED", direction=found, vehicle_id=vid)
            if self.emergencies.pop(vid, None):
                self._log(now, "EMERGENCY_CLEARED", vehicle_id=vid)
        self._note_mode(now)
        return Result("ACCEPTED")

    def command(self, cmd, direction, now):
        if cmd == "RETURN_TO_AUTOMATIC":
            if self.degraded:
                if not self.controller_online:
                    return Result("REJECTED", "CONTROLLER_OFFLINE")
                self.degraded = False
                self.requested = {d: None for d in Direction}   # re-send everything, trust nothing
                self.pending.clear()
                self._enter(Step.ALL_RED, now, self.cfg.all_red_s)
            self.manual_phase = self.manual_until = None
            self._log(now, "RETURN_TO_AUTOMATIC")
            self._note_mode(now)
            return Result("ACCEPTED")
        if cmd == "MANUAL_GREEN_REQUEST":
            if self.degraded:
                return Result("REJECTED", "DEGRADED")
            if self.emergencies:
                return Result("REJECTED", "EMERGENCY_ACTIVE")   # emergency > manual
            try:
                d = Direction(direction)
            except ValueError:
                return Result("REJECTED", "INVALID_DIRECTION")
            self.manual_phase = phase_of(d)                      # last valid command wins
            self.manual_until = now + self.cfg.manual_ttl_s
            self._log(now, "MANUAL_OVERRIDE", direction=d, expires_at=self.manual_until)
            self._note_mode(now)
            return Result("ACCEPTED")
        return Result("REJECTED", "UNKNOWN_COMMAND")

    def on_ack(self, command_id, status, actual_state, now):
        if command_id in self.acked:
            self._log(now, "DUPLICATE_ACK", command_id=command_id)
            return Result("DUPLICATE")
        cmd = self.pending.get(command_id)
        if cmd is None:
            self._log(now, "UNKNOWN_ACK", command_id=command_id)   # late/superseded/unknown
            return Result("REJECTED", "UNKNOWN_COMMAND")
        try:
            state = Signal(actual_state)
        except ValueError:
            return Result("REJECTED", "INVALID_STATE")
        del self.pending[command_id]
        self.acked.add(command_id)
        if status != "ACK":
            self.actual[cmd.direction] = Signal.UNKNOWN
            self._log(now, "COMMAND_FAILED", command_id=command_id, status=status)
            self._degrade(now, "COMMAND_FAILED")
            return Result("ACCEPTED", "FAILED")
        prev = self.actual[cmd.direction]
        self.actual[cmd.direction] = state
        self._log(now, "CONTROLLER_ACK", command_id=command_id, direction=cmd.direction,
                  previous_state=prev, new_state=state)
        if state != cmd.requested_state:
            self._log(now, "STATE_MISMATCH", command_id=command_id,
                      desired=cmd.requested_state, actual=state)
            self._degrade(now, "STATE_MISMATCH")
        greens = {phase_of(d) for d, s in self.actual.items() if s == Signal.GREEN}
        if len(greens) > 1:
            self._degrade(now, "CONFLICTING_ACTUAL_GREEN")
        return Result("ACCEPTED")

    def on_device_status(self, device_type, direction, status, now):
        key = (device_type, direction)
        self._log(now, "DEVICE_STATUS", device_type=device_type, direction=direction, status=status)
        if status == "OFFLINE":
            self.failed_devices.add(key)
            if device_type != "SENSOR":          # sensor failure: alert only, queues may be stale
                self.actual = {d: Signal.UNKNOWN for d in Direction}
                self.pending.clear()
                self._degrade(now, f"{device_type}_OFFLINE")
        elif status == "ONLINE":
            self.failed_devices.discard(key)     # stays DEGRADED until admin RETURN_TO_AUTOMATIC
        return Result("ACCEPTED")

    def tick(self, now):
        self._expire(now)
        self._check_timeouts(now)
        if not self.degraded:
            self._advance(now)
        self._note_mode(now)

    # ---------- internals ----------
    def _wanted(self, now):
        if self.emergencies:                       # oldest emergency wins; others wait their turn
            d, _ = min(self.emergencies.values(), key=lambda x: x[1])
            return phase_of(d)
        if self.manual_phase:
            return self.manual_phase
        if self.step == Step.GREEN:
            return scheduler.choose_next(self.phase, self.queues, now, self.cfg, now - self.step_started)
        return None

    def _advance(self, now):
        if self.step == Step.GREEN:
            want = self._wanted(now)
            if want and want != self.phase:
                self.target = want
                self._enter(Step.YELLOW, now, self.cfg.yellow_s)
        elif self.step == Step.YELLOW and now >= self.step_deadline:
            self._enter(Step.ALL_RED, now, self.cfg.all_red_s)
        elif self.step == Step.ALL_RED and now >= self.step_deadline:
            target = (self._wanted(now) or self.target
                      or scheduler.best_phase(self.queues, now, self.cfg))
            if self._safe_to_green(target):
                self.target = None
                self._enter(Step.GREEN, now, 0, phase=target)

    def _safe_to_green(self, target):
        if not self.cfg.require_ack:
            return True
        return all(self.actual[d] == Signal.RED for d in Direction if d not in PHASE_DIRS[target])

    def _enter(self, step, now, dur, phase=None):
        prev = self.step
        self.step = step
        if phase is not None:
            self.phase = phase
        self.step_started, self.step_deadline = now, now + dur
        self._log(now, "TRANSITION_STARTED", from_step=prev, to_step=step, phase=self.phase)
        self._apply_desired(now)

    def _apply_desired(self, now):
        des = self.desired()
        for d in Direction:
            if self.requested[d] == des[d]:
                continue
            self._n += 1
            cmd = SignalCommand(f"cmd-{self._n}", d, des[d], now)
            for cid, p in list(self.pending.items()):
                if p.direction == d:
                    del self.pending[cid]
            self.pending[cmd.command_id] = cmd
            self.requested[d] = des[d]
            self.outbox.append(replace(cmd))
            self._log(now, "SIGNAL_REQUESTED", direction=d, state=des[d], command_id=cmd.command_id)
        self.assert_safe()

    def assert_safe(self):
        for sig in (self.desired(), {d: s for d, s in self.requested.items() if s}):
            if len({phase_of(d) for d, s in sig.items() if s == Signal.GREEN}) > 1:
                raise SafetyViolation("conflicting GREEN")

    def _degrade(self, now, reason):
        if self.degraded:
            return
        self.degraded = True
        self._log(now, "DEGRADED_ENTERED", reason=reason)
        self._enter(Step.ALL_RED, now, self.cfg.all_red_s)   # fail-safe: all RED, no auto-green

    def _check_timeouts(self, now):
        for cmd in list(self.pending.values()):
            if now - cmd.issued_at < self.cfg.ack_timeout_s:
                continue
            if cmd.attempts <= self.cfg.max_retries and self.controller_online:
                cmd.attempts += 1
                cmd.issued_at = now
                self.outbox.append(replace(cmd))             # same command_id -> idempotent retry
                self._log(now, "CONTROLLER_TIMEOUT", command_id=cmd.command_id, action="RETRY")
            else:
                del self.pending[cmd.command_id]
                self.actual[cmd.direction] = Signal.UNKNOWN  # never assume it executed
                self._log(now, "CONTROLLER_TIMEOUT", command_id=cmd.command_id, action="GAVE_UP")
                self._degrade(now, "ACK_TIMEOUT")

    def _expire(self, now):
        for vid, (d, since) in list(self.emergencies.items()):
            if now - since >= self.cfg.emergency_stale_s:
                del self.emergencies[vid]
                self._log(now, "EMERGENCY_EXPIRED", vehicle_id=vid)
        if self.manual_phase and now >= self.manual_until:
            self.manual_phase = self.manual_until = None
            self._log(now, "MANUAL_EXPIRED")

    def _note_mode(self, now):
        if self.mode != self._last_mode:
            self._log(now, "MODE_CHANGED", previous=self._last_mode, new=self.mode)
            self._last_mode = self.mode

    def _log(self, now, event_type, **kw):
        row = {"junction_id": self.id, "event_type": event_type, "at": now}
        row.update({k: (v.value if isinstance(v, Enum) else v) for k, v in kw.items()})
        self.audit.append(row)

    # ---------- persistence / recovery ----------
    def snapshot(self):
        """Full state as JSON-safe dict (stored in DB after every operation)."""
        return {
            "queues": {d.value: [[v.id, v.type.value, v.arrived_at] for v in q] for d, q in self.queues.items()},
            "emergencies": {k: [d.value, t] for k, (d, t) in self.emergencies.items()},
            "manual_phase": self.manual_phase.value if self.manual_phase else None,
            "manual_until": self.manual_until,
            "degraded": self.degraded,
            "phase": self.phase.value if self.phase else None,
            "seen_events": sorted(self.seen_events),
            "vehicle_seq": self.vehicle_seq,
            "step": self.step.value,
            "step_started": self.step_started,
            "step_deadline": self.step_deadline,
            "target": self.target.value if self.target else None,
            "actual": {d.value: s.value for d, s in self.actual.items()},
            "requested": {d.value: (s.value if s else None) for d, s in self.requested.items()},
            "pending": [[c.command_id, c.direction.value, c.requested_state.value, c.issued_at, c.attempts]
                        for c in self.pending.values()],
            "acked": sorted(self.acked),
            "failed_devices": [[t, d] for t, d in sorted(self.failed_devices, key=str)],
            "n": self._n,
        }

    def _load_core(self, data):
        for d, rows in data["queues"].items():
            self.queues[Direction(d)] = [Vehicle(i, VehicleType(t), a) for i, t, a in rows]
        self.emergencies = {k: (Direction(d), t) for k, (d, t) in data["emergencies"].items()}
        self.manual_phase = Phase(data["manual_phase"]) if data["manual_phase"] else None
        self.manual_until = data["manual_until"]
        self.phase = Phase(data["phase"]) if data["phase"] else None
        self.seen_events = set(data["seen_events"])
        self.vehicle_seq = dict(data["vehicle_seq"])
        self.degraded = data["degraded"]
        self.failed_devices = {(t, d) for t, d in data["failed_devices"]}
        self.acked = set(data["acked"])
        self._n = data["n"]

    @classmethod
    def from_state(cls, junction_id, data, cfg=None):
        """EXACT reload (normal request): the app has been running, so signal state is trusted."""
        e = cls(junction_id, cfg, 0.0)
        e.outbox.clear()
        e.audit.clear()
        e._load_core(data)
        e.step = Step(data["step"])
        e.step_started, e.step_deadline = data["step_started"], data["step_deadline"]
        e.target = Phase(data["target"]) if data["target"] else None
        e.actual = {Direction(d): Signal(s) for d, s in data["actual"].items()}
        e.requested = {Direction(d): (Signal(s) if s else None) for d, s in data["requested"].items()}
        e.pending = {c[0]: SignalCommand(c[0], Direction(c[1]), Signal(c[2]), c[3], c[4]) for c in data["pending"]}
        e._last_mode = e.mode
        return e

    @classmethod
    def restore(cls, junction_id, data, now, cfg=None):
        """RECOVERY after restart: keep queues/emergency/manual, trust NO signal state.
        Boot in ALL_RED, re-request every signal with fresh command_ids; GREEN only after fresh ACKs."""
        e = cls(junction_id, cfg, now)
        e._load_core(data)
        e.requested = {d: None for d in Direction}
        e.pending.clear()
        e.outbox.clear()
        e.audit.clear()
        e.actual = {d: Signal.UNKNOWN for d in Direction}
        e.step, e.target = Step.ALL_RED, None
        e.step_started, e.step_deadline = now, now + e.cfg.all_red_s
        e._apply_desired(now)
        e._last_mode = e.mode
        e._log(now, "RECOVERED_AFTER_RESTART", note="signal state untrusted; ALL_RED re-requested")
        return e
