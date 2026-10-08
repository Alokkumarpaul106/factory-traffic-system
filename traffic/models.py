from django.db import models
from django.utils import timezone


class Junction(models.Model):
    id = models.CharField(max_length=32, primary_key=True)
    name = models.CharField(max_length=100, blank=True)
    config = models.JSONField(default=dict, blank=True)        
    auto_ack = models.BooleanField(default=True)               
    created_at = models.DateTimeField(default=timezone.now)


class JunctionState(models.Model):
   
    junction = models.OneToOneField(Junction, on_delete=models.CASCADE, related_name="state")
    data = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)


class ControllerCommand(models.Model):
    PENDING, ACKED, FAILED, TIMED_OUT, SUPERSEDED = "PENDING", "ACKED", "FAILED", "TIMED_OUT", "SUPERSEDED"
    junction = models.ForeignKey(Junction, on_delete=models.CASCADE, related_name="commands")
    command_id = models.CharField(max_length=64)
    direction = models.CharField(max_length=10)
    requested_state = models.CharField(max_length=10)
    status = models.CharField(max_length=12, default=PENDING)
    attempts = models.PositiveSmallIntegerField(default=1)
    issued_at = models.DateTimeField(default=timezone.now)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["junction", "command_id"], name="uniq_cmd_per_junction")]


class AuditEvent(models.Model):
    junction = models.ForeignKey(Junction, on_delete=models.CASCADE, related_name="audit")
    event_type = models.CharField(max_length=40, db_index=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["junction", "-id"])]
