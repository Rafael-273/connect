import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from safedelete.models import HARD_DELETE

from ..models.external_media import (
    GlossaryTerm,
    SubtitleReviewEvent,
    SubtitleReviewSession,
    SubtitleRevision,
    SubtitleSuggestion,
    SubtitleTrack,
)


class SubtitleReviewService:
    TOKEN_BYTES = 48

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @classmethod
    def create_session(
        cls, project, member, languages, validity_days=30, expires_at=None,
        reviewer_name='', reviewer_contact='',
    ):
        tracks = list(project.render_job.subtitle_tracks.filter(language__in=languages))
        if not tracks:
            raise ValueError('Nenhuma legenda disponível para os idiomas selecionados.')
        token = secrets.token_urlsafe(cls.TOKEN_BYTES)
        expiry = expires_at or (timezone.now() + timedelta(days=validity_days))
        session = SubtitleReviewSession.objects.create(
            project=project,
            job=project.render_job,
            token_hash=cls.hash_token(token),
            token_prefix=token[:12],
            languages=[track.language for track in tracks],
            base_revisions={track.language: track.revision for track in tracks},
            reviewer_name=(reviewer_name or '').strip(),
            reviewer_contact=(reviewer_contact or '').strip(),
            expires_at=expiry,
            created_by=member,
        )
        for track in tracks:
            cls.ensure_revision_snapshot(track, member=member, reason='Link de revisão criado')
        cls.log(session, 'LINK_CREATED', member, {'languages': session.languages})
        return session, token

    @classmethod
    def from_token(cls, token, include_inactive=False):
        session = SubtitleReviewSession.objects.select_related(
            'project', 'job', 'created_by',
        ).filter(token_hash=cls.hash_token(token)).first()
        if not session:
            return None
        if not include_inactive and (session.revoked_at or session.expires_at <= timezone.now()):
            return None
        return session

    @staticmethod
    def ensure_revision_snapshot(track, member=None, reason='', session=None):
        revision, _ = SubtitleRevision.objects.get_or_create(
            track=track,
            revision=track.revision,
            defaults={
                'cues_snapshot': list(track.cues.values(
                    'id', 'cue_index', 'start_ms', 'end_ms', 'text',
                )),
                'reason': reason,
                'created_by': member,
                'review_session': session,
            },
        )
        return revision

    @staticmethod
    def log(session, event, actor=None, details=None):
        return SubtitleReviewEvent.objects.create(
            session=session, event=event, actor=actor, details=details or {},
        )

    @classmethod
    def autosave(cls, session, cue_id, suggested_text, comment=''):
        if session.status != SubtitleReviewSession.Status.DRAFT:
            raise ValueError('Esta revisão já foi enviada e não pode mais ser alterada.')
        cue = session.job.subtitle_tracks.filter(
            language__in=session.languages,
            cues__pk=cue_id,
        ).values_list('cues__pk', flat=True).first()
        if not cue:
            raise ValueError('Legenda não disponível nesta revisão.')
        from ..models.external_media import SubtitleCue
        cue = SubtitleCue.objects.select_related('track').get(pk=cue)
        text = (suggested_text or '').strip()
        comment = (comment or '').strip()
        if not text:
            raise ValueError('A legenda não pode ficar vazia.')
        existing = SubtitleSuggestion.objects.filter(session=session, cue=cue).first()
        if text == cue.text and not comment:
            if existing:
                existing.delete(force_policy=HARD_DELETE)
            return None
        suggestion, created = SubtitleSuggestion.objects.update_or_create(
            session=session,
            cue=cue,
            defaults={
                'language': cue.track.language,
                'base_track_revision': session.base_revisions.get(
                    cue.track.language, cue.track.revision,
                ),
                'original_text': existing.original_text if existing else cue.text,
                'suggested_text': text or cue.text,
                'comment': comment,
                'status': SubtitleSuggestion.Status.PENDING,
            },
        )
        cls.log(
            session,
            'SUGGESTION_CREATED' if created else 'SUGGESTION_UPDATED',
            details={'cue_id': cue.pk, 'language': cue.track.language},
        )
        return suggestion

    @classmethod
    def submit(cls, session, general_comment=''):
        if session.status != SubtitleReviewSession.Status.DRAFT:
            raise ValueError('Esta revisão já foi enviada.')
        session.status = SubtitleReviewSession.Status.SUBMITTED
        session.general_comment = (general_comment or '').strip()
        session.submitted_at = timezone.now()
        session.save(update_fields=['status', 'general_comment', 'submitted_at', 'update_at'])
        cls.log(session, 'REVIEW_SUBMITTED', details={'suggestions': session.suggestions.count()})
        return session

    @classmethod
    @transaction.atomic
    def decide(cls, suggestion, member, approve, resolved_text='', add_to_glossary=False):
        suggestion = SubtitleSuggestion.objects.select_for_update().select_related(
            'cue__track__job', 'session',
        ).get(pk=suggestion.pk)
        if suggestion.status not in {
            SubtitleSuggestion.Status.PENDING,
            SubtitleSuggestion.Status.CONFLICT,
        }:
            return suggestion
        now = timezone.now()
        if not approve:
            suggestion.status = SubtitleSuggestion.Status.REJECTED
            suggestion.reviewed_by = member
            suggestion.reviewed_at = now
            suggestion.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'update_at'])
            cls.log(suggestion.session, 'SUGGESTION_REJECTED', member, {'suggestion_id': suggestion.pk})
            cls.refresh_session_status(suggestion.session)
            return suggestion

        cue = suggestion.cue
        chosen_text = (resolved_text or suggestion.suggested_text).strip()
        if not chosen_text:
            raise ValueError('A legenda aprovada não pode ficar vazia.')
        if cue.text != suggestion.original_text and suggestion.status != SubtitleSuggestion.Status.CONFLICT:
            suggestion.status = SubtitleSuggestion.Status.CONFLICT
            suggestion.save(update_fields=['status', 'update_at'])
            cls.log(suggestion.session, 'CONFLICT_DETECTED', member, {'suggestion_id': suggestion.pk})
            return suggestion

        track = cue.track
        cue.text = chosen_text
        cue.save(update_fields=['text', 'update_at'])
        track.revision += 1
        track.human_reviewed = True
        track.subtitle_dirty = True
        track.translation_status = SubtitleTrack.TranslationStatus.CURRENT
        track.save(update_fields=[
            'revision', 'human_reviewed', 'subtitle_dirty', 'translation_status', 'update_at',
        ])
        if track.is_source:
            SubtitleTrack.objects.filter(job=track.job).exclude(pk=track.pk).update(
                translation_status=SubtitleTrack.TranslationStatus.SOURCE_CHANGED,
            )
        suggestion.status = SubtitleSuggestion.Status.APPROVED
        suggestion.resolved_text = chosen_text
        suggestion.reviewed_by = member
        suggestion.reviewed_at = now
        suggestion.save(update_fields=[
            'status', 'resolved_text', 'reviewed_by', 'reviewed_at', 'update_at',
        ])
        cls.ensure_revision_snapshot(
            track, member=member, reason='Sugestão de revisão aprovada', session=suggestion.session,
        )
        if add_to_glossary:
            cls.add_to_glossary(suggestion, chosen_text)
            cls.log(suggestion.session, 'GLOSSARY_UPDATED', member, {'suggestion_id': suggestion.pk})
        cls.log(suggestion.session, 'SUGGESTION_APPROVED', member, {'suggestion_id': suggestion.pk})
        cls.refresh_session_status(suggestion.session)
        return suggestion

    @staticmethod
    def add_to_glossary(suggestion, chosen_text):
        job = suggestion.session.job
        source_track = job.subtitle_tracks.filter(is_source=True).first()
        if not source_track:
            return None
        source_cue = source_track.cues.filter(cue_index=suggestion.cue.cue_index).first()
        if not source_cue or source_track.language == suggestion.language:
            return None
        term, _ = GlossaryTerm.objects.update_or_create(
            source_language=source_track.language,
            target_language=suggestion.language,
            source_text=source_cue.text[:255],
            defaults={'translated_text': chosen_text[:255], 'is_active': True},
        )
        return term

    @staticmethod
    def refresh_session_status(session):
        statuses = set(session.suggestions.values_list('status', flat=True))
        pending = {
            SubtitleSuggestion.Status.PENDING,
            SubtitleSuggestion.Status.CONFLICT,
        }
        if statuses & pending:
            status = SubtitleReviewSession.Status.UNDER_REVIEW
        elif statuses == {SubtitleSuggestion.Status.APPROVED}:
            status = SubtitleReviewSession.Status.APPROVED
        elif statuses == {SubtitleSuggestion.Status.REJECTED}:
            status = SubtitleReviewSession.Status.REJECTED
        else:
            status = SubtitleReviewSession.Status.PARTIALLY_APPROVED
        SubtitleReviewSession.objects.filter(pk=session.pk).update(status=status)
        session.status = status
        return session

    @staticmethod
    def next_video_version(project):
        from ..models.external_media import SubtitleVideoVersion
        current = SubtitleVideoVersion.objects.filter(project=project).aggregate(
            highest=Max('version'),
        )['highest'] or 0
        return current + 1
