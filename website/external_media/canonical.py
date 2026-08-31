"""Representacoes canônicas compatíveis com o pipeline legado.

Este módulo descreve a edição sem executar FFmpeg. Os campos legados em
``ExternalMediaProject.configuration`` continuam sendo a fonte de
compatibilidade durante a migração.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from django.utils import timezone

from website.models.external_media import MediaTemplatePlugin
from .speech_edit import SpeechEditPlan


SOURCE_MANIFEST_SCHEMA = 'connect.source_manifest.v1'
EDIT_DECISIONS_SCHEMA = 'connect.edit_decisions.v1'


def _int(value, default=0):
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def normalize_edit_ranges(ranges, protected_ranges=(), minimum_duration_ms=80):
    """Clamp, sort and merge edit ranges without allowing protected spans.

    Input stays deliberately data-oriented so producers can evolve without a
    class hierarchy. The output keeps the metadata of the highest-priority
    overlapping range, while guaranteeing deterministic non-overlapping cuts.
    """
    protected = sorted(
        (_int(item.get('start_ms')), _int(item.get('end_ms')))
        for item in (protected_ranges or ())
        if _int(item.get('end_ms')) > _int(item.get('start_ms'))
    )
    fragments = []
    for item in ranges or ():
        start, end = _int(item.get('start_ms')), _int(item.get('end_ms'))
        if end <= start:
            continue
        pieces = [(start, end)]
        for protected_start, protected_end in protected:
            remaining = []
            for left, right in pieces:
                if protected_end <= left or protected_start >= right:
                    remaining.append((left, right))
                else:
                    if left < protected_start:
                        remaining.append((left, protected_start))
                    if protected_end < right:
                        remaining.append((protected_end, right))
            pieces = remaining
        for left, right in pieces:
            if right - left >= minimum_duration_ms:
                fragments.append({**item, 'start_ms': left, 'end_ms': right})
    priority = {'background_voice': 3, 'filler': 2, 'silence': 1}
    result = []
    for item in sorted(fragments, key=lambda value: (value['start_ms'], value['end_ms'], value.get('kind', ''))):
        if result and item['start_ms'] <= result[-1]['end_ms']:
            previous = result[-1]
            preferred = item if priority.get(item.get('kind'), 0) > priority.get(previous.get('kind'), 0) else previous
            result[-1] = {
                **preferred,
                'start_ms': previous['start_ms'],
                'end_ms': max(previous['end_ms'], item['end_ms']),
            }
        else:
            result.append(item)
    return result


class ProjectProcessingState:
    """Typed access to the compatible JSON state stored on a project."""

    def __init__(self, project):
        self.project = project
        self.data = dict(project.configuration or {})

    def get_speech_edit_plan(self):
        return self.data.get('speech_edit_plan')

    def set_speech_edit_plan(self, value):
        self.data['speech_edit_plan'] = value

    def get_background_voice_plan(self):
        return self.data.get('background_voice_plan')

    def set_background_voice_plan(self, value):
        self.data['background_voice_plan'] = value

    def get_noise_analysis_plan(self):
        return self.data.get('noise_analysis_plan')

    def set_noise_analysis_plan(self, value):
        self.data['noise_analysis_plan'] = value

    def get_noise_reduction_decisions(self):
        return self.data.get('noise_reduction_decisions')

    def set_noise_reduction_decisions(self, value):
        self.data['noise_reduction_decisions'] = value

    def get_auto_reframe_plans(self):
        return self.data.get('auto_reframe_plans') or []

    def set_auto_reframe_plans(self, value):
        self.data['auto_reframe_plans'] = value or []

    def get_source_manifest(self):
        return self.data.get('source_manifest')

    def set_source_manifest(self, value):
        self.data['source_manifest'] = value

    def get_edit_decision_set(self):
        return self.data.get('edit_decision_set')

    def set_edit_decision_set(self, value):
        self.data['edit_decision_set'] = value

    def apply(self):
        self.project.configuration = self.data
        return self.data


class EffectiveCapabilities:
    """Single compatibility-preserving interpretation of template capabilities."""

    @classmethod
    def resolve(cls, project):
        configured = (project.configuration or {}).get('plugins', {})
        ignored_codes = {
            MediaTemplatePlugin.Code.SUBTITLE_PT,
            MediaTemplatePlugin.Code.TRANSLATION_EN,
            MediaTemplatePlugin.Code.LUT,
            MediaTemplatePlugin.Code.INTRO,
            MediaTemplatePlugin.Code.OUTRO,
            MediaTemplatePlugin.Code.MUSIC,
        }
        result = []
        for plugin in project.template_version.plugins.filter(is_enabled=True).exclude(code__in=ignored_codes):
            if not plugin.user_can_override or configured.get(plugin.code, True):
                result.append(plugin)
        version = project.template_version
        # Existing versions default to enabled; newer templates may explicitly
        # opt out without relying on empty styles or language hacks.
        if version.subtitles_enabled:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.SUBTITLE_PT, configuration={}))
        if (
            version.subtitles_enabled
            and version.translated_subtitles_enabled
            and any(language != version.original_language for language in version.output_languages)
        ):
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.TRANSLATION_EN, configuration={}))
        if version.color_lut_id or version.lut_file:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.LUT, configuration={}))
        has_music = bool(
            (version.background_music_id and version.background_music and version.background_music.audio_file)
            or version.music_file
        )
        if has_music:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.MUSIC, configuration={}))
        if version.dialogue_processing_enabled:
            result.append(SimpleNamespace(code='dialogue_processing', configuration={}))
        if version.audio_noise_cleanup_enabled:
            result.append(SimpleNamespace(code='audio_noise_cleanup', configuration=version.audio_noise_cleanup_config or {}))
        if version.audio_mixing_enabled and has_music:
            result.append(SimpleNamespace(code='audio_mixing', configuration={}))
        if version.audio_mastering_enabled:
            result.append(SimpleNamespace(code='audio_mastering', configuration={}))
        return result


class SourceManifestBuilder:
    """Resolves every source once, preserving the legacy assembly ordering."""

    @classmethod
    def build(cls, project, processing_job_id=None):
        capabilities = {item.code for item in EffectiveCapabilities.resolve(project)}
        version = project.template_version
        sources, position = [], 0

        def append(kind, source_id, field, *, role, block=None, custom_block=None, media=None):
            nonlocal position
            if not field:
                return
            entity = custom_block or block
            sources.append({
                'id': source_id,
                'kind': kind,
                'role': role,
                'position': position,
                'block_id': entity.pk if entity else None,
                'block_type': 'custom' if custom_block else ('template' if block else None),
                'block_key': f'custom-{custom_block.pk}' if custom_block else (block.key if block else ''),
                'block_name': entity.name if entity else role,
                'source_reference': str(getattr(media, 'pk', '') or getattr(block, 'pk', '') or getattr(custom_block, 'pk', '') or role),
                'storage_name': field.name,
                'filename': (getattr(media, 'original_filename', '') or field.name.rsplit('/', 1)[-1]),
                'trim': {
                    'start_ms': _int(getattr(media, 'trim_start_ms', 0)),
                    'end_ms': getattr(media, 'trim_end_ms', None),
                },
                'duration_ms': getattr(media, 'duration_ms', None),
                'metadata': {
                    'skip_extra_processing': bool(getattr(block, 'skip_extra_processing', False)),
                    'remove_background_voice': bool(getattr(block, 'remove_background_voice', False)),
                    'media_role': getattr(media, 'media_role', 'CAMERA'),
                    'camera_role': getattr(media, 'camera_role', 'PRIMARY'),
                    'camera_label': getattr(media, 'camera_label', '') or '',
                    'camera_hint': getattr(media, 'camera_hint', 'AUTO'),
                    'take_position': getattr(media, 'position', None),
                    # Until a camera-selection decision exists, only the primary
                    # source enters the legacy sequential renderer.
                    'render_enabled': getattr(media, 'camera_role', 'PRIMARY') == 'PRIMARY',
                },
            })
            position += 1

        # Keep the current capability semantics; intro/outro remain absent until
        # a template enables them through the same effective resolver.
        if version.intro_video and MediaTemplatePlugin.Code.INTRO in capabilities:
            append('template_intro', f'template-intro-{version.pk}', version.intro_video, role='intro')

        media_by_block, media_by_custom = {}, {}
        for item in project.block_media.select_related('block', 'custom_block').all().order_by('position', 'camera_order', 'pk'):
            if item.block_id:
                media_by_block.setdefault(item.block_id, []).append(item)
            elif item.custom_block_id:
                media_by_custom.setdefault(item.custom_block_id, []).append(item)
        block_entries = []
        for block in version.blocks.all().order_by('order', 'pk'):
            items = media_by_block.get(block.pk, [])
            if items:
                for item in items:
                    block_entries.append(('upload', f'project-media-{item.pk}', item.file, 'main', block, None, item))
            elif block.default_video:
                block_entries.append(('template_default', f'template-default-{block.pk}', block.default_video, 'default', block, None, None))
        for custom in project.custom_blocks.all().order_by('position', 'pk'):
            for item in media_by_custom.get(custom.pk, []):
                block_entries.append(('custom_upload', f'project-media-{item.pk}', item.file, 'main', None, custom, item))
        configured = (project.configuration or {}).get('block_order') or []
        if configured:
            order_index = {str(value): index for index, value in enumerate(configured)}
            decorated = []
            for original_index, entry in enumerate(block_entries):
                _, _, _, _, block, custom, _ = entry
                key = f'c-{custom.pk}' if custom else f't-{block.pk}'
                decorated.append((order_index.get(key, len(order_index)), original_index, entry))
            block_entries = [entry for _, _, entry in sorted(decorated)]
        for kind, source_id, field, role, block, custom, media in block_entries:
            append(kind, source_id, field, role=role, block=block, custom_block=custom, media=media)
        if version.outro_video and MediaTemplatePlugin.Code.OUTRO in capabilities:
            append('template_outro', f'template-outro-{version.pk}', version.outro_video, role='outro')
        multicam_groups = {}
        for source in sources:
            metadata = source.get('metadata') or {}
            if metadata.get('media_role') != 'CAMERA' or not source.get('block_id'):
                continue
            group_id = (
                f"{source['block_type']}-{source['block_id']}-take-"
                f"{metadata.get('take_position') or source['position']}"
            )
            group = multicam_groups.setdefault(group_id, {
                'id': group_id,
                'block_id': source['block_id'],
                'block_type': source['block_type'],
                'reference_source_id': None,
                'source_ids': [],
            })
            group['source_ids'].append(source['id'])
            if metadata.get('camera_role') == 'PRIMARY':
                group['reference_source_id'] = source['id']
        return {
            'schema': SOURCE_MANIFEST_SCHEMA,
            'project_id': str(project.public_id),
            'template_version_id': str(version.pk),
            'processing_job_id': str(processing_job_id) if processing_job_id else None,
            'created_at': timezone.now().isoformat(),
            'sources': sources,
            'multicam_groups': [
                group for group in multicam_groups.values() if len(group['source_ids']) > 1
            ],
        }

    @staticmethod
    def resolve_field(project, source):
        """Returns the original Django file field referenced by a manifest item."""
        kind, reference = source['kind'], str(source.get('source_reference') or '')
        if kind in {'upload', 'custom_upload'}:
            item = project.block_media.select_related('block', 'custom_block').get(pk=reference)
            return item.file
        version = project.template_version
        if kind == 'template_default':
            return version.blocks.get(pk=reference).default_video
        if kind == 'template_intro':
            return version.intro_video
        if kind == 'template_outro':
            return version.outro_video
        raise ValueError(f'Tipo de source não suportado: {kind}')


class EditDecisionSetBuilder:
    """Builds a versioned snapshot from the formats produced by the legacy code."""

    @classmethod
    def build(cls, project, source_manifest=None, processing_job_id=None):
        state = ProjectProcessingState(project)
        configuration = state.data
        operations = []

        background = SpeechEditPlan.from_dict(state.get_background_voice_plan())

        def add_plan(payload, producer, map_to_original=False):
            for cut in (payload or {}).get('cuts', []):
                start, end = _int(cut.get('start_ms')), _int(cut.get('end_ms'))
                if map_to_original and background.cuts:
                    start, end = background.source_time(start), background.source_time(end)
                operations.append({
                    'id': f'{producer}-{len(operations) + 1}', 'type': 'remove_segment',
                    'source_id': 'project-master', 'source_in_ms': start,
                    'source_out_ms': end, 'origin': 'AUTO',
                    'producer': producer, 'producer_version': 'legacy',
                    'reason': cut.get('label') or cut.get('kind'), 'confidence': None,
                    'metadata': {'kind': cut.get('kind', 'silence'), 'coordinate_space': 'project_timeline'},
                })

        add_plan(state.get_background_voice_plan(), 'background_voice')
        add_plan(state.get_speech_edit_plan(), 'speech_edit', map_to_original=True)
        for item in configuration.get('protected_block_ranges') or []:
            operations.append({
                'id': f'protected-{len(operations) + 1}', 'type': 'protected_range',
                'source_id': 'project-master', 'source_in_ms': _int(item.get('start_ms')),
                'source_out_ms': _int(item.get('end_ms')), 'origin': 'AUTO',
                'producer': 'assembly', 'producer_version': 'legacy', 'reason': 'skip_extra_processing',
                'confidence': None, 'metadata': {'block_key': item.get('block_key')},
            })
        source_ids = [item['id'] for item in (source_manifest or {}).get('sources', [])]
        for index, plan in enumerate(state.get_auto_reframe_plans()):
            if plan and plan.get('plan'):
                operations.append({
                    'id': f'reframe-{len(operations) + 1}', 'type': 'reframe',
                    'source_id': source_ids[index] if index < len(source_ids) else f'source-index-{index}',
                    'origin': 'AUTO', 'producer': 'auto_reframe', 'producer_version': configuration.get('auto_reframe_plan_version', 'legacy'),
                    'reason': 'face_tracking', 'confidence': None, 'plan_reference': plan,
                    'metadata': {},
                })
        version = project.template_version
        lut_file = version.color_lut.lut_file if version.color_lut_id and version.color_lut.lut_file else version.lut_file
        if lut_file:
            operations.append({
                'id': f'color-{len(operations) + 1}', 'type': 'color', 'source_id': 'project-master',
                'origin': 'AUTO', 'producer': 'template', 'producer_version': 'legacy',
                'reason': 'template_lut', 'confidence': None,
                'metadata': {'lut_file': lut_file.name, 'intensity': version.lut_intensity},
            })
        if version.background_music_id or version.music_file:
            operations.append({
                'id': f'audio-{len(operations) + 1}', 'type': 'audio_automation', 'source_id': 'project-master',
                'origin': 'AUTO', 'producer': 'template', 'producer_version': 'legacy',
                'reason': 'background_music', 'confidence': None,
                'metadata': {'music_volume': version.music_volume, 'ducking': bool(version.audio_ducking_enabled)},
            })
        for index, decision in enumerate(configuration.get('noise_reduction_decisions') or []):
            start_ms = _int(decision.get('start_ms'))
            end_ms = _int(decision.get('end_ms'))
            if decision.get('mode') != 'GLOBAL' and end_ms <= start_ms:
                continue
            operations.append({
                'id': f'noise-{index + 1}',
                'type': 'audio_noise_reduction',
                'source_id': 'project-master',
                'source_in_ms': start_ms,
                'source_out_ms': end_ms if end_ms > start_ms else None,
                'origin': 'AUTO',
                'producer': 'auto_noise_analysis',
                'producer_version': '1',
                'reason': decision.get('label') or decision.get('noise_type') or 'noise',
                'confidence': decision.get('confidence'),
                'enabled': bool(decision.get('enabled', False)),
                'metadata': {
                    'noise_type': decision.get('noise_type'),
                    'mode': decision.get('mode', 'LOCAL'),
                    'strength': decision.get('strength', 'LIGHT'),
                    'source': decision.get('source', 'AUTO_NOISE_ANALYSIS'),
                    'speech_overlap': bool(decision.get('speech_overlap')),
                    'recommended_action': decision.get('recommended_action'),
                    'label': decision.get('label') or '',
                    'event_index': decision.get('event_index'),
                },
            })
        return {
            'schema': EDIT_DECISIONS_SCHEMA,
            'project_id': str(project.public_id),
            'processing_job_id': str(processing_job_id) if processing_job_id else None,
            'created_at': timezone.now().isoformat(),
            'operations': operations,
        }
