from typing import Protocol


class ControllerPort(Protocol):
    """The only thing the application layer knows about physical controllers.
    REST simulator today, MQTT adapter later (send -> publish)."""
    def send(self, junction_id: str, command) -> None: ...
