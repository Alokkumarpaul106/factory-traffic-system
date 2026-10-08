from .infrastructure.rest_controller_sim import RestSimulatorController
from .services import TrafficService

service = TrafficService(RestSimulatorController())
