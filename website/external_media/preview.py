from copy import deepcopy
from datetime import timedelta
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import uuid

from django.core.files import File
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from ..models.external_media import (
    ExternalMediaProject,
    MediaTemplatePlugin,
    PreviewSession,
    ProxyProfile,
    ProjectBrollAsset,
    ProjectSourceProxy,
    SubtitleCue,
    TimelineMutation,
    TimelineRevision,
)
from .canonical import EditDecisionSetBuilder, ProjectProcessingState, SourceManifestBuilder
from .exceptions import ExternalMediaError
from .services import StorageService, VideoAssemblyService
from .quality_control import MediaQualityService
from .overlays import OverlayTimelineService
from .broll import BrollTimelineService
from .workspace import JobWorkspace

logger = logging.getLogger(__name__)


# The final interactive-review assembly is 30 fps. A manual cut may be as
# short as one rendered frame, which is important for trimming a take's tail.
MIN_MANUAL_CUT_MS = 33

# Before this version, a phone video carrying a 90°/270° display rotation
# could have its proxy calculated from its encoded (landscape) dimensions.
# FFmpeg then rotated the frame and scaled it into that landscape geometry,
# permanently stretching the people in the browser preview.  Keep this value
# with the proxy rather than trusting an old READY status, so projects opened
# after the fix transparently get one correctly shaped source proxy.
# Version 3 also rebuilds phone MOV proxies with their audio timestamps rebased
# to the video clock. Older cached proxies can retain a delayed AAC start PTS.
SOURCE_PROXY_GEOMETRY_VERSION = 4
REVIEW_MASTER_PROXY_ID = '__review-master__'
TIMELINE_REVIEW_PROXY_PREFIX = '__timeline-review-r'
TIMELINE_REVIEW_PROXY_LEASE = timedelta(minutes=15)


class ProjectProxyService:
    @staticmethod
    def timeline_review_proxy_id(revision):
        return f'{TIMELINE_REVIEW_PROXY_PREFIX}{revision.pk}__'

    @classmethod
    def has_usable_timeline_review_proxy(cls, proxy, revision):
        metadata = (proxy.metadata or {}) if proxy else {}
        return bool(
            proxy
            and proxy.status == ProjectSourceProxy.Status.READY
            and proxy.proxy_file
            and metadata.get('timeline_revision_id') == revision.pk
        )

    @classmethod
    def _claim_timeline_review_proxy(cls, project, revision, profile):
        """Claim one revision proxy render without holding a DB lock for FFmpeg.

        The lease is persisted with the proxy so separate Celery workers (and
        not just separate HTTP requests) agree on the single active render.
        A stale lease is safely recovered after a worker crash.
        """
        with transaction.atomic():
            proxy, _ = ProjectSourceProxy.objects.get_or_create(
                project=project,
                source_id=cls.timeline_review_proxy_id(revision),
                profile=profile,
                defaults={'source_storage_name': project.render_job.original_video.name},
            )
            proxy = ProjectSourceProxy.objects.select_for_update().get(pk=proxy.pk)
            if cls.has_usable_timeline_review_proxy(proxy, revision):
                return proxy, None
            metadata = dict(proxy.metadata or {})
            active_lease = metadata.get('generation_lease')
            if (
                proxy.status == ProjectSourceProxy.Status.PENDING
                and active_lease
                and timezone.now() - proxy.update_at < TIMELINE_REVIEW_PROXY_LEASE
            ):
                return proxy, None
            lease = uuid.uuid4().hex
            metadata['generation_lease'] = lease
            proxy.status = ProjectSourceProxy.Status.PENDING
            proxy.error_message = ''
            proxy.source_storage_name = project.render_job.original_video.name
            proxy.metadata = metadata
            proxy.save(update_fields=[
                'status', 'error_message', 'source_storage_name', 'metadata', 'update_at',
            ])
            return proxy, lease

    @classmethod
    def _profile(cls, project):
        profile = project.template_version.preview_proxy_profile
        if not profile:
            profile, _ = ProxyProfile.objects.get_or_create(
                code='community-1',
                defaults={'name': 'Community 1', 'max_width': 960, 'fps': 30, 'video_crf': 27, 'is_default': True},
            )
        return profile

    @classmethod
    def prepare(cls, project):
        state = ProjectProcessingState(project)
        manifest = state.get_source_manifest() or SourceManifestBuilder.build(project, project.render_job_id)
        profile = cls._profile(project)
        storage = StorageService()
        assembly = VideoAssemblyService(storage=storage)
        quality = MediaQualityService(assembly.runner)
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
                and (proxy.metadata or {}).get('geometry_version') == SOURCE_PROXY_GEOMETRY_VERSION
            ):
                # A proxy is a persistent project asset; opening the review must not rebuild it.
                continue
            proxy.source_storage_name = field.name
            try:
                # Do not reuse ProjectBlockMedia.preview_file here.  Those files
                # predate the rotation-aware proxy geometry and are exactly what
                # would make the "Voltar ao original" preview look stretched.
                # This small per-source asset is generated once and then cached
                # with the geometry version above.
                media_input = storage.input(field)
                with JobWorkspace(
                    project.public_id,
                    'interactive-preview',
                    estimated_bytes=media_input.workspace_estimate(needs_proxy=True),
                ) as workspace:
                    output = workspace.file('proxy', 'proxy.mp4')
                    input_source = media_input.get_ffmpeg_input()
                    assembly.create_proxy(input_source, output, profile=profile)
                    if assembly._has_audio(input_source):
                        quality.validate_preview_proxy(
                            output, expected_duration_ms=assembly._duration_ms(input_source),
                        ).require_ok()
                    proxy.duration_ms = assembly._duration_ms(output)
                    with output.open('rb') as handle:
                        proxy.proxy_file.save('proxy.mp4', File(handle), save=False)
                    proxy.metadata = {
                        'geometry_version': SOURCE_PROXY_GEOMETRY_VERSION,
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
        # A single lightweight version of the already assembled review master
        # gives the browser continuous playback without making it download the
        # delivery-quality source on first open.  It deliberately lives beside
        # (rather than replaces) the per-take proxies: manual framing still
        # needs those sources until a revision-specific compositor is ready.
        job = project.render_job
        if job and job.original_video:
            master_proxy, _ = ProjectSourceProxy.objects.get_or_create(
                project=project,
                source_id=REVIEW_MASTER_PROXY_ID,
                profile=profile,
                defaults={'source_storage_name': job.original_video.name},
            )
            if not (
                master_proxy.status == ProjectSourceProxy.Status.READY
                and master_proxy.proxy_file
                and master_proxy.source_storage_name == job.original_video.name
                and (master_proxy.metadata or {}).get('geometry_version') == SOURCE_PROXY_GEOMETRY_VERSION
            ):
                master_proxy.source_storage_name = job.original_video.name
                try:
                    media_input = storage.input(job.original_video)
                    with JobWorkspace(
                        project.public_id,
                        'continuous-review-preview',
                        estimated_bytes=media_input.workspace_estimate(needs_proxy=True),
                    ) as workspace:
                        output = workspace.file('review-master', 'proxy.mp4')
                        input_source = media_input.get_ffmpeg_input()
                        assembly.create_proxy(input_source, output, profile=profile)
                        if assembly._has_audio(input_source):
                            quality.validate_preview_proxy(
                                output, expected_duration_ms=assembly._duration_ms(input_source),
                            ).require_ok()
                        master_proxy.duration_ms = assembly._duration_ms(output)
                        with output.open('rb') as handle:
                            master_proxy.proxy_file.save('review-master.mp4', File(handle), save=False)
                    master_proxy.metadata = {
                        'geometry_version': SOURCE_PROXY_GEOMETRY_VERSION,
                        'profile': profile.code,
                        'kind': 'continuous_review_master',
                        'temporal_parity': 'verified_by_duration',
                    }
                    master_proxy.status = ProjectSourceProxy.Status.READY
                    master_proxy.error_message = ''
                    master_proxy.save()
                except Exception as exc:
                    # A missing convenience proxy must never block an editor
                    # that can still fall back to the assembled master.
                    master_proxy.status = ProjectSourceProxy.Status.ERROR
                    master_proxy.error_message = str(exc)[:255]
                    master_proxy.save(update_fields=['status', 'error_message', 'source_storage_name', 'update_at'])
        if errors:
            raise ExternalMediaError(
                'Não foi possível preparar o preview de: ' + ', '.join(errors[:3]) + '.'
            )
        return manifest

    @classmethod
    def build_timeline_review_proxy(cls, project_id, revision_id):
        """Render one lightweight, continuous source for a timeline revision.

        This is a delivery-preview, not merely a transport proxy: its main
        video, B-roll, overlays and ASS subtitles are rendered server-side
        from the immutable revision. The browser must not draw those layers a
        second time once this asset is ready.
        """
        project = ExternalMediaProject.objects.select_related(
            'template_version__preset', 'template_version__preview_proxy_profile', 'render_job',
        ).get(pk=project_id)
        revision = TimelineRevision.objects.get(pk=revision_id, project=project)
        job = project.render_job
        if not job or not job.original_video:
            return None
        profile = cls._profile(project)
        proxy, lease = cls._claim_timeline_review_proxy(project, revision, profile)
        if lease is None:
            return proxy
        storage = StorageService()
        assembly = VideoAssemblyService(storage=storage)
        quality = MediaQualityService(assembly.runner)
        try:
            source = storage.input(job.original_video).get_ffmpeg_input()
            source_width, source_height = assembly._video_dimensions(source)
            # A manual crop uses even rounding. Without restoring every part
            # to one canvas before concat, adjacent pieces can differ by a few
            # pixels (for example 850x262 vs 854x266) and FFmpeg rejects the
            # entire review proxy.
            filters, segment_count = cls._timeline_review_filters(
                revision, assembly, concat_width=source_width, concat_height=source_height,
            )
            if not segment_count:
                raise ExternalMediaError('A revisão não possui trechos de vídeo para o preview contínuo.')
            expected_duration_ms = cls._timeline_review_duration_ms(revision)
            # Use the delivery canvas. Rendering a smaller proxy and then
            # asking libass/Pillow/FFmpeg to infer positions at browser size
            # is precisely what caused the preview to drift from delivery.
            preset = job.preset or project.template_version.preset
            proxy_width = int(getattr(preset, 'width', 0) or source_width)
            proxy_height = int(getattr(preset, 'height', 0) or source_height)
            filter_graph = ';'.join(filters + [
                f'[joinedv]scale={proxy_width}:{proxy_height}:flags=fast_bilinear,fps={int(getattr(profile, "fps", 0) or 30)},setsar=1[vout]',
            ])
            with JobWorkspace(
                project.public_id, f'timeline-review-r{revision.revision}',
                estimated_bytes=storage.input(job.original_video).workspace_estimate(needs_proxy=True),
            ) as workspace:
                main_output = workspace.file('output', 'timeline-review-main.mp4')
                command = [settings.FFMPEG_BINARY, '-y', '-i', assembly.runner.input_arg(source), '-filter_complex', filter_graph,
                    '-map', '[vout]', '-map', '[joineda]', '-c:v', 'libx264', '-preset', settings.EXTERNAL_MEDIA_PROXY_PRESET,
                    '-crf', str(int(getattr(profile, 'video_crf', 0) or settings.EXTERNAL_MEDIA_PROXY_CRF)), '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', f'{int(getattr(profile, "audio_bitrate_kbps", 0) or 96)}k', '-ar', '48000', '-ac', '2',
                    '-movflags', '+faststart', str(main_output)]
                assembly.runner.run(command)
                rendered_layers = []
                output = main_output
                duration_ms = cls._timeline_review_duration_ms(revision)
                brolls = list((revision.timeline or {}).get('brolls') or [])
                if brolls:
                    from .broll import BrollRenderService

                    broll_output = workspace.file('output', 'timeline-review-broll.mp4')
                    output = BrollRenderService(assembly.runner, storage).apply(
                        project, output, broll_output, brolls, proxy_width, proxy_height,
                        duration_ms=duration_ms,
                    )
                    rendered_layers.append('BROLL')
                overlays = list((revision.timeline or {}).get('overlays') or [])
                if overlays:
                    from .overlays import OverlayRenderService

                    overlay_output = workspace.file('output', 'timeline-review-overlays.mp4')
                    output = OverlayRenderService(assembly.runner).apply(
                        output, overlay_output, overlays, proxy_width, proxy_height, workspace.path,
                    )
                    rendered_layers.append('OVERLAYS')
                tracks = list(job.subtitle_tracks.all().order_by('language', 'pk'))
                if tracks and project.template_version.subtitles_enabled:
                    from .services import RenderService

                    subtitle_output = workspace.file('output', 'timeline-review-subtitles.mp4')
                    RenderService(assembly.runner).render_tracks(
                        output, tracks, subtitle_output, preset, job.subtitle_style, workspace.path,
                        source_language=project.template_version.original_language,
                        translated_style=job.translated_subtitle_style or job.subtitle_style,
                    )
                    output = subtitle_output
                    rendered_layers.append('CAPTIONS')
                # Do not mark a continuous review proxy as ready solely
                # because FFmpeg exited successfully.  An AAC stream can
                # still begin seconds after the picture, which recreates the
                # silent-take/caption drift bug this proxy is meant to avoid.
                quality.validate_preview_proxy(
                    output,
                    expected_duration_ms=expected_duration_ms,
                    expected_width=proxy_width,
                    expected_height=proxy_height,
                ).require_ok()
                proxy.duration_ms = assembly._duration_ms(output)
                with output.open('rb') as handle:
                    proxy.proxy_file.save('timeline-review.mp4', File(handle), save=False)
            with transaction.atomic():
                locked_proxy = ProjectSourceProxy.objects.select_for_update().get(pk=proxy.pk)
                # A replacement worker may have recovered an expired lease
                # while this long FFmpeg process was still ending. Its result
                # is authoritative; do not publish this older one over it.
                if (locked_proxy.metadata or {}).get('generation_lease') != lease:
                    return locked_proxy
                locked_proxy.proxy_file = proxy.proxy_file
                locked_proxy.duration_ms = proxy.duration_ms
                locked_proxy.status = ProjectSourceProxy.Status.READY
                locked_proxy.error_message = ''
                locked_proxy.metadata = {
                    'kind': 'timeline_review', 'timeline_revision_id': revision.pk,
                    'timeline_revision': revision.revision, 'continuous': True,
                    'temporal_parity': 'verified_audio_timing_duration_and_geometry',
                    'geometry': {'width': proxy_width, 'height': proxy_height},
                    'rendered_layers': rendered_layers,
                }
                locked_proxy.save()
                logger.info(
                    'Preview contínuo pronto para projeto %s, revisão %s.',
                    project.public_id, revision.revision,
                )
                return locked_proxy
        except Exception as exc:
            # Do not let an expired worker overwrite a newer lease's result.
            with transaction.atomic():
                locked_proxy = ProjectSourceProxy.objects.select_for_update().get(pk=proxy.pk)
                metadata = dict(locked_proxy.metadata or {})
                if metadata.get('generation_lease') == lease:
                    metadata.pop('generation_lease', None)
                    locked_proxy.status = ProjectSourceProxy.Status.ERROR
                    locked_proxy.error_message = str(exc)[:255]
                    locked_proxy.metadata = metadata
                    locked_proxy.save(update_fields=[
                        'status', 'error_message', 'source_storage_name', 'metadata', 'update_at',
                    ])
            logger.exception(
                'Falha ao gerar preview contínuo do projeto %s, revisão %s.',
                project.public_id, revision.revision,
            )
            raise

    @classmethod
    def _timeline_review_filters(cls, revision, assembly, concat_width=None, concat_height=None):
        manifest = revision.source_manifest or {}
        timeline = revision.timeline or {}
        clip_end_by_source = {}
        for clip in timeline.get('clips') or []:
            source_id = str(clip.get('asset_id') or '')
            clip_end_by_source[source_id] = max(
                clip_end_by_source.get(source_id, 0), int(clip.get('source_out_ms') or 0),
            )
        source_starts, cursor = {}, 0
        for source in manifest.get('sources') or []:
            if not (source.get('metadata') or {}).get('render_enabled', True):
                continue
            trim = source.get('trim') or {}
            start = int(trim.get('start_ms') or 0)
            end = int(trim.get('end_ms') or source.get('duration_ms') or clip_end_by_source.get(str(source.get('id'))) or start)
            source_starts[str(source.get('id'))] = (cursor, start)
            cursor += max(1, end - start)
        reframe_operations = [
            item for item in (revision.edit_decision_set.get('operations') or [])
            if item.get('type') == 'reframe' and item.get('enabled', True)
        ]
        transforms = {
            str(item.get('source_id')): (item.get('metadata') or {}).get('manual_transform')
            for item in reframe_operations
            # Scoped decisions belong to a blade segment. They must not
            # overwrite another segment simply because they occur later.
            if 'segment_start_ms' not in (item.get('metadata') or {})
        }
        video_filters, audio_filters, count = [], [], 0
        for clip in timeline.get('clips') or []:
            source_id = str(clip.get('asset_id') or '')
            if source_id not in source_starts:
                continue
            master_start, trim_start = source_starts[source_id]
            start = (master_start + int(clip.get('source_in_ms') or 0) - trim_start) / 1000
            end = (master_start + int(clip.get('source_out_ms') or 0) - trim_start) / 1000
            if end <= start:
                continue
            clip_start_ms, clip_end_ms = round(start * 1000), round(end * 1000)
            scoped = sorted(
                (item for item in reframe_operations if (
                    str(item.get('source_id')) == source_id
                    and 'segment_start_ms' in (item.get('metadata') or {})
                )),
                key=lambda item: int((item.get('metadata') or {}).get('segment_start_ms') or 0),
            )
            boundaries = {clip_start_ms, clip_end_ms}
            for item in scoped:
                metadata = item.get('metadata') or {}
                for boundary in (metadata.get('segment_start_ms'), metadata.get('segment_end_ms')):
                    if boundary is not None and clip_start_ms < int(boundary) < clip_end_ms:
                        boundaries.add(int(boundary))
            ordered_boundaries = sorted(boundaries)
            for part_start_ms, part_end_ms in zip(ordered_boundaries, ordered_boundaries[1:]):
                transform = transforms.get(source_id) or {}
                for item in scoped:
                    metadata = item.get('metadata') or {}
                    if int(metadata.get('segment_start_ms') or 0) <= part_start_ms < int(metadata.get('segment_end_ms') or 0):
                        transform = metadata.get('manual_transform') or {}
                        break
                scale = min(2.0, max(1.0, float(transform.get('scale') or 1)))
                x = min(1.0, max(-1.0, float(transform.get('x') or 0)))
                y = min(1.0, max(-1.0, float(transform.get('y') or 0)))
                crop = '' if scale == 1 else (
                    f',crop=trunc(iw/{scale:.5f}/2)*2:trunc(ih/{scale:.5f}/2)*2:'
                    f'(iw-ow)/2-({x:.5f})*(iw-ow)/2:(ih-oh)/2-({y:.5f})*(ih-oh)/2,'
                    f'scale=trunc(iw*{scale:.5f}/2)*2:trunc(ih*{scale:.5f}/2)*2'
                )
                normalize = (
                    f',scale={int(concat_width)}:{int(concat_height)}:flags=fast_bilinear,setsar=1'
                    if concat_width and concat_height else ''
                )
                video_filters.append(f'[0:v]trim=start={part_start_ms / 1000:.3f}:end={part_end_ms / 1000:.3f},setpts=PTS-STARTPTS{crop}{normalize}[v{count}]')
                audio_filters.append(f'[0:a]atrim=start={part_start_ms / 1000:.3f}:end={part_end_ms / 1000:.3f},asetpts=PTS-STARTPTS,aresample=async=1000:first_pts=0[a{count}]')
                count += 1
        if count:
            joined = ''.join(f'[v{index}][a{index}]' for index in range(count))
            video_filters.append(f'{joined}concat=n={count}:v=1:a=1[joinedv][joineda]')
        return [*video_filters, *audio_filters], count

    @classmethod
    def _timeline_review_duration_ms(cls, revision):
        """Return the exact edited clock expected from the concatenated clips."""
        renderable_sources = {
            str(source.get('id'))
            for source in ((revision.source_manifest or {}).get('sources') or [])
            if (source.get('metadata') or {}).get('render_enabled', True)
        }
        duration = 0
        for clip in (revision.timeline or {}).get('clips') or []:
            if str(clip.get('asset_id') or '') not in renderable_sources:
                continue
            start = int(clip.get('source_in_ms') or 0)
            end = int(clip.get('source_out_ms') or 0)
            duration += max(0, end - start)
        return duration

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
        if MediaTemplatePlugin.Code.BROLL in plugin_codes or project.broll_assets.exists():
            available.add(cls.BROLL)
        if project.template_version.audio_noise_cleanup_enabled:
            available.add(cls.AUDIO_NOISE_CLEANUP)
        if MediaTemplatePlugin.Code.LUT in plugin_codes:
            available.add(cls.COLOR)
        available.update(declared)
        if project.template_version.blocks.exclude(overlay_definitions=[]).exists() or project.overlays.exists():
            available.add(cls.GRAPHICS)
        implemented_editable = {cls.CUTS, cls.SUBTITLES, cls.TRANSFORMS, cls.GRAPHICS, cls.BROLL}
        editable = [item for item in configured if item in available and item in implemented_editable]
        if cls.GRAPHICS in available and cls.GRAPHICS not in editable:
            editable.append(cls.GRAPHICS)
        if cls.BROLL in available and cls.BROLL not in editable:
            editable.append(cls.BROLL)
        return {
            'available': sorted(available),
            'editable': editable,
            'read_only': sorted(available - set(editable)),
        }


class PreviewCompositionService:
    """Composes browser playback data without introducing editorial decisions."""

    FIDELITY = {
        'CUTS': 'EXACT',
        # Timing is mapped to the same edited clock, but browser typography
        # and libass do not share one rasterizer. Do not promise pixel parity
        # for wrapping, glyph metrics or ASS-only background/shadow behavior.
        'SUBTITLES': 'APPROXIMATE',
        'TRANSFORMS': 'APPROXIMATE',
        'CAMERA_SWITCHES': 'NOT_AVAILABLE',
        'LAYOUTS': 'NOT_AVAILABLE',
        'GRAPHICS': 'APPROXIMATE',
        'BROLL': 'APPROXIMATE',
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
            'background_height_percent': style.background_height_percent,
            'background_radius': style.background_radius, 'outline_color': style.outline_color,
            'outline_width': style.outline_width, 'shadow': style.shadow,
            'shadow_angle': style.shadow_angle, 'shadow_size': style.shadow_size,
            'shadow_blur': style.shadow_blur, 'shadow_opacity': style.shadow_opacity,
            'max_lines': style.max_lines, 'max_characters': style.max_characters,
        }

    @classmethod
    def hydrate_legacy_caption_clock(cls, project, timeline):
        """Expose legacy revision captions on their authoritative edited clock."""
        if timeline.get('caption_clock') == 'edited' or not project.render_job_id:
            return timeline
        captions = []
        for cue in SubtitleCue.objects.filter(track__job_id=project.render_job_id).select_related('track').order_by(
            'track__language', 'start_ms', 'pk',
        ):
            start_ms = max(0, int(cue.start_ms or 0))
            end_ms = max(0, int(cue.end_ms or 0))
            if end_ms <= start_ms:
                continue
            captions.append({
                'id': cue.pk,
                'track_id': cue.track_id,
                'language': cue.track.language,
                'start_ms': start_ms,
                'end_ms': end_ms,
                'text': cue.text,
                'is_source': cue.track.is_source,
            })
        timeline['captions'] = captions
        timeline['caption_clock'] = 'edited'
        return timeline

    @classmethod
    def compose(
        cls, project, source_manifest, decisions, revision,
        overlays_override=None, brolls_override=None,
    ):
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
            and 'segment_start_ms' not in (item.get('metadata') or {})
        }
        assets, clips, markers = [], [], []
        source_cursor = timeline_cursor = 0
        # Ranges kept in the pre-cut "project_timeline" coordinate space (the
        # same clock used by remove_segment/audio_noise_reduction decisions),
        # so those decisions can later be traced back to the source that
        # produced them even though they are recorded against 'project-master'.
        master_source_ranges = []
        for source in source_manifest.get('sources') or []:
            if not (source.get('metadata') or {}).get('render_enabled', True):
                continue
            source_id = str(source['id'])
            proxy = proxies.get(source_id)
            if not proxy:
                # A legacy project can have one proxy for the original take
                # while its current manifest splits it into -trecho-N ranges.
                # The ranges seek within that same file; they must therefore
                # inherit its real duration instead of collapsing to 1 ms when
                # an element edit creates a fresh timeline revision.
                base_source_id, marker, segment_index = source_id.rpartition('-trecho-')
                if marker and segment_index.isdigit():
                    proxy = proxies.get(base_source_id)
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
            master_source_ranges.append((global_start, global_end, source['id']))
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

        # The review UI lets the member click a specific take on the timeline
        # and see only the decisions that belong to it. remove_segment/
        # audio_noise_reduction operations are recorded against the virtual
        # 'project-master' timeline, so resolve which real source produced
        # each one from its position in that same coordinate space.
        for item in operations:
            if item.get('type') not in {'remove_segment', 'audio_noise_reduction'}:
                continue
            if item.get('source_id') != 'project-master':
                continue
            position = int(item.get('source_in_ms') or 0)
            for range_start, range_end, range_source_id in master_source_ranges:
                if range_start <= position < range_end:
                    item['related_source_id'] = range_source_id
                    break

        captions = []
        if project.render_job_id:
            for cue in SubtitleCue.objects.filter(track__job_id=project.render_job_id).select_related('track').order_by(
                'track__language', 'start_ms', 'pk',
            ):
                # SubtitleCue timestamps already use the currently edited
                # timeline clock. Initial transcription is remapped after the
                # automatic speech edits, and member cuts shift the persisted
                # cues in `_shift_cues_for_decision`. Applying `cuts` here a
                # second time made every caption after a cut advance twice.
                start_ms = max(0, int(cue.start_ms or 0))
                end_ms = max(0, int(cue.end_ms or 0))
                if end_ms <= start_ms:
                    continue
                captions.append({
                    'id': cue.pk,
                    'track_id': cue.track_id,
                    'language': cue.track.language,
                    'start_ms': start_ms,
                    'end_ms': end_ms,
                    'text': cue.text,
                    'is_source': cue.track.is_source,
                })
        capabilities = PreviewCapabilityRegistry.resolve(project)
        overlays = (
            deepcopy(overlays_override)
            if overlays_override is not None
            else OverlayTimelineService.compose(project, clips, timeline_cursor)
        )
        composed_brolls, broll_assets = BrollTimelineService.compose(
            project, clips, timeline_cursor,
            time_mapper=lambda value: cls._source_to_timeline(value, cuts),
        )
        brolls = deepcopy(brolls_override) if brolls_override is not None else composed_brolls
        assets.extend(broll_assets)
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
        has_reframe_override = any(
            item.get('type') == 'reframe'
            and (
                not item.get('enabled', True)
                or (item.get('metadata') or {}).get('manual_transform')
            )
            for item in operations
        )
        # Until a fresh continuous review proxy is composed, a manual transform
        # must use the source proxy. The prior assembled master can contain a
        # previous crop and would show a different zoom from the final render.
        if has_reframe_override:
            review_master_url = None
        fidelity = {key: cls.FIDELITY[key] for key in capabilities['available']}
        if review_master_url and not has_reframe_override and 'TRANSFORMS' in fidelity:
            fidelity['TRANSFORMS'] = 'EXACT'
        output_preset = project.render_job.preset if project.render_job_id else project.template_version.preset
        return {
            'schema': 'connect.internal_timeline.v1',
            'timeline_revision': revision,
            'project': {'id': str(project.public_id), 'name': project.name},
            'sequence': {
                # Subtitles are burned using the render job's frozen preset.
                # The editor must use that same canvas, even if a template was
                # changed after this project was created.
                'width': output_preset.width or 1920,
                'height': output_preset.height or 1080,
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
            # Visual blade points do not remove media by themselves, but must
            # survive a reload so the member can select either resulting take.
            'video_splits_ms': sorted({
                cls._source_to_timeline(int(value), cuts)
                for value in (decisions.get('video_splits_ms') or [])
                if 0 < cls._source_to_timeline(int(value), cuts) < timeline_cursor
            }),
            'video_tracks': [
                {'id': 'V1', 'role': 'main', 'clips': clips},
                {'id': 'V2', 'role': 'broll', 'clips': brolls},
                {'id': 'V3', 'role': 'overlay', 'clips': overlays},
            ],
            'broll_tracks': [{'id': 'BROLL', 'role': 'broll', 'clips': brolls}],
            'brolls': brolls,
            'overlay_tracks': [{'id': 'OVERLAYS', 'role': 'overlay', 'clips': overlays}],
            'overlays': overlays,
            'captions': captions,
            'caption_clock': 'edited',
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

    @staticmethod
    def _source_to_timeline(value, cuts):
        """Map assembled-master time into the edited timeline after removals."""
        value = max(0, int(value or 0))
        removed = 0
        for start, end in cuts:
            if value <= start:
                break
            removed += max(0, min(value, end) - start)
        return max(0, value - removed)


class TimelineRevisionService:
    @staticmethod
    def source_duration_reference(project, parent=None):
        """Return the newest usable source timeline for legacy manifests.

        Older projects can have a manifest with trim ranges but without media
        durations, and only a proxy for the unsplit take.  A source-only edit
        must never turn that into one millisecond per clip.
        """
        candidates = [parent] if parent else []
        candidates.extend(
            project.timeline_revisions.exclude(pk=getattr(parent, 'pk', None)).order_by('-revision')[:50]
        )
        for revision in candidates:
            if not revision:
                continue
            timeline = revision.timeline or {}
            duration = int((timeline.get('sequence') or {}).get('duration_ms') or 0)
            if duration >= 1000 and timeline.get('assets') and timeline.get('clips'):
                return timeline
        return {}

    @classmethod
    def manifest_with_reference_durations(cls, manifest, reference_timeline):
        prepared = deepcopy(manifest)
        known = {
            str(item.get('id')): int(item.get('duration_ms') or 0)
            for item in reference_timeline.get('assets', [])
            if item.get('id') and int(item.get('duration_ms') or 0) > 0
        }
        for clip in reference_timeline.get('clips', []):
            source_id = str(clip.get('asset_id') or '')
            known[source_id] = max(known.get(source_id, 0), int(clip.get('source_out_ms') or 0))
        for source in prepared.get('sources', []):
            if source.get('duration_ms'):
                continue
            source_id = str(source.get('id') or '')
            base_id, marker, segment_index = source_id.rpartition('-trecho-')
            duration = known.get(source_id) or (
                known.get(base_id) if marker and segment_index.isdigit() else 0
            )
            if duration:
                source['duration_ms'] = duration
        return prepared

    @staticmethod
    def _available_master_video_ranges(manifest, decisions):
        """Return uncut video ranges in the original assembled-master clock."""
        cuts = sorted(
            (
                max(0, int(item.get('source_in_ms') or 0)),
                max(0, int(item.get('source_out_ms') or 0)),
            )
            for item in (decisions.get('operations') or [])
            if item.get('type') == 'remove_segment' and item.get('enabled', True)
        )
        ranges, cursor = [], 0
        for source in manifest.get('sources') or []:
            if not (source.get('metadata') or {}).get('render_enabled', True):
                continue
            trim = source.get('trim') or {}
            source_start = max(0, int(trim.get('start_ms') or 0))
            source_duration = int(
                source.get('duration_ms')
                or (source.get('metadata') or {}).get('duration_ms')
                or trim.get('end_ms')
                or source_start
            )
            source_end = max(source_start, min(
                source_duration, int(trim.get('end_ms') or source_duration),
            ))
            duration = source_end - source_start
            if not duration:
                continue
            ranges.extend(PreviewCompositionService._subtract(cursor, cursor + duration, cuts))
            cursor += duration
        return ranges

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
    def apply_all_noise_reductions(cls, project, member):
        """Accept every pending noise recommendation in one undoable revision."""
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        decisions = deepcopy(current.edit_decision_set)
        pending = [
            item for item in decisions.get('operations', [])
            if item.get('type') == 'audio_noise_reduction' and not item.get('enabled', False)
        ]
        if not pending:
            return current
        reviewed_at = timezone.now().isoformat()
        for item in pending:
            item['enabled'] = True
            item['review'] = {'member_id': member.pk, 'reviewed_at': reviewed_at}
        revision = cls._create(
            locked, current.source_manifest, decisions,
            f'{len(pending)} redução(ões) de ruído aplicada(s)', member, current,
        )
        session = cls.session(locked, member, revision)
        cls._record(
            session, current, revision, 'APPLY_ALL_NOISE_REDUCTIONS',
            {'decision_ids': [item['id'] for item in pending]},
            {'decision_ids': [item['id'] for item in pending], 'enabled': False}, member,
        )
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
    def create_video_split(cls, project, member, master_ms):
        """Persist a non-destructive main-video blade point in the timeline."""
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        point = max(0, int(master_ms))
        available_ranges = cls._available_master_video_ranges(
            current.source_manifest, current.edit_decision_set,
        )
        if not any(
            start + MIN_MANUAL_CUT_MS <= point <= end - MIN_MANUAL_CUT_MS
            for start, end in available_ranges
        ):
            raise ValueError('O corte precisa ficar dentro de um vídeo, com ao menos um quadro em cada lado.')
        decisions = deepcopy(current.edit_decision_set)
        points = sorted({int(value) for value in (decisions.get('video_splits_ms') or [])} | {point})
        if any(existing != point and abs(existing - point) < MIN_MANUAL_CUT_MS for existing in points):
            raise ValueError('Já existe um corte neste quadro.')
        decisions['video_splits_ms'] = points
        # A blade is an editorial/timeline boundary, not a new render source.
        # The final assembler normalizes one source at a time; cloning reframe
        # operations here made it possible to approve different framing for
        # each side of a blade even though the delivery renderer could only
        # honour one of them. Keep one framing decision per source.
        revision = cls._create(locked, current.source_manifest, decisions, 'Vídeo dividido', member, current)
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'SPLIT_VIDEO', {'master_ms': point}, {'master_ms': point}, member)
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
        if 'segment_start_ms' in (target.get('metadata') or {}):
            # Revisions created before per-source framing was enforced may
            # still contain one reframe operation for each side of a blade.
            # Promote the user-selected value to the whole take now instead
            # of silently rendering only an arbitrary segment at delivery.
            source_id = target.get('source_id')
            normalized = deepcopy(target)
            metadata = normalized.setdefault('metadata', {})
            metadata.pop('segment_start_ms', None)
            metadata.pop('segment_end_ms', None)
            decisions['operations'] = [
                item for item in decisions.get('operations', [])
                if not (
                    item.get('type') == 'reframe'
                    and item.get('source_id') == source_id
                    and 'segment_start_ms' in (item.get('metadata') or {})
                )
            ]
            decisions['operations'].append(normalized)
            target = normalized
        previous = deepcopy((target.get('metadata') or {}).get('manual_transform'))
        if values.get('restore_original'):
            target['enabled'] = False
            target.setdefault('metadata', {}).pop('manual_transform', None)
            revision = cls._create(locked, current.source_manifest, decisions, 'Enquadramento original restaurado', member, current)
            session = cls.session(locked, member, revision)
            cls._record(session, current, revision, 'RESTORE_ORIGINAL_REFRAME', {
                'decision_id': decision_id,
            }, {'decision_id': decision_id, 'manual_transform': previous}, member)
            return revision
        if values.get('restore_auto'):
            target['enabled'] = True
            target.setdefault('metadata', {}).pop('manual_transform', None)
            revision = cls._create(locked, current.source_manifest, decisions, 'Enquadramento automático restaurado', member, current)
            session = cls.session(locked, member, revision)
            cls._record(session, current, revision, 'RESTORE_AUTO_REFRAME', {
                'decision_id': decision_id,
            }, {'decision_id': decision_id, 'manual_transform': previous}, member)
            return revision
        transform = {
            'scale': min(2.0, max(1.0, float(values.get('scale') or 1))),
            'x': min(1.0, max(-1.0, float(values.get('x') or 0))),
            'y': min(1.0, max(-1.0, float(values.get('y') or 0))),
        }
        if previous == transform and target.get('enabled', True):
            return current
        # Moving a slider is an explicit manual framing choice, including when
        # the member had previously opted out of Auto Reframe.
        target['enabled'] = True
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
    def update_music(cls, project, member, track_id):
        """Store the chosen music in the immutable editorial snapshot.

        Music used to be written directly to ``project.configuration``.  That
        made undo/redo and reopening a finished project disagree with the
        version that would actually be rendered.  Keeping its id in the
        timeline gives it exactly the same history semantics as captions,
        framing and B-roll.
        """
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        previous = (current.timeline or {}).get('music_override_track_id')
        track_id = int(track_id) if track_id else None
        # Once the project is edited again, do not let a pre-revision override
        # shadow an undo back to the template music.
        legacy_configuration = dict(locked.configuration or {})
        if legacy_configuration.pop('music_override_track_id', None) is not None:
            locked.configuration = legacy_configuration
            locked.save(update_fields=['configuration', 'update_at'])
        if previous == track_id:
            return current
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set,
            'Trilha sonora alterada', member, current,
            music_override_track_id=track_id,
        )
        session = cls.session(locked, member, revision)
        cls._record(
            session, current, revision, 'UPDATE_MUSIC',
            {'track_id': track_id}, {'track_id': previous}, member,
        )
        return revision

    @classmethod
    @transaction.atomic
    def update_music_volume(cls, project, member, volume):
        """Save the member's music gain in the same immutable revision."""
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        requested = max(0.0, min(1.0, float(volume)))
        previous = float((current.timeline or {}).get(
            'music_volume', locked.template_version.music_volume,
        ))
        if abs(previous - requested) < .0001:
            return current
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set,
            'Volume da trilha ajustado', member, current,
            music_volume=requested,
        )
        session = cls.session(locked, member, revision)
        cls._record(
            session, current, revision, 'UPDATE_MUSIC_VOLUME',
            {'volume': requested}, {'volume': previous}, member,
        )
        return revision

    @classmethod
    @transaction.atomic
    def mutate_overlay(cls, project, member, overlay_id, payload, *, create=False, delete=False):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        overlays = deepcopy(current.timeline.get('overlays') or [])
        target = next((item for item in overlays if item.get('id') == overlay_id), None)
        previous = deepcopy(target)
        split_at = payload.get('split_at_ms')
        if split_at is not None and not create and not delete:
            if not target:
                raise ValueError('Overlay não encontrado nesta timeline.')
            point = int(split_at)
            start, end = int(target.get('start_ms') or 0), int(target.get('end_ms') or 0)
            if point - start < MIN_MANUAL_CUT_MS or end - point < MIN_MANUAL_CUT_MS:
                raise ValueError('O corte precisa deixar pelo menos um quadro em cada lado.')
            right = deepcopy(target)
            target['end_ms'] = point
            right['id'] = f'{overlay_id}-split-{uuid.uuid4().hex[:8]}'
            right['start_ms'] = point
            # A blade cut is instantaneous; do not replay the original entry
            # animation in the middle of an otherwise continuous overlay.
            right['animation'] = {**(right.get('animation') or {}), 'type': 'NONE'}
            right['source'] = 'USER'
            overlays.append(right)
            reason, operation = 'Overlay dividido', 'SPLIT_OVERLAY'
        elif delete:
            if not target:
                raise ValueError('Overlay não encontrado nesta timeline.')
            overlays.remove(target)
            reason, operation = 'Overlay removido', 'DELETE_OVERLAY'
        elif create:
            if target:
                raise ValueError('Já existe um overlay com este identificador.')
            overlay_type = str(payload.get('type') or 'TEXT').upper()
            # New timeline elements are text-only.  QR Codes, images and
            # videos are uploaded as visual media so they get the same crop,
            # timing, transform and preview workflow as every other asset.
            if overlay_type != 'TEXT':
                raise ValueError('Adicione QR Codes e imagens como mídia visual. Elementos novos aceitam apenas texto.')
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
    @transaction.atomic
    def mutate_broll(cls, project, member, broll_id, payload, *, delete=False, restore=False):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        brolls = deepcopy(current.timeline.get('brolls') or [])
        target = next((item for item in brolls if item.get('id') == broll_id), None)
        if not target:
            raise ValueError('B-roll não encontrado nesta timeline.')
        previous = deepcopy(target)
        split_at = payload.get('split_at_ms')
        if split_at is not None and not delete and not restore:
            point = int(split_at)
            start, end = int(target.get('start_ms') or 0), int(target.get('end_ms') or 0)
            if point - start < MIN_MANUAL_CUT_MS or end - point < MIN_MANUAL_CUT_MS:
                raise ValueError('O corte precisa deixar pelo menos um quadro em cada lado.')
            right = deepcopy(target)
            target['end_ms'] = point
            right['id'] = f'{broll_id}-split-{uuid.uuid4().hex[:8]}'
            right['start_ms'] = point
            target['exit'] = {**(target.get('exit') or {}), 'type': 'NONE'}
            right['entry'] = {**(right.get('entry') or {}), 'type': 'NONE'}
            # A video B-roll must continue from the matching source frame.
            if str(right.get('media_type') or '').upper() == ProjectBrollAsset.MediaType.VIDEO:
                right['source_in_ms'] = int(right.get('source_in_ms') or 0) + (point - start)
            right['source'] = 'USER'
            brolls.append(right)
            reason, operation = 'B-roll dividido', 'SPLIT_BROLL'
        elif delete:
            brolls.remove(target)
            reason, operation = 'B-roll removido', 'DELETE_BROLL'
        elif restore:
            automatic = deepcopy(target.get('auto') or {})
            if not automatic:
                raise ValueError('Este B-roll não possui uma decisão automática para restaurar.')
            automatic['auto'] = deepcopy(target.get('auto'))
            target.clear()
            target.update(automatic)
            reason, operation = 'B-roll restaurado', 'RESTORE_BROLL'
        else:
            duration = int(current.timeline.get('sequence', {}).get('duration_ms') or 1)
            if 'start_ms' in payload:
                target['start_ms'] = max(0, min(duration - 1, int(payload['start_ms'])))
            if 'end_ms' in payload:
                target['end_ms'] = min(duration, max(target['start_ms'] + 1, int(payload['end_ms'])))
            if 'layer' in payload:
                target['layer'] = max(0, min(20, int(payload['layer'])))
            if 'source_in_ms' in payload:
                target['source_in_ms'] = max(0, int(payload['source_in_ms']))
            if 'source_out_ms' in payload:
                target['source_out_ms'] = max(target.get('source_in_ms', 0) + 1, int(payload['source_out_ms']))
            if 'display_mode' in payload:
                mode = str(payload['display_mode']).upper()
                if mode not in {'FULLSCREEN', 'OVERLAY'}:
                    raise ValueError('Modo de exibição inválido.')
                target['display_mode'] = mode
            if 'asset_id' in payload:
                asset = ProjectBrollAsset.objects.filter(project=locked, public_id=payload['asset_id'], is_enabled=True).first()
                if not asset:
                    raise ValueError('Asset de substituição não encontrado.')
                target['asset_id'] = str(asset.public_id)
                target['media_type'] = asset.media_type
            for key in ('transform', 'motion', 'entry', 'exit', 'fit', 'crop_mode'):
                if key in payload:
                    target[key] = deepcopy(payload[key])
            target['source'] = 'USER'
            reason, operation = 'B-roll ajustado', 'UPDATE_BROLL'
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set, reason, member, current,
            overlays=deepcopy(current.timeline.get('overlays') or []), brolls=brolls,
        )
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, operation, {
            'broll_id': broll_id, 'broll': deepcopy(target) if not delete else None,
        }, {'broll_id': broll_id, 'broll': previous}, member)
        return revision

    @classmethod
    @transaction.atomic
    def add_broll(cls, project, member, asset, start_ms=0):
        locked = ExternalMediaProject.objects.select_for_update().get(pk=project.pk)
        current = locked.current_timeline_revision or cls.ensure_initial(locked, member)
        brolls = deepcopy(current.timeline.get('brolls') or [])
        duration = int(current.timeline.get('sequence', {}).get('duration_ms') or 1)
        start = max(0, min(duration - 1, int(start_ms or 0)))
        end = min(duration, start + min(6000, max(1000, int(asset.duration_ms or 5000))))
        description = (asset.description or asset.original_filename).lower()
        display_mode = 'OVERLAY' if any(word in description for word in ('qr', 'logo', 'card')) else 'FULLSCREEN'
        item = {
            'id': f'broll-{asset.public_id}', 'type': 'BROLL', 'asset_id': str(asset.public_id),
            'media_type': asset.media_type, 'start_ms': start, 'end_ms': max(start + 1, end),
            'source_in_ms': int(asset.trim_start_ms or 0),
            'source_out_ms': int(asset.trim_end_ms or (asset.trim_start_ms or 0) + max(1, end - start)),
            'display_mode': display_mode, 'fit': 'COVER', 'crop_mode': 'CENTER',
            'transform': {'x': .82 if display_mode == 'OVERLAY' else .5, 'y': .78 if display_mode == 'OVERLAY' else .5, 'scale': .28 if display_mode == 'OVERLAY' else 1},
            'motion': {'type': 'ZOOM_IN' if asset.media_type == 'IMAGE' else 'NONE', 'from_scale': 1, 'to_scale': 1.06, 'easing': 'EASE_IN_OUT'},
            'entry': {'type': 'FADE', 'duration_ms': 300, 'easing': 'ease-out'},
            'exit': {'type': 'FADE', 'duration_ms': 250, 'easing': 'ease-in'},
            'source': 'USER', 'confidence': 1, 'enabled': True,
        }
        item['auto'] = deepcopy(item)
        brolls.append(item)
        revision = cls._create(
            locked, current.source_manifest, current.edit_decision_set,
            'B-roll adicionado', member, current,
            overlays=deepcopy(current.timeline.get('overlays') or []), brolls=brolls,
        )
        session = cls.session(locked, member, revision)
        cls._record(session, current, revision, 'CREATE_BROLL', {'broll_id': item['id'], 'broll': item}, {'broll_id': item['id']}, member)
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
        # The revision proxy improves visual review, but it is asynchronous
        # infrastructure. A slow preview worker must never prevent approval
        # or leave a project stuck in review; the final renderer consumes this
        # immutable revision directly.
        scoped_reframes = [
            item for item in (revision.edit_decision_set.get('operations') or [])
            if item.get('type') == 'reframe'
            and 'segment_start_ms' in (item.get('metadata') or {})
        ]
        if scoped_reframes:
            # Legacy blades used to clone a reframe decision per segment even
            # though the final assembler has one framing plan per source. Do
            # not strand those projects in review: create an auditable child
            # revision that consolidates each take. A manual value wins; when
            # more than one exists, the last saved one is the member's latest
            # explicit editorial choice.
            decisions = deepcopy(revision.edit_decision_set)
            operations = decisions.get('operations') or []
            for source_id in {item.get('source_id') for item in scoped_reframes}:
                candidates = [
                    item for item in operations
                    if item.get('type') == 'reframe'
                    and item.get('source_id') == source_id
                    and 'segment_start_ms' in (item.get('metadata') or {})
                ]
                if not candidates:
                    continue
                manual = [
                    item for item in candidates
                    if (item.get('metadata') or {}).get('manual_transform')
                ]
                chosen = deepcopy((manual or candidates)[-1])
                chosen_metadata = chosen.setdefault('metadata', {})
                chosen_metadata.pop('segment_start_ms', None)
                chosen_metadata.pop('segment_end_ms', None)
                candidate_ids = {id(item) for item in candidates}
                operations = [item for item in operations if id(item) not in candidate_ids]
                operations.append(chosen)
            decisions['operations'] = operations
            revision = cls._create(
                locked, revision.source_manifest, decisions,
                'Enquadramentos por trecho consolidados para renderização', member, revision,
            )
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
    def _create(
        cls, project, manifest, decisions, reason, member, parent=None,
        overlays=None, brolls=None, music_override_track_id=None, music_volume=None,
    ):
        number = (project.timeline_revisions.aggregate(value=Max('revision'))['value'] or 0) + 1
        # Overlay edits live in the revision itself. Preserve them when another
        # kind of edit creates a child revision, otherwise template overlays
        # would silently return and manual overlays would disappear.
        if overlays is None and parent:
            overlays = deepcopy(parent.timeline.get('overlays') or [])
        if brolls is None and parent:
            brolls = deepcopy(parent.timeline.get('brolls') or [])
        composition_manifest = cls.manifest_with_reference_durations(
            manifest, cls.source_duration_reference(project, parent),
        )
        timeline = PreviewCompositionService.compose(
            project, composition_manifest, decisions, number,
            overlays_override=overlays, brolls_override=brolls,
        )
        # Keep the selected track through every unrelated mutation.  ``None``
        # means inherit the parent; callers changing music pass an explicit
        # id (or the empty-string sentinel when clearing it in the future).
        if music_override_track_id is None and parent:
            music_override_track_id = (parent.timeline or {}).get('music_override_track_id')
        if music_override_track_id:
            timeline['music_override_track_id'] = int(music_override_track_id)
        if music_volume is None and parent:
            music_volume = (parent.timeline or {}).get('music_volume')
        if music_volume is not None:
            timeline['music_volume'] = max(0.0, min(1.0, float(music_volume)))
        revision = TimelineRevision.objects.create(
            project=project, revision=number, parent=parent, timeline=timeline,
            source_manifest=composition_manifest, edit_decision_set=decisions, reason=reason, created_by=member,
        )
        project.current_timeline_revision = revision
        project.preview_dirty = bool(parent)
        project.final_render_outdated = bool(parent)
        project.save(update_fields=[
            'current_timeline_revision', 'preview_dirty', 'final_render_outdated', 'update_at',
        ])
        # This is deliberately deferred: an edit must be saved even if a
        # worker is temporarily unavailable. The editor continues on source
        # proxies until the continuous revision proxy is ready.
        if project.render_job_id and project.render_job and project.render_job.original_video:
            transaction.on_commit(lambda: cls._enqueue_timeline_review_proxy(project.pk, revision.pk))
        return revision

    @staticmethod
    def _enqueue_timeline_review_proxy(project_id, revision_id):
        """Queue the optional preview without ever delaying an editor save.

        Redis is an infrastructure dependency for the render queue.  When it
        is temporarily down Celery can spend many seconds retrying a publish;
        doing that inside ``transaction.on_commit`` used to make a slider look
        as if its change had not been saved.  The revision itself is already
        durable, so publish on a daemon thread and let the editor keep using
        source proxies until the continuous preview is available.
        """
        def dispatch():
            try:
                from .tasks import create_timeline_review_proxy

                create_timeline_review_proxy.apply_async(
                    args=(project_id, revision_id),
                    queue='media_previews',
                    ignore_result=True,
                    retry=False,
                )
            except Exception:
                logger.warning(
                    'Não foi possível agendar o proxy contínuo da revisão %s do projeto %s.',
                    revision_id, project_id,
                )

        try:
            Thread(
                target=dispatch,
                name=f'timeline-preview-{revision_id}',
                daemon=True,
            ).start()
            return True
        except Exception:
            logger.warning(
                'Não foi possível iniciar o agendamento do proxy contínuo da revisão %s do projeto %s.',
                revision_id, project_id,
            )
            return False

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
