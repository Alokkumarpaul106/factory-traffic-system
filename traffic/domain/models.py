"""Domain types. Pure Python: no Django / HTTP / DB imports."""
from dataclasses import dataclass
from enum import Enum


class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class Phase(str, Enum):
    NORTH_SOUTH = "NORTH_SOUTH"
    EAST_WEST = "EAST_WEST"


class Signal(str, Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"
    UNKNOWN = "UNKNOWN"  # physical state not confirmed


class Step(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    ALL_RED = "ALL_RED"


class Mode(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"
    DEGRADED = "DEGRADED"


class VehicleType(str, Enum):
    EMERGENCY = "EMERGENCY"
    TRUCK = "TRUCK"
    FORKLIFT = "FORKLIFT"
    EMPLOYEE_VEHICLE = "EMPLOYEE_VEHICLE"


# Junction config lives here (not hard-coded in engine logic).
PHASE_DIRS = {
    Phase.NORTH_SOUTH: (Direction.NORTH, Direction.SOUTH),
    Phase.EAST_WEST: (Direction.EAST, Direction.WEST),
}
VEHICLE_WEIGHT = {
    VehicleType.EMERGENCY: 100,
    VehicleType.TRUCK: 3,
    VehicleType.FORKLIFT: 2,
    VehicleType.EMPLOYEE_VEHICLE: 1,
}


def phase_of(d: Direction) -> Phase:
    return Phase.NORTH_SOUTH if d in PHASE_DIRS[Phase.NORTH_SOUTH] else Phase.EAST_WEST


def opposite(p: Phase) -> Phase:
    return Phase.EAST_WEST if p == Phase.NORTH_SOUTH else Phase.NORTH_SOUTH


@dataclass(frozen=True)
class Config:
    green_s: float = 30
    yellow_s: float = 5
    all_red_s: float = 2
    min_green_s: float = 10      # avoid unnecessary switching
    hysteresis: float = 1.2      # other phase must score 20% higher to steal green early
    wait_factor: float = 0.5     # score points per second of oldest waiting vehicle
    max_wait_s: float = 90       # starvation limit
    manual_ttl_s: float = 120    # manual override auto-expires
    emergency_stale_s: float = 60
    ack_timeout_s: float = 5
    max_retries: int = 2
    require_ack: bool = True     # never go GREEN until conflicting sides are CONFIRMED red


@dataclass
class Vehicle:
    id: str
    type: VehicleType
    arrived_at: float            # SERVER time (used for waiting-time)


@dataclass
class SignalCommand:
    command_id: str
    direction: Direction
    requested_state: Signal
    issued_at: float
    attempts: int = 1


@dataclass
class Result:
    status: str                  # ACCEPTED | DUPLICATE | STALE | REJECTED
    reason: str = ""
