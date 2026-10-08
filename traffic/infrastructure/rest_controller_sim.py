import logging

logger = logging.getLogger("traffic.controller")


class RestSimulatorController:
    """REST-simulator adapter. The command is already persisted as PENDING (in the DB) before
    send() is called; the 'device' answers through POST /api/controller-events (or the
    auto-ACK simulator in traffic/simulator.py). An MQTT adapter would publish here."""
    def send(self, junction_id, command):
        logger.info("COMMAND %s -> junction %s %s=%s (attempt %s)", command.command_id, junction_id,
                    command.direction.value, command.requested_state.value, command.attempts)
