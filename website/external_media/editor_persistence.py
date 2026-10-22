"""HTTP concurrency boundary shared by the existing preview mutation views."""
import hashlib
import json
import uuid

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from website.models.external_media import ExternalMediaProject, PreviewOperation


class PreviewMutationMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.method != 'POST':
            return super().dispatch(request, *args, **kwargs)
        if not request.headers.get('X-Editor-Operation'):
            # Legacy integrations retain their response shape, but cannot edit
            # the mutable subtitle projection while the approved render uses it.
            with transaction.atomic():
                project = get_object_or_404(
                    ExternalMediaProject.objects.select_for_update(), public_id=kwargs['public_id'],
                )
                if project.status in {project.Status.PENDING, project.Status.PROCESSING, project.Status.ASSEMBLING}:
                    return JsonResponse({'error': 'O projeto não está disponível para edição.'}, status=409)
                return super().dispatch(request, *args, **kwargs)
        try:
            operation_id = uuid.UUID(request.headers['X-Editor-Operation'])
            expected = int(request.headers['X-Editor-Version'])
            if expected < 0:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return JsonResponse({'error': 'Identificação de edição inválida.'}, status=400)

        digest = hashlib.sha256(request.path.encode())
        if request.content_type == 'application/json':
            digest.update(request.body)
        else:
            digest.update(json.dumps(sorted(request.POST.lists()), ensure_ascii=False).encode())
            for key, files in sorted(request.FILES.lists()):
                for upload in files:
                    digest.update(json.dumps([key, upload.name, upload.content_type]).encode())
                    for chunk in upload.chunks():
                        digest.update(chunk)
                    upload.seek(0)
        request_hash = digest.hexdigest()

        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update(), public_id=kwargs['public_id'],
            )
            receipt = PreviewOperation.objects.filter(project=project, operation_id=operation_id).first()
            # A lost response can be retried even after its base version became stale.
            if receipt:
                if receipt.member_id != self.member.pk or receipt.request_hash != request_hash:
                    return JsonResponse({'error': 'Identificador reutilizado para outra edição.'}, status=409)
                return JsonResponse(receipt.response)
            if project.editorial_version != expected:
                return JsonResponse({
                    'error': 'O projeto foi alterado em outra sessão. Suas alterações locais foram preservadas.',
                    'editorial_version': project.editorial_version, 'conflict': True,
                }, status=409)
            if project.status not in {project.Status.AWAITING_REVIEW, project.Status.FINISHED}:
                return JsonResponse({'error': 'O projeto não está disponível para edição.', 'conflict': True}, status=409)
            response = super().dispatch(request, *args, **kwargs)
            if 200 <= response.status_code < 300 and response.get('Content-Type', '').startswith('application/json'):
                project.refresh_from_db(fields=['editorial_version'])
                data = json.loads(response.content)
                data['editorial_version'] = project.editorial_version
                PreviewOperation.objects.create(
                    project=project, member=self.member, operation_id=operation_id,
                    request_hash=request_hash, response=data,
                )
                return JsonResponse(data)
            return response
