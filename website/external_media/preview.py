from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid

from django.core.files import File
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from ..models.external_media import (
    ExternalMediaProject,
    MediaTemplatePlugin,
    PreviewSession,
    ProxyProfile,
    ProjectBlockMedia,
    ProjectSourceProxy,
    SubtitleCue,
    TimelineMutation,
    TimelineRevision,
)
from .canonical import EditDecisionSetBuilder, ProjectProcessingState, SourceManifestBuilder
from .exceptions import ExternalMediaError
from .services import StorageService, VideoAssemblyService
from .overlays import OverlayTimelineService
from .workspace import JobWorkspace


# The final interactive-review assembly is 30 fps. A manual cut may be as
# short as one rendered frame, which is important for trimming a take's tail.
MIN_MANUAL_CUT_MS = 33


class ProjectProxyService:
    @classmethod
    def prepare(cls, project):
        state = ProjectProcessingState(project)
        manifest = state.get_source_manifest() or SourceManifestBuilder.build(project, project.render_job_id)
        profile = project.template_version.preview_proxy_profile
        if not profile:
            profile, _ = ProxyProfile.objects.get_or_create(
                code='community-1',
                defaults={'name': 'Community 1', 'max_width': 960, 'fps': 30, 'video_crf': 27, 'is_default': True},
            )
        storage = StorageService()
        assembly = VideoAssemblyService(storage=storage)
        errors = []
        for source in manifest.get('sources') or []:
            field = SourceManifestBuilder.resolve_field(project, source)
            proxy, _ = ProjectSourceProxy.objects.get_or_create(
                project=project,
                source_id=source['id'],
                profile=profile,
                defaults={'source_storage_name': field.name},
            )
            if (
                proxy.status == ProjectSourceProxy.Status.READY
                and proxy.proxy_file
                and proxy.source_storage_name == field.name
            ):
                # A proxy is a persistent project asset; opening the review must not rebuild it.
                continue
            proxy.source_storage_name = field.name
            try:
                reference = cls._ready_upload_preview(project, source)
                if reference:
                    proxy.proxy_file.name = reference.preview_file.name
                    proxy.duration_ms = reference.duration_ms
                    if not proxy.duration_ms:
                        proxy.duration_ms = assembly._duration_ms(
                            storage.ffmpeg_input(reference.preview_file)
                        )
                    proxy.metadata = {'reused_upload_proxy': True, 'temporal_parity': 'trim_applied_at_playback'}
                else:
                    media_input = storage.input(field)
                    with JobWorkspace(
                        project.public_id,
                        'interactive-preview',
                        estimated_bytes=media_input.workspace_estimate(needs_proxy=True),
                    ) as workspace:
                        output = workspace.file('proxy', 'proxy.mp4')
                        assembly.create_proxy(media_input.get_ffmpeg_input(), output, profile=profile)
                        proxy.duration_ms = assembly._duration_ms(output)
                        with output.open('rb') as handle:
                            proxy.proxy_file.save('proxy.mp4', File(handle), save=False)
                        proxy.metadata = {
                            'reused_upload_proxy': False,
                            'profile': profile.code,
                            'temporal_parity': 'verified_by_duration',
                        }
                proxy.status = ProjectSourceProxy.Status.READY
                proxy.error_message = ''
                proxy.save()
            except Exception as exc:
                proxy.status = ProjectSourceProxy.Status.ERROR
                proxy.error_message = str(exc)[:255]
                proxy.save(update_fields=['status', 'error_message', 'source_storage_name', 'update_at'])
                errors.append(source.get('filename') or source['id'])
        if errors:
            raise ExternalMediaError(
                'Não foi possível preparar o preview de: ' + ', '.join(errors[:3]) + '.'
            )
        return manifest

    @staticmethod
    def _ready_upload_preview(project, source):
        if source.get('kind') not in {'upload', 'custom_upload'}:
            return None
        return ProjectBlockMedia.objects.filter(
            project=project,
            pk=source.get('source_reference'),
            preview_status=ProjectBlockMedia.PreviewStatus.READY,
        ).exclude(preview_file='').first()


class PreviewCapabilityRegistry:
    CUTS = 'CUTS'
    SUBTITLES = 'SUBTITLES'
    TRANSFORMS = 'TRANSFORMS'
    CAMERA_SWITCHES = 'CAMERA_SWITCHES'
    LAYOUTS = 'LAYOUTS'
    GRAPHICS = 'GRAPHICS'
    BROLL = 'BROLL'
    AUDIO_AUTOMATION = 'AUDIO_AUTOMATION'
    AUDIO_NOISE_CLEANUP = 'AUDIO_NOISE_CLEANUP'
    COLOR = 'COLOR'
    ALL = {
        CUTS, SUBTITLES, TRANSFORMS, CAMERA_SWITCHES, LAYOUTS,
        GRAPHICS, BROLL, AUDIO_AUTOMATION, AUDIO_NOISE_CLEANUP, COLOR,
    }

    @classmethod
    def resolve(cls, project):
        configured = project.template_version.preview_editable_capabilities or []
        declared = set(configured) & cls.ALL
        plugin_codes = set(project.template_version.plugins.filter(is_enabled=True).values_list('code', flat=True))
        available = {cls.CUTS}
        if project.template_version.subtitles_enabled:
            available.add(cls.SUBTITLES)
        if MediaTemplatePlugin.Code.AUTO_TRACKING in plugin_codes:
            available.add(cls.TRANSFORMS)
        if MediaTemplatePlugin.Code.MUSIC in plugin_codes:
            available.add(cls.AUDIO_AUTOMATION)
        if project.template_version.audio_noise_cleanup_enabled:
            available.add(cls.AUDIO_NOISE_CLEANUP)
        if MediaTemplatePlugin.Code.LUT in plugin_codes:
            available.add(cls.COLOR)
        available.update(declared)
        if project.template_version.blocks.exclude(overlay_definitions=[]).exists() or project.overlays.exists():
            available.add(cls.GRAPHICS)
        implemented_editable = {cls.CUTS, cls.SUBTITLES, cls.TRANSFORMS, cls.GRAPHICS}
        editable = [item for item in configured if item in available and item in implemented_editable]
        if cls.GRAPHICS in available and cls.GRAPHICS not in editable:
            editable.append(cls.GRAPHICS)
        return {
            'available': sorted(available),
            'editable': editable,
            'read_only': sorted(available - set(editable)),
        }


class PreviewCompositionService:
    """Composes browser playback data without introducing editorial decisions."""

    FIDELITY = {
        'CUTS': 'EXACT',
        'SUBTITLES': 'EXACT',
        'TRANSFORMS': 'APPROXIMATE',
        'CAMERA_SWITCHES': 'NOT_AVAILABLE',
        'LAYOUTS': 'NOT_AVAILABLE',
        'GRAPHICS': 'APPROXIMATE',
        'BROLL': 'NOT_AVAILABLE',
        'AUDIO_AUTOMATION': 'APPROXIMATE',
        'AUDIO_NOISE_CLEANUP': 'APPROXIMATE',
        'COLOR': 'APPROXIMATE',
    }

    @staticmethod
    def subtitle_style_payload(style):
        if not style:
            return {}
        return {
            'font': style.font_name, 'font_weight': style.font_weight,
            'font_size': style.font_size, 'color': style.primary_color,
            'primary_opacity': style.primary_opacity, 'alignment': style.alignment,
            'margin_bottom': style.margin_bottom, 'background_enabled': style.background_enabled,
            'background_color': style.background_color, 'background_opacity': style.background_opacity,
            'background_padding_x': style.background_padding_x, 'background_padding_y': style.background_padding_y,
            'background_radius': style.background_radius, 'outline_color': style.outline_color,
            'outline_width': style.outline_width, 'shadow': style.shadow,
            'shadow_angle': style.shadow_angle, 'shadow_size': style.shadow_size,
            'shadow_blur': style.shadow_blur, 'shadow_opacity': style.shadow_opacity,
        }

    @classmethod
    def compose(cls, project, source_manifest, decisions, revision, overlays_override=None):
        proxies = {
            item.source_id: item
            for item in project.source_proxies.filter(status=ProjectSourceProxy.Status.READY).select_related('profile')
        }
        operations = decisions.get('operations') or []
        cuts = sorted(
            (
                max(0, int(item.get('source_in_ms') or 0)),
                max(0, int(item.get('source_out_ms') or 0)),
            )
            for item in operations
            if item.get('type') == 'remove_segment' and item.get('enabled', True)
        )
        transforms = {
            item.get('source_id'): (item.get('metadata') or {}).get('manual_transform')
            for item in operations
            if item.get('type') == 'reframe' and item.get('enabled', True)
        }
        assets, clips, markers = [], [], []
        source_cursor = timeline_cursor = 0
        for source in source_manifest.get('sources') or []:
            if not (source.get('metadata') or {}).get('render_enabled', True):
                continue
            proxy = proxies.get(source['id'])
            trim = source.get('trim') or {}
            source_in = int(trim.get('start_ms') or 0)
            raw_duration = int(
                (proxy.duration_ms if proxy else None)
                or source.get('duration_ms')
                or (source.get('metadata') or {}).get('duration_ms')
                or source_in + 1
            )
            source_out = min(raw_duration, int(trim.get('end_ms') or raw_duration))
            effective_duration = max(1, source_out - source_in)
            global_start, global_end = source_cursor, source_cursor + effective_duration
            kept = cls._subtract(global_start, global_end, cuts)
            asset = {
                'id': source['id'],
                'name': source.get('filename') or source.get('block_name') or source['id'],
                'role': source.get('role') or 'main',
                'duration_ms': raw_duration,
                'status': proxy.status if proxy else ProjectSourceProxy.Status.PENDING,
                'url': reverse(
                    'external_media_project_preview_source',
                    kwargs={'public_id': project.public_id, 'source_id': source['id']},
                ) if proxy else None,
            }
            assets.append(asset)
            marker_start = timeline_cursor
            for keep_start, keep_end in kept:
                local_start = keep_start - global_start
                duration = keep_end - keep_start
                clips.append({
                    'id': f'clip-{len(clips) + 1}',
                    'asset_id': source['id'],
                    'timeline_in_ms': timeline_cursor,
                    'timeline_out_ms': timeline_cursor + duration,
                    'source_in_ms': source_in + local_start,
                    'source_out_ms': source_in + local_start + duration,
                    'block': {
                        'id': source.get('block_id'),
                        'key': source.get('block_key'),
                        'name': source.get('block_name'),
                    },
                    'skip_extra_processing': bool((source.get('metadata') or {}).get('skip_extra_processing')),
                    'effects': ([{'type': 'transform', **transforms[source['id']]}]
                                if transforms.get(source['id']) else []),
                })
                timeline_cursor += duration
            markers.append({
                'time_ms': marker_start,
                'name': source.get('block_name') or source.get('filename') or source['id'],
            })
            source_cursor = global_end

        captions = []
        if project.render_job_id:
            for cue in SubtitleCue.objects.filter(track__job_id=project.render_job_id).select_related('track').order_by(
                'track__language', 'start_ms', 'pk',
            ):
                captions.append({
                    'id': cue.pk,
                    'track_id': cue.track_id,
                    'language': cue.track.language,
                    'start_ms': cue.start_ms,
                    'end_ms': cue.end_ms,
                    'text': cue.text,
                    'is_source': cue.track.is_source,
                })
        capabilities = PreviewCapabilityRegistry.resolve(project)
        overlays = (
            deepcopy(overlays_override)
            if overlays_override is not None
            else OverlayTimelineService.compose(project, clips, timeline_cursor)
        )
        thresholds = project.template_version.preview_confidence_thresholds or {}
        flag_below = float(thresholds.get('flag_below') or 0.72)
        review_items = [
            item.get('id') for item in operations
            if item.get('type') in {'remove_segment', 'reframe', 'audio_noise_reduction'}
            and (
                (item.get('metadata') or {}).get('recommended_action') == 'REVIEW'
                or item.get('confidence') is None
                or float(item.get('confidence')) < flag_below
            )
        ]
        noise_preview_base = reverse('external_media_project_preview', kwargs={'public_id': project.public_id})
        for item in operations:
            if item.get('type') != 'audio_noise_reduction':
                continue
            metadata = item.setdefault('metadata', {})
            metadata['preview_original_url'] = reverse(
                'external_media_project_preview_noise_clip',
                kwargs={'public_id': project.public_id, 'decision_id': item.get('id')},
            ) + '?variant=original'
            metadata['preview_treated_url'] = reverse(
                'external_media_project_preview_noise_clip',
                kwargs={'public_id': project.public_id, 'decision_id': item.get('id')},
            ) + '?variant=treated'
        # The assembled source is the same normalized master used as input by the
        # final renderer.  Playing it in the review UI makes the automatically
        # calculated reframe (and LUT) faithful instead of trying to recreate it
        # with CSS on the individual camera proxies.
        review_master_url = None
        if project.render_job_id and project.render_job.original_video:
            review_master_url = reverse(
                'external_media_project_preview_master',
                kwargs={'public_id': project.public_id},
            )
        has_manual_transforms = any(
            item.get('type') == 'reframe'
            and item.get('enabled', True)
            and (item.get('metadata') or {}).get('manual_transform')
            for item in operations
        )
        fidelity = {key: cls.FIDELITY[key] for key in capabilities['available']}
        if review_master_url and not has_manual_transforms and 'TRANSFORMS' in fidelity:
            fidelity['TRANSFORMS'] = 'EXACT'
        return {
            'schema': 'connect.internal_timeline.v1',
            'timeline_revision': revision,
            'project': {'id': str(project.public_id), 'name': project.name},
            'sequence': {
                'width': project.template_version.preset.width or 1920,
                'height': project.template_version.preset.height or 1080,
                # The final assembly is rendered at 30 fps. RenderPreset only
                # describes the output dimensions/codecs, not a frame rate.
                'fps': 30,
                'duration_ms': timeline_cursor,
            },
            'assets': assets,
            'review_master_url': review_master_url,
            'has_music': bool(
                project.template_version.background_music_id or project.template_version.music_file
            ),
            'clips': clips,
            'video_tracks': [
                {'id': 'V1', 'role': 'main', 'clips': clips},
                {'id': 'V2', 'role': 'overlay', 'clips': overlays},
            ],
            'overlay_tracks': [{'id': 'OVERLAYS', 'role': 'overlay', 'clips': overlays}],
            'overlays': overlays,
            'captions': captions,
            'caption_styles': {
                'source': cls.subtitle_style_payload(getattr(project.render_job, 'subtitle_style', None))
                if project.render_job_id else {},
                'translated': cls.subtitle_style_payload(getattr(project.render_job, 'translated_subtitle_style', None))
                if project.render_job_id else {},
                'source_language': project.template_version.original_language,
            },
            'markers': markers,
            'decisions': operations,
            'review_items': review_items,
            'capabilities': capabilities,
            'fidelity': {key: cls.FIDELITY[key] for key in capabilities['available']},
        }

    @staticmethod
    def _subtract(start, end, cuts):
        cursor, kept = start, []
        for cut_start, cut_end in cuts:
            if cut_end <= cursor or cut_start >= end:
                continue
            if cut_start > cursor:
                kept.append((cursor, min(cut_start, end)))
            cursor = max(cursor, min(cut_end, end))
            if cursor >= end:
                break
        if cursor < end:
            kept.append((cursor, end))
        return [(left, right) for left, right in kept if right > left]


class TimelineRevisionService:
    @classmethod
    @transaction.atomic
    def ensure_initial(cls, project, member=None):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        if locked.current_timeline_revision_id:
            return locked.current_timeline_revision
        state = ProjectProcessingState(locked)
        manifest = state.get_source_manifest() or SourceManifestBuilder.build(locked, locked.render_job_id)
        decisions = state.get_edit_decision_set() or EditDecisionSetBuilder.build(locked, manifest, locked.render_job_id)
        return cls._create(locked, manifest, decisions, 'Análise inicial', member)

    @classmethod
    @transaction.atomic
    def mutate_decision(cls, project, member, decision_id, enabled):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        decisions = deepcopy(current.edit_decision_set)
        target = next((item for item in decisions.get('operations', []) if item.get('id') == decision_id), None)
        if not target:
            raise ValueError('Decisão não encontrada nesta timeline.')
        previous = target.get('enabled', True)
        requested = bool(enabled)
        if previous == requested:
            return current
        if previous != requested and target.get('type') == 'remove_segment':
            cls._shift_cues_for_decision(locked, decisions, target, enabling=requested)
        target['enabled'] = requested
        target['review'] = {'member_id': member.pk, 'reviewed_at': timezone.now().isoformat()}
        revision = cls._create(locked, current.source_manifest, decisions, 'Revisão de corte', member, current)
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'SET_DECISION_ENABLED', {
            'decision_id': decision_id, 'enabled': bool(enabled),
        }, {'decision_id': decision_id, 'enabled': previous}, member)
        return revision

    @classmethod
    @transaction.atomic
    def create_manual_cut(cls, project, member, start_ms, end_ms):
        """Add a user-selected removal in original-master time coordinates."""
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        start, end = max(0, int(start_ms)), max(0, int(end_ms))
        if end - start < MIN_MANUAL_CUT_MS:
            raise ValueError('Selecione um trecho de pelo menos um quadro.')
        decisions = deepcopy(current.edit_decision_set)
        operations = decisions.setdefault('operations', [])
        for item in operations:
            if item.get('type') != 'remove_segment' or not item.get('enabled', True):
                continue
            other_start, other_end = int(item.get('source_in_ms') or 0), int(item.get('source_out_ms') or 0)
            if start < other_end and end > other_start:
                raise ValueError('O trecho escolhido se sobrepõe a um corte já existente.')
        target = {
            'id': f'user-cut-{uuid.uuid4().hex[:12]}', 'type': 'remove_segment',
            'source_id': 'project-master', 'source_in_ms': start, 'source_out_ms': end,
            'origin': 'USER', 'producer': 'member_review', 'producer_version': '1',
            'reason': 'Corte manual', 'confidence': 1.0, 'enabled': True,
            'metadata': {'kind': 'manual', 'recommended_action': 'KEEP'},
        }
        operations.append(target)
        cls._shift_cues_for_decision(locked, decisions, target, enabling=True)
        revision = cls._create(locked, current.source_manifest, decisions, 'Corte manual adicionado', member, current)
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'CREATE_MANUAL_CUT', {
            'decision_id': target['id'], 'source_in_ms': start, 'source_out_ms': end,
        }, {'decision_id': target['id']}, member)
        return revision

    @classmethod
    @transaction.atomic
    def update_subtitle(cls, project, member, cue_id, text):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        cue = SubtitleCue.objects.select_for_update().get(pk=cue_id, track__job=locked.render_job)
        previous = cue.text
        cue.text = text.strip()
        if cue.text == previous:
            return locked.current_timeline_revision or cls.ensure_initial(locked, member)
        cue.save(update_fields=['text', 'update_at'])
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set, 'Legenda atualizada', member, current,
        )
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'UPDATE_SUBTITLE', {
            'cue_id': cue_id, 'text': cue.text,
        }, {'cue_id': cue_id, 'text': previous}, member)
        return revision

    @classmethod
    @transaction.atomic
    def update_transform(cls, project, member, decision_id, values):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        decisions = deepcopy(current.edit_decision_set)
        target = next((item for item in decisions.get('operations', []) if item.get('id') == decision_id), None)
        if not target or target.get('type') != 'reframe':
            raise ValueError('Enquadramento não encontrado nesta timeline.')
        previous = deepcopy((target.get('metadata') or {}).get('manual_transform'))
        transform = {
            'scale': min(2.0, max(1.0, float(values.get('scale') or 1))),
            'x': min(1.0, max(-1.0, float(values.get('x') or 0))),
            'y': min(1.0, max(-1.0, float(values.get('y') or 0))),
        }
        if previous == transform:
            return current
        target.setdefault('metadata', {})['manual_transform'] = transform
        target['origin'] = 'USER'
        revision = cls._create(locked, current.source_manifest, decisions, 'Enquadramento ajustado', member, current)
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'UPDATE_TRANSFORM', {
            'decision_id': decision_id, **transform,
        }, {'decision_id': decision_id, 'manual_transform': previous}, member)
        return revision

    @classmethod
    @transaction.atomic
    def mutate_overlay(cls, project, member, overlay_id, payload, *, create=False, delete=False):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        overlays = deepcopy(current.timeline.get('overlays') or [])
        target = next((item for item in overlays if item.get('id') == overlay_id), None)
        previous = deepcopy(target)
        if delete:
            if not target:
                raise ValueError('Overlay não encontrado nesta timeline.')
            overlays.remove(target)
            reason, operation = 'Overlay removido', 'DELETE_OVERLAY'
        elif create:
            if target:
                raise ValueError('Já existe um overlay com este identificador.')
            overlay_type = str(payload.get('type') or 'TEXT').upper()
            if overlay_type not in {'TEXT', 'QR_CODE', 'QR_CODE_CARD', 'IMAGE'}:
                raise ValueError('Tipo de overlay não suportado.')
            target = {
                'id': overlay_id, 'type': overlay_type, 'purpose': payload.get('purpose', ''),
                'start_ms': max(0, int(payload.get('start_ms') or 0)),
                'end_ms': max(1, int(payload.get('end_ms') or min(current.timeline['sequence']['duration_ms'], 5000))),
                'position': payload.get('position') or {'x': .5, 'y': .82, 'width': .25},
                'style': payload.get('style') or {},
                'animation': payload.get('animation') or {'type': 'FADE', 'duration': .35, 'easing': 'ease-out'},
                'content': payload.get('content') or {}, 'content_schema': {},
                'allowed_overrides': ['content', 'position', 'style', 'animation', 'timing'],
                'source': 'MANUAL', 'block_id': None, 'preset': payload.get('preset'),
                'portability': 'APPROXIMATE',
            }
            overlays.append(target)
            reason, operation = 'Overlay adicionado', 'CREATE_OVERLAY'
        else:
            if not target:
                raise ValueError('Overlay não encontrado nesta timeline.')
            allowed = set(target.get('allowed_overrides') or [])
            for key in ('content', 'position', 'style', 'animation'):
                if key in payload and (key == 'content' or key in allowed):
                    target[key] = deepcopy(payload[key])
            if 'timing' in allowed:
                if 'start_ms' in payload:
                    target['start_ms'] = max(0, int(payload['start_ms']))
                if 'end_ms' in payload:
                    target['end_ms'] = max(target['start_ms'] + 1, int(payload['end_ms']))
            target['end_ms'] = min(target['end_ms'], current.timeline['sequence']['duration_ms'])
            reason, operation = 'Overlay atualizado', 'UPDATE_OVERLAY'
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set, reason, member, current,
            overlays=overlays,
        )
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, operation, {
            'overlay_id': overlay_id, 'overlay': deepcopy(target),
        }, {'overlay_id': overlay_id, 'overlay': previous}, member)
        return revision

    @classmethod
    def session(cls, project, member, revision=None):
        session, _ = PreviewSession.objects.get_or_create(project=project, member=member)
        if revision and session.current_revision_id != revision.pk:
            session.current_revision = revision
            session.last_seen_at = timezone.now()
            session.save(update_fields=['current_revision', 'last_seen_at', 'update_at'])
        return session

    @staticmethod
    def history_state(project, member):
        """Return the availability of per-edit undo/redo for this member."""
        session = PreviewSession.objects.filter(project=project, member=member).only(
            'undo_stack', 'redo_stack',
        ).first()
        return {
            'can_undo': bool(session and session.undo_stack),
            'can_redo': bool(session and session.redo_stack),
        }

    @classmethod
    @transaction.atomic
    def navigate_history(cls, project, member, direction):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        session = cls.session(locked, member, locked.current_timeline_revision)
        source = list(session.undo_stack if direction == 'undo' else session.redo_stack)
        if not source:
            return locked.current_timeline_revision
        revision_id = source.pop()
        target = TimelineRevision.objects.get(pk=revision_id, project=locked)
        opposite = list(session.redo_stack if direction == 'undo' else session.undo_stack)
        if locked.current_timeline_revision_id:
            opposite.append(locked.current_timeline_revision_id)
        if direction == 'undo':
            session.undo_stack, session.redo_stack = source, opposite
        else:
            session.redo_stack, session.undo_stack = source, opposite
        session.current_revision = target
        session.save(update_fields=['undo_stack', 'redo_stack', 'current_revision', 'update_at'])
        locked.current_timeline_revision = target
        locked.preview_dirty = True
        locked.final_render_outdated = True
        locked.save(update_fields=['current_timeline_revision', 'preview_dirty', 'final_render_outdated', 'update_at'])
        cls._sync_cues_from_revision(locked, target)
        return target

    @classmethod
    @transaction.atomic
    def approve(cls, project, member):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        revision = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        revision.approved_at = timezone.now()
        revision.save(update_fields=['approved_at', 'update_at'])
        locked.approved_timeline_revision = revision
        locked.preview_dirty = False
        locked.final_render_outdated = True
        locked.save(update_fields=[
            'approved_timeline_revision', 'preview_dirty', 'final_render_outdated', 'update_at',
        ])
        return revision

    @classmethod
    def _create(cls, project, manifest, decisions, reason, member, parent=None, overlays=None):
        number = (project.timeline_revisions.aggregate(value=Max('revision'))['value'] or 0) + 1
        # Overlay edits live in the revision itself. Preserve them when another
        # kind of edit creates a child revision, otherwise template overlays
        # would silently return and manual overlays would disappear.
        if overlays is None and parent:
            overlays = deepcopy(parent.timeline.get('overlays') or [])
        timeline = PreviewCompositionService.compose(project, manifest, decisions, number, overlays_override=overlays)
        revision = TimelineRevision.objects.create(
            project=project, revision=number, parent=parent, timeline=timeline,
            source_manifest=manifest, edit_decision_set=decisions, reason=reason, created_by=member,
        )
        project.current_timeline_revision = revision
        project.preview_dirty = bool(parent)
        project.final_render_outdated = bool(parent)
        project.save(update_fields=[
            'current_timeline_revision', 'preview_dirty', 'final_render_outdated', 'update_at',
        ])
        return revision

    @classmethod
    def _record(cls, session, previous, revision, operation_type, payload, inverse_payload, member):
        TimelineMutation.objects.create(
            project=revision.project, session=session, from_revision=previous, to_revision=revision,
            operation_type=operation_type, payload=payload, inverse_payload=inverse_payload, created_by=member,
        )
        undo = list(session.undo_stack)
        undo.append(previous.pk)
        session.undo_stack = undo[-50:]
        session.redo_stack = []
        session.current_revision = revision
        session.save(update_fields=['undo_stack', 'redo_stack', 'current_revision', 'update_at'])

    @staticmethod
    def _shift_cues_for_decision(project, decisions, target, enabling):
        duration = max(0, int(target.get('source_out_ms') or 0) - int(target.get('source_in_ms') or 0))
        if not duration or not project.render_job_id:
            return
        other_cuts = sorted([
            item for item in decisions.get('operations', [])
            if item.get('type') == 'remove_segment'
            and item.get('id') != target.get('id')
            and item.get('enabled', True)
        ], key=lambda item: int(item.get('source_in_ms') or 0))
        source_start = int(target.get('source_in_ms') or 0)
        removed_before = sum(
            max(0, min(source_start, int(item.get('source_out_ms') or 0)) - int(item.get('source_in_ms') or 0))
            for item in other_cuts
            if int(item.get('source_in_ms') or 0) < source_start
        )
        timeline_point = max(0, source_start - removed_before)
        delta = -duration if enabling else duration
        cues = SubtitleCue.objects.filter(track__job=project.render_job, start_ms__gte=timeline_point)
        for cue in cues:
            cue.start_ms = max(0, cue.start_ms + delta)
            cue.end_ms = max(cue.start_ms + 1, cue.end_ms + delta)
        SubtitleCue.objects.bulk_update(cues, ['start_ms', 'end_ms'])

    @staticmethod
    def _sync_cues_from_revision(project, revision):
        if not project.render_job_id:
            return
        by_id = {int(item['id']): item for item in revision.timeline.get('captions') or []}
        cues = list(SubtitleCue.objects.filter(track__job=project.render_job, pk__in=by_id))
        for cue in cues:
            item = by_id[cue.pk]
            cue.start_ms = int(item['start_ms'])
            cue.end_ms = int(item['end_ms'])
            cue.text = item['text']
        SubtitleCue.objects.bulk_update(cues, ['start_ms', 'end_ms', 'text'])
