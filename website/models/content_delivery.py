"""Reusable physical files, step requirements and immutable delivery versions."""
import uuid

from django.conf import settings
from django.db import models

from website.content_file_storage import get_content_file_storage

from ._base import BaseModel
from .external_media import get_external_media_storage


class ContentFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(storage=get_content_file_storage, max_length=500)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, unique=True, null=True, blank=True)
    origin = models.CharField(max_length=40, default='manual_upload')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    ready = models.BooleanField(default=False)
    upload_token = models.UUIDField(default=uuid.uuid4)
    upload_expires_at = models.DateTimeField(null=True)

    @property
    def storage(self):
        return get_external_media_storage() if self.origin == 'video_editor' else self.file.storage

    class Meta:
        constraints = [models.UniqueConstraint(fields=['file'], name='unique_content_file_path')]


class MediaDeliveryRequirement(BaseModel):
    task = models.ForeignKey('MediaTask', on_delete=models.CASCADE, related_name='delivery_requirements')
    format = models.SlugField(max_length=40)
    label = models.CharField(max_length=100)
    required = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['task', 'format', 'label'], name='unique_step_delivery_format')]


class MediaDelivery(BaseModel):
    requirement = models.ForeignKey(MediaDeliveryRequirement, on_delete=models.CASCADE, related_name='deliveries')
    asset = models.ForeignKey(ContentFile, on_delete=models.PROTECT, related_name='deliveries')
    version = models.PositiveIntegerField()
    original_name = models.CharField(max_length=255)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=30, default='submitted')
    # Client-generated key makes retries safe after a lost HTTP response.
    submission_id = models.UUIDField(default=uuid.uuid4, unique=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['requirement', 'version'], name='unique_delivery_version')]


class MediaDeliveryReview(BaseModel):
    task = models.ForeignKey('MediaTask', on_delete=models.CASCADE, related_name='delivery_reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    requirement = models.ForeignKey(MediaDeliveryRequirement, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    delivery = models.ForeignKey(MediaDelivery, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviews')
    decision = models.CharField(max_length=12, choices=[('approved', 'Aprovada'), ('rejected', 'Alteração solicitada')])
    note = models.TextField(blank=True)
    snapshot = models.JSONField(default=list)

    class Meta:
        ordering = ['-created_at', '-pk']
