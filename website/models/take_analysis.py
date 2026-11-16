"""Private, reusable source transcription; never stores approved edit decisions."""
from django.db import models


class SourceTakeAnalysis(models.Model):
    project = models.ForeignKey('ExternalMediaProject', on_delete=models.CASCADE)
    sha256 = models.CharField(max_length=64)
    language = models.CharField(max_length=16, default='pt')
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, default='pending')
    lease = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveBigIntegerField(default=0)
    transcript = models.JSONField(default=list)
    chunks = models.JSONField(default=dict)
    transcript_ready = models.BooleanField(default=False)
    suggestions = models.JSONField(default=list)
    review_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'sha256', 'language', 'version'], name='unique_source_take_analysis')]
