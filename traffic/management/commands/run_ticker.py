import logging
import time

from django.core.management.base import BaseCommand

from traffic.container import service
from traffic.simulator import ack_pending

log = logging.getLogger("traffic.ticker")


class Command(BaseCommand):
    help = "Signal-timer loop. Recovers state on boot, then ticks every junction (+ controller simulator)."

    def add_arguments(self, p):
        p.add_argument("--interval", type=float, default=0.5)
        p.add_argument("--no-simulator", action="store_true", help="do not auto-ACK commands")

    def handle(self, *args, **o):
        service.recover_all()
        log.info("ticker started (recovery done, interval=%ss)", o["interval"])
        while True:
            t0 = time.monotonic()
            try:
                service.tick_all()
                if not o["no_simulator"]:
                    ack_pending(service)
            except Exception:
                log.exception("tick failed (state unchanged, will retry)")
            time.sleep(max(0.0, o["interval"] - (time.monotonic() - t0)))
