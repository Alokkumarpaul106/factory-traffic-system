
from datetime import timedelta

from django.utils import timezone

from .models import ControllerCommand


def ack_pending(service, delay=0.3):
    cutoff = timezone.now() - timedelta(seconds=delay)
    rows = ControllerCommand.objects.filter(status=ControllerCommand.PENDING, issued_at__lte=cutoff,
                                            junction__auto_ack=True)
    for r in list(rows):
        service.controller_event({"junction_id": r.junction_id, "command_id": r.command_id,
                                  "status": "ACK", "actual_state": r.requested_state})
