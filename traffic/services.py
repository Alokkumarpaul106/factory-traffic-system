
import logging
import time
from datetime import datetime, timezone as dt_tz

from django.db import transaction
from django.utils import timezone

from .domain.engine import JunctionEngine
from .domain.models import Config, Step
from .models import AuditEvent, ControllerCommand, Junction, JunctionState

logger = logging.getLogger("traffic.service")


class UnknownJunction(Exception):
    pass


class JunctionExists(Exception):
    pass


class ConcurrencyError(Exception):
    pass


def _ts(epoch):
    return datetime.fromtimestamp(epoch, tz=dt_tz.utc)


def build_status(e, now):
    unknown = any(v.value == "UNKNOWN" for v in e.actual.values())
    cs = "OFFLINE" if not e.controller_online else "DEGRADED" if e.degraded else "UNKNOWN" if unknown else "ONLINE"
    return {
        "junction_id": e.id, "mode": e.mode.value, "phase": e.phase.value if e.phase else None,
        "step": e.step.value,
        "step_remaining_s": None if e.step == Step.GREEN else max(0, round(e.step_deadline - now, 1)),
        "controller_status": cs,
        "desired_signals": {d.value: s.value for d, s in e.desired().items()},
        "actual_signals": {d.value: s.value for d, s in e.actual.items()},
        "queues": {d.value: len(q) for d, q in e.queues.items()},
        "queue_vehicles": {d.value: [{"vehicle_id": v.id, "type": v.type.value} for v in q]
                           for d, q in e.queues.items()},
        "emergencies": [{"vehicle_id": k, "direction": d.value, "since": t} for k, (d, t) in e.emergencies.items()],
        "manual": {"active": e.manual_phase is not None, "phase": e.manual_phase.value if e.manual_phase else None,
                   "expires_at": e.manual_until},
        "alerts": e.alerts(),
        "pending_commands": [{"command_id": c.command_id, "direction": c.direction.value,
                              "requested_state": c.requested_state.value, "attempts": c.attempts}
                             for c in e.pending.values()],
    }


class TrafficService:
    def __init__(self, controller, clock=time.time):
        self.controller, self.clock = controller, clock

    @staticmethod
    def _cfg(j):
        return Config(**j.config) if j.config else Config()

    def _get(self, junction_id):
        try:
            return Junction.objects.select_related("state").get(pk=junction_id)
        except Junction.DoesNotExist:
            raise UnknownJunction(junction_id)

    def _mutate(self, junction_id, fn, recover=False, retries=8):
        """Optimistic concurrency: UPDATE ... WHERE version=v; 0 rows => someone else won, reload+retry.
        Commands go to the controller only AFTER the DB commit succeeded."""
        for _ in range(retries):
            j = self._get(junction_id)
            state, now, cfg = j.state, self.clock(), self._cfg(j)
            engine = (JunctionEngine.restore(junction_id, state.data, now, cfg) if recover
                      else JunctionEngine.from_state(junction_id, state.data, cfg))
            result = fn(engine, now)
            commands, audit = engine.drain_commands(), engine.drain_audit()
            new_data = engine.snapshot()
            if not recover and not commands and not audit and new_data == state.data:
                return result, engine                     # no change: skip DB write
            with transaction.atomic():
                if not JunctionState.objects.filter(pk=state.pk, version=state.version).update(
                        data=new_data, version=state.version + 1, updated_at=timezone.now()):
                    continue
                self._flush(j, engine, commands, audit)
            for c in commands:
                self.controller.send(junction_id, c)
            return result, engine
        raise ConcurrencyError(junction_id)

    @staticmethod
    def _flush(j, engine, commands, audit):
        for c in commands:
            ControllerCommand.objects.update_or_create(
                junction=j, command_id=c.command_id,
                defaults={"direction": c.direction.value, "requested_state": c.requested_state.value,
                          "attempts": c.attempts, "status": ControllerCommand.PENDING, "issued_at": _ts(c.issued_at)})
        for r in audit:
            qs = ControllerCommand.objects.filter(junction=j, command_id=r.get("command_id"))
            et = r["event_type"]
            if et == "CONTROLLER_ACK":
                qs.update(status=ControllerCommand.ACKED, acked_at=_ts(r["at"]))
            elif et == "COMMAND_FAILED":
                qs.update(status=ControllerCommand.FAILED)
            elif et == "CONTROLLER_TIMEOUT" and r.get("action") == "GAVE_UP":
                qs.update(status=ControllerCommand.TIMED_OUT)
            logger.info("AUDIT %s %s", j.id, {k: v for k, v in r.items() if k != "junction_id"})
        ControllerCommand.objects.filter(junction=j, status=ControllerCommand.PENDING) \
            .exclude(command_id__in=list(engine.pending)).update(status=ControllerCommand.SUPERSEDED)
        AuditEvent.objects.bulk_create([AuditEvent(junction=j, event_type=r["event_type"], payload=r,
                                                   created_at=_ts(r["at"])) for r in audit])

    # ---- use-cases ----
    def create_junction(self, junction_id, name="", config=None):
        if Junction.objects.filter(pk=junction_id).exists():
            raise JunctionExists(junction_id)
        engine = JunctionEngine(junction_id, Config(**(config or {})), self.clock())
        commands, audit = engine.drain_commands(), engine.drain_audit()
        with transaction.atomic():
            j = Junction.objects.create(id=junction_id, name=name, config=config or {})
            JunctionState.objects.create(junction=j, data=engine.snapshot(), version=0)
            self._flush(j, engine, commands, audit)
        for c in commands:
            self.controller.send(junction_id, c)
        return j

    def ingest_sensor_event(self, payload):
        return self._mutate(str(payload.get("junction_id", "")), lambda e, now: e.handle_sensor_event(payload, now))[0]

    def send_command(self, junction_id, command, direction=None):
        return self._mutate(junction_id, lambda e, now: e.command(command, direction, now))[0]

    def controller_event(self, p):
        if "command_id" in p:
            fn = lambda e, now: e.on_ack(p["command_id"], p.get("status", "ACK"), p.get("actual_state") or "UNKNOWN", now)
        elif "device_type" in p:
            fn = lambda e, now: e.on_device_status(p["device_type"], p.get("direction"), p.get("status"), now)
        else:
            raise ValueError("need command_id or device_type")
        return self._mutate(str(p.get("junction_id", "")), fn)[0]

    def set_auto_ack(self, junction_id, value):
        self._get(junction_id)
        Junction.objects.filter(pk=junction_id).update(auto_ack=value)

    def tick(self, junction_id):
        self._mutate(junction_id, lambda e, now: e.tick(now))

    def tick_all(self):
        for jid in Junction.objects.values_list("id", flat=True):
            self.tick(jid)

    def recover_all(self):
        """Run once at ticker start: restart-safe recovery (signal state NOT trusted)."""
        for jid in Junction.objects.values_list("id", flat=True):
            self._mutate(jid, lambda e, now: None, recover=True)

    # ---- queries ----
    def status(self, junction_id):
        j = self._get(junction_id)
        s = build_status(JunctionEngine.from_state(junction_id, j.state.data, self._cfg(j)), self.clock())
        s["simulator"] = {"auto_ack": j.auto_ack}
        s["name"] = j.name
        return s

    def list_status(self):
        return [self.status(i) for i in Junction.objects.order_by("id").values_list("id", flat=True)]

    def history(self, junction_id, limit=100):
        self._get(junction_id)
        rows = AuditEvent.objects.filter(junction_id=junction_id).order_by("-id")[:limit]
        return [{**r.payload, "timestamp": r.created_at.isoformat()} for r in rows]
