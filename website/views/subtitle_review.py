import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from ..external_media.subtitle_reviews import SubtitleReviewService
from ..external_media.render_workflow import enqueue_video_work
from ..external_media.tasks import (
    create_subtitle_review_preview,
    render_reviewed_subtitles,
)
from ..models.external_media import (
    ExternalMediaProject,
    GlossaryTerm,
    MediaAsset,
    SubtitleReviewSession,
    SubtitleSuggestion,
    SubtitleTrack,
    SubtitleVideoVersionAsset,
)
from .external_media import protected_file_response
from .mixins import ExternalMediaRequiredMixin


LANGUAGE_LABELS = {'pt': 'Português', 'en': 'English'}


def _enqueue_review_preview(session_id):
    if settings.RENDER_WORKFLOW_ENABLED:
        return enqueue_video_work('review-preview', session_id)
    return create_subtitle_review_preview.delay(session_id).id


def _enqueue_review_render(project_id, review_id, celery_task_id):
    try:
        if settings.RENDER_WORKFLOW_ENABLED:
            task_id = enqueue_video_work('review-render', project_id, review_id)
            ExternalMediaProject.objects.filter(pk=project_id).update(celery_task_id=task_id)
            return task_id
        render_reviewed_subtitles.apply_async(
            args=[project_id, review_id], task_id=celery_task_id,
        )
        return celery_task_id
    except Exception:
        ExternalMediaProject.objects.filter(pk=project_id).update(
            status=ExternalMediaProject.Status.ERROR,
            current_step='Não foi possível iniciar a atualização do vídeo',
            error_message=(
                'Não foi possível iniciar o worker de vídeo no Render.'
                if settings.RENDER_WORKFLOW_ENABLED else
                'Verifique se o Redis e o worker Celery estão ativos.'
            ),
        )
        return None


class ProjectSubtitleReviewsView(ExternalMediaRequiredMixin, View):
    def get_project(self, public_id):
        return get_object_or_404(
            ExternalMediaProject.objects.select_related(
                'render_job', 'template_version__template',
            ).prefetch_related(
                'render_job__subtitle_tracks',
                'subtitle_review_sessions__suggestions',
            ),
            public_id=public_id,
        )

    def get(self, request, public_id):
        project = self.get_project(public_id)
        tracks = list(project.render_job.subtitle_tracks.all()) if project.render_job_id else []
        created_link = request.session.pop('subtitle_review_created_link', '')
        return render(request, 'member/external_media/subtitle_reviews.html', {
            'project': project,
            'tracks': tracks,
            'sessions': project.subtitle_review_sessions.all(),
            'created_link': created_link,
            'language_labels': LANGUAGE_LABELS,
            'has_dirty_tracks': any(track.subtitle_dirty for track in tracks),
            'pending_reviews': project.subtitle_review_sessions.filter(
                status__in=[
                    SubtitleReviewSession.Status.SUBMITTED,
                    SubtitleReviewSession.Status.UNDER_REVIEW,
                ],
            ).count(),
            'video_versions': project.subtitle_video_versions.prefetch_related('assets')[:10],
        })

    def post(self, request, public_id):
        project = self.get_project(public_id)
        if not project.render_job_id:
            messages.error(request, 'Gere o vídeo antes de criar uma revisão.')
            return redirect('external_media_project_subtitle_reviews', public_id=public_id)
        available = set(project.render_job.subtitle_tracks.values_list('language', flat=True))
        languages = [item for item in request.POST.getlist('languages') if item in available]
        if not languages:
            messages.error(request, 'Selecione ao menos um idioma disponível.')
            return redirect('external_media_project_subtitle_reviews', public_id=public_id)
        validity = request.POST.get('validity', '30')
        expires_at = None
        try:
            validity_days = int(validity)
            if validity_days not in {1, 7, 30}:
                raise ValueError
        except ValueError:
            validity_days = 30
            custom_value = parse_datetime(request.POST.get('custom_expires_at', ''))
            if custom_value:
                expires_at = custom_value if timezone.is_aware(custom_value) else timezone.make_aware(custom_value)
        if expires_at and expires_at <= timezone.now():
            messages.error(request, 'Escolha uma validade futura.')
            return redirect('external_media_project_subtitle_reviews', public_id=public_id)
        try:
            review, token = SubtitleReviewService.create_session(
                project,
                self.member,
                languages,
                validity_days=validity_days,
                expires_at=expires_at,
                reviewer_name=request.POST.get('reviewer_name', ''),
                reviewer_contact=request.POST.get('reviewer_contact', ''),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('external_media_project_subtitle_reviews', public_id=public_id)
        transaction.on_commit(lambda: _enqueue_review_preview(review.pk))
        request.session['subtitle_review_created_link'] = request.build_absolute_uri(
            reverse('public_subtitle_review', args=[token]),
        )
        messages.success(request, 'Link de revisão criado. Copie-o antes de sair desta página.')
        return redirect('external_media_project_subtitle_reviews', public_id=public_id)


class ProjectSubtitleReviewRevokeView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, session_id):
        review = get_object_or_404(
            SubtitleReviewSession, pk=session_id, project__public_id=public_id,
        )
        if not review.revoked_at:
            review.revoked_at = timezone.now()
            review.save(update_fields=['revoked_at', 'update_at'])
            SubtitleReviewService.log(review, 'LINK_REVOKED', self.member)
        messages.success(request, 'O link foi revogado imediatamente.')
        return redirect('external_media_project_subtitle_reviews', public_id=public_id)


class ProjectSubtitleVideoVersionAssetView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, asset_id):
        asset = get_object_or_404(
            SubtitleVideoVersionAsset.objects.select_related('version__project'),
            pk=asset_id,
            version__project__public_id=public_id,
        )
        return protected_file_response(request, asset.file)


class ProjectSubtitleReviewDetailView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, session_id):
        review = get_object_or_404(
            SubtitleReviewSession.objects.select_related('project', 'job').prefetch_related(
                'suggestions__cue__track',
            ),
            pk=session_id,
            project__public_id=public_id,
        )
        suggestions = list(review.suggestions.all())
        cue_ids = [item.cue_id for item in suggestions]
        conflicting_cues = set(
            SubtitleSuggestion.objects.filter(
                cue_id__in=cue_ids,
                session__project=review.project,
                status=SubtitleSuggestion.Status.PENDING,
            ).exclude(session=review).values_list('cue_id', flat=True)
        )
        for item in suggestions:
            item.has_other_suggestion = item.cue_id in conflicting_cues
        return render(request, 'member/external_media/subtitle_review_detail.html', {
            'project': review.project,
            'review': review,
            'suggestions': suggestions,
            'dirty_tracks': review.job.subtitle_tracks.filter(subtitle_dirty=True),
        })


class ProjectSubtitleSuggestionDecisionView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, session_id, suggestion_id):
        suggestion = get_object_or_404(
            SubtitleSuggestion.objects.select_related('session__project'),
            pk=suggestion_id,
            session_id=session_id,
            session__project__public_id=public_id,
        )
        action = request.POST.get('action')
        try:
            result = SubtitleReviewService.decide(
                suggestion,
                self.member,
                approve=action == 'approve',
                resolved_text=request.POST.get('resolved_text', ''),
                add_to_glossary=request.POST.get('add_to_glossary') == '1',
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            if result.status == SubtitleSuggestion.Status.CONFLICT:
                messages.warning(request, 'A legenda oficial mudou. Confira o conflito antes de aprovar.')
            else:
                messages.success(request, 'Decisão salva.')
        return redirect(
            'external_media_project_subtitle_review_detail',
            public_id=public_id,
            session_id=session_id,
        )


class ProjectSubtitleReviewApproveAllView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, session_id):
        review = get_object_or_404(
            SubtitleReviewSession, pk=session_id, project__public_id=public_id,
        )
        approved = conflicts = 0
        for suggestion in review.suggestions.filter(status=SubtitleSuggestion.Status.PENDING):
            result = SubtitleReviewService.decide(suggestion, self.member, approve=True)
            if result.status == SubtitleSuggestion.Status.CONFLICT:
                conflicts += 1
            else:
                approved += 1
        if conflicts:
            messages.warning(request, f'{approved} sugestões aprovadas e {conflicts} conflitos encontrados.')
        else:
            messages.success(request, f'{approved} sugestões aprovadas.')
        return redirect(
            'external_media_project_subtitle_review_detail',
            public_id=public_id,
            session_id=session_id,
        )


class ProjectSubtitleReviewRenderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, session_id=None):
        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update(),
                public_id=public_id,
            )
            review = get_object_or_404(
                SubtitleReviewSession, pk=session_id, project=project,
            ) if session_id else None
            if not project.render_job_id or not project.render_job.subtitle_tracks.filter(subtitle_dirty=True).exists():
                messages.info(request, 'Não há alterações aprovadas aguardando aplicação.')
                if review:
                    return redirect(
                        'external_media_project_subtitle_review_detail',
                        public_id=public_id,
                        session_id=session_id,
                    )
                return redirect('external_media_project_subtitle_reviews', public_id=public_id)
            task_id = str(uuid.uuid4())
            project.status = ExternalMediaProject.Status.PENDING
            project.progress = 84
            project.current_step = 'Atualização adicionada à fila'
            project.celery_task_id = task_id
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'celery_task_id', 'update_at',
            ])
            if review:
                SubtitleReviewService.log(review, 'RENDER_REQUESTED', self.member)
            transaction.on_commit(lambda: _enqueue_review_render(
                project.pk, review.pk if review else None, task_id,
            ))
        messages.success(request, 'Atualização do vídeo iniciada. Apenas as legendas serão renderizadas novamente.')
        return redirect('external_media_project_detail', public_id=public_id)


class PublicSubtitleReviewMixin:
    def get_review(self, token):
        review = SubtitleReviewService.from_token(token)
        if not review:
            raise Http404('Este link expirou ou foi revogado.')
        return review


@method_decorator(ensure_csrf_cookie, name='dispatch')
class PublicSubtitleReviewView(PublicSubtitleReviewMixin, View):
    def get(self, request, token):
        review = self.get_review(token)
        if not review.first_accessed_at:
            review.first_accessed_at = timezone.now()
            review.save(update_fields=['first_accessed_at', 'update_at'])
            SubtitleReviewService.log(review, 'LINK_ACCESSED')
        tracks = list(review.job.subtitle_tracks.filter(
            language__in=review.languages,
        ).prefetch_related('cues'))
        existing = {
            item.cue_id: item
            for item in review.suggestions.all()
        }
        cue_groups = []
        for track in tracks:
            cues = []
            for cue in track.cues.all():
                cue.saved_suggestion = existing.get(cue.pk)
                cues.append(cue)
            cue_groups.append((track, cues))
        caption_style = (
            review.job.subtitle_style
            if review.languages and review.languages[0] == review.job.original_language
            else review.job.translated_subtitle_style or review.job.subtitle_style
        )
        alignment = caption_style.alignment if caption_style else 2
        caption_classes = []
        if alignment in {1, 4, 7}:
            caption_classes.append('align-left')
        elif alignment in {3, 6, 9}:
            caption_classes.append('align-right')
        if alignment in {7, 8, 9}:
            caption_classes.append('align-top')
        elif alignment in {4, 5, 6}:
            caption_classes.append('align-middle')
        response = render(request, 'external_media/public_subtitle_review.html', {
            'review': review,
            'token': token,
            'cue_groups': cue_groups,
            'suggestion_count': len(existing),
            'caption_style': caption_style,
            'caption_classes': ' '.join(caption_classes),
        })
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class PublicSubtitleReviewAutosaveView(PublicSubtitleReviewMixin, View):
    def post(self, request, token):
        review = self.get_review(token)
        try:
            payload = json.loads(request.body or '{}')
            suggestion = SubtitleReviewService.autosave(
                review,
                payload.get('cue_id'),
                payload.get('suggested_text'),
                payload.get('comment', ''),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        return JsonResponse({
            'ok': True,
            'saved': bool(suggestion),
            'suggestion_count': review.suggestions.count(),
        })


class PublicSubtitleReviewSubmitView(PublicSubtitleReviewMixin, View):
    def post(self, request, token):
        review = self.get_review(token)
        try:
            SubtitleReviewService.submit(review, request.POST.get('general_comment', ''))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Revisão enviada com sucesso. Obrigado pela ajuda!')
        return redirect('public_subtitle_review', token=token)


class PublicSubtitleReviewPreviewView(PublicSubtitleReviewMixin, View):
    def get(self, request, token):
        review = self.get_review(token)
        if review.preview_file:
            response = protected_file_response(request, review.preview_file, force_stream=True)
            response['Cache-Control'] = 'no-store, private'
            return response
        assets = {
            asset.language: asset
            for asset in review.job.assets.filter(kind=MediaAsset.Kind.VIDEO)
        }
        asset = next(
            (assets[language] for language in review.languages if language in assets),
            assets.get(review.job.original_language),
        )
        if asset:
            response = protected_file_response(request, asset.file, force_stream=True)
            response['Cache-Control'] = 'no-store, private'
            return response
        raise Http404('A prévia ainda está sendo preparada.')
