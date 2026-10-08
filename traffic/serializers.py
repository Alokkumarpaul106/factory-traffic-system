
import dataclasses

from rest_framework import serializers

from .domain.models import Config, Direction, Signal, VehicleType

DIRS = [d.value for d in Direction]


class JunctionCreateSerializer(serializers.Serializer):
    id = serializers.RegexField(r"^[A-Za-z0-9_-]{1,32}$")
    name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    config = serializers.DictField(required=False)

    def validate_config(self, value):
        allowed = {f.name for f in dataclasses.fields(Config)}
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(f"unknown config keys: {sorted(unknown)}")
        for k, v in value.items():
            if k == "require_ack":
                ok = isinstance(v, bool)
            else:
                ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
            if not ok:
                raise serializers.ValidationError(f"invalid value for {k}")
        return value


class SensorEventSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=64)
    junction_id = serializers.CharField(max_length=32)
    direction = serializers.ChoiceField(choices=DIRS)
    event_type = serializers.ChoiceField(choices=["VEHICLE_ARRIVED", "VEHICLE_CLEARED"])
    vehicle_id = serializers.CharField(max_length=64)
    vehicle_type = serializers.ChoiceField(choices=[v.value for v in VehicleType], required=False)
    sequence_no = serializers.IntegerField(min_value=0)
    timestamp = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs["event_type"] == "VEHICLE_ARRIVED" and "vehicle_type" not in attrs:
            raise serializers.ValidationError({"vehicle_type": "required for VEHICLE_ARRIVED"})
        return attrs


class CommandSerializer(serializers.Serializer):
    command = serializers.ChoiceField(choices=["MANUAL_GREEN_REQUEST", "RETURN_TO_AUTOMATIC"])
    direction = serializers.ChoiceField(choices=DIRS, required=False)

    def validate(self, attrs):
        if attrs["command"] == "MANUAL_GREEN_REQUEST" and "direction" not in attrs:
            raise serializers.ValidationError({"direction": "required for MANUAL_GREEN_REQUEST"})
        return attrs


ACK_STATUSES = ("ACK", "NACK", "FAILED")


class ControllerEventSerializer(serializers.Serializer):
    """Two shapes: ACK (has command_id) or device status (has device_type)."""
    junction_id = serializers.CharField(max_length=32)
    event_id = serializers.CharField(required=False, max_length=64)
    command_id = serializers.CharField(required=False, max_length=64)
    status = serializers.ChoiceField(choices=list(ACK_STATUSES) + ["ONLINE", "OFFLINE", "DEGRADED", "WARNING", "UNKNOWN"])
    actual_state = serializers.ChoiceField(choices=[s.value for s in Signal], required=False)
    device_type = serializers.ChoiceField(choices=["SIGNAL_CONTROLLER", "JUNCTION_CONTROLLER", "SENSOR"], required=False)
    direction = serializers.ChoiceField(choices=DIRS, required=False)
    timestamp = serializers.DateTimeField(required=False)

    def validate(self, a):
        if "command_id" in a:
            if a["status"] not in ACK_STATUSES:
                raise serializers.ValidationError({"status": "ACK/NACK/FAILED expected for a command_id"})
            if a["status"] == "ACK" and "actual_state" not in a:
                raise serializers.ValidationError({"actual_state": "required when status=ACK"})
        elif "device_type" in a:
            if a["status"] in ACK_STATUSES:
                raise serializers.ValidationError({"status": "device status expected (ONLINE/OFFLINE/...)"})
        else:
            raise serializers.ValidationError("send command_id (acknowledgement) or device_type (status event)")
        return a


class SimulatorSerializer(serializers.Serializer):
    auto_ack = serializers.BooleanField()
