
from datetime import datetime

from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from .container import service
from .serializers import (CommandSerializer, ControllerEventSerializer,
                          JunctionCreateSerializer, SensorEventSerializer)
from .services import ConcurrencyError, JunctionExists, UnknownJunction


def exception_handler(exc, context):
    if isinstance(exc, UnknownJunction):
        return Response({"error": "UNKNOWN_JUNCTION", "detail": f"junction '{exc}' not found"}, status=404)
    if isinstance(exc, ConcurrencyError):
        return Response({"error": "BUSY", "detail": "concurrent update, please retry"}, status=503)
    return drf_exception_handler(exc, context)


def _plain(data):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()}


class JunctionList(APIView):
    def get(self, request):
        return Response(service.list_status())

    def post(self, request):
        s = JunctionCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            service.create_junction(d["id"], d.get("name", ""), d.get("config"))
        except JunctionExists:
            return Response({"error": "JUNCTION_EXISTS", "detail": f"'{d['id']}' already exists"}, status=409)
        return Response(service.status(d["id"]), status=201)


class JunctionDetail(APIView):          # also used for /status (same read model)
    def get(self, request, junction_id):
        return Response(service.status(junction_id))


class SensorEvents(APIView):
    CODES = {"ACCEPTED": 201, "DUPLICATE": 200, "STALE": 200, "REJECTED": 409}

    def post(self, request):
        s = SensorEventSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payload = _plain(s.validated_data)
        r = service.ingest_sensor_event(payload)
        return Response({"status": r.status, "reason": r.reason, "event_id": payload["event_id"]},
                        status=self.CODES.get(r.status, 200))


class Commands(APIView):
    def post(self, request, junction_id):
        s = CommandSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        r = service.send_command(junction_id, d["command"], d.get("direction"))
        body = {"result": r.status, "reason": r.reason, "status": service.status(junction_id)}
        return Response(body, status=200 if r.status == "ACCEPTED" else 409)


class ControllerEvents(APIView):
    def post(self, request):
        s = ControllerEventSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        r = service.controller_event(_plain(s.validated_data))
        code = 200 if r.status in ("ACCEPTED", "DUPLICATE") else 409
        return Response({"status": r.status, "reason": r.reason}, status=code)


class History(APIView):
    def get(self, request, junction_id):
        try:
            limit = max(1, min(int(request.query_params.get("limit", 100)), 500))
        except ValueError:
            return Response({"error": "INVALID_LIMIT"}, status=400)
        return Response(service.history(junction_id, limit))


from .serializers import SimulatorSerializer  # noqa: E402


class Simulator(APIView):
    def post(self, request, junction_id):
        s = SimulatorSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        service.set_auto_ack(junction_id, s.validated_data["auto_ack"])
        return Response(service.status(junction_id))
