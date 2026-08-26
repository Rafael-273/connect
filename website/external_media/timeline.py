from __future__ import annotations

import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .audio_mixing import DuckingSettings, build_ducking_envelope, group_speech_blocks
from .ffmpeg_runner import FFmpegRunner
from .speech_edit import SpeechEditPlan


CAPABILITY_MATRIX = {
    'cuts': {'exportable': True, 'strategy': 'native'},
    'source_in_out': {'exportable': True, 'strategy': 'native'},
    'music': {'exportable': True, 'strategy': 'separate_track'},
    'volume_automation': {'exportable': True, 'strategy': 'keyframes'},
    'auto_reframe': {'exportable': 'partial', 'strategy': 'transform_keyframes'},
    'captions': {'exportable': True, 'strategy': 'srt_editable_titles_and_alpha_overlay'},
    'caption_style': {'exportable': True, 'strategy': 'pre_rendered_alpha_overlay'},
    'lut': {'exportable': 'partial', 'strategy': 'asset_and_metadata'},
    'dialogue_processing': {'exportable': 'partial', 'strategy': 'separate_dialogue_source_plus_metadata'},
    'mastering': {'exportable': False, 'strategy': 'recommended_after_premiere'},
    'complex_motion': {'exportable': 'partial', 'strategy': 'pre_render_when_available'},
}


@dataclass(frozen=True)
class TimelineSource:
    asset_id: str
    field_file: object
    relative_path: str
    original_name: str
    role: str
    block_id: int | None = None
    block_key: str = ''
    block_name: str = ''
    trim_start_ms: int = 0
    trim_end_ms: int | None = None
    skip_extra_processing: bool = False


class KeyframeSimplifier:
    """Douglas-Peucker vetorial para deixar movimentos editáveis e manejáveis."""

    @classmethod
    def simplify(cls, keyframes, tolerance=1.5, max_points=64):
        if len(keyframes) <= 2:
            return list(keyframes)
        points = cls._douglas_peucker(list(keyframes), float(tolerance))
        if len(points) <= max_points:
            return points
        last = len(points) - 1
        indices = sorted({round(index * last / (max_points - 1)) for index in range(max_points)})
        return [points[index] for index in indices]

    @classmethod
    def _douglas_peucker(cls, points, tolerance):
        if len(points) <= 2:
            return points
        first, last = points[0], points[-1]
        span = max(1, last['time_ms'] - first['time_ms'])
        greatest, greatest_index = 0.0, 0
        for index, point in enumerate(points[1:-1], start=1):
            ratio = (point['time_ms'] - first['time_ms']) / span
            expected = {
                key: first.get(key, 0) + (last.get(key, 0) - first.get(key, 0)) * ratio
                for key in ('x', 'y', 'scale')
            }
            distance = math.sqrt(sum((point.get(key, 0) - expected[key]) ** 2 for key in expected))
            if distance > greatest:
                greatest, greatest_index = distance, index
        if greatest <= tolerance:
            return [first, last]
        left = cls._douglas_peucker(points[:greatest_index + 1], tolerance)
        right = cls._douglas_peucker(points[greatest_index:], tolerance)
        return left[:-1] + right


class InternalTimelineBuilder:
    """Converte decisões do pipeline em dados neutros, preservando os sources originais."""

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def build(self, project, package_root: Path):
        for folder in ('Media', 'Audio', 'Captions', 'Graphics', 'LUTs', 'Metadata', 'Project'):
            (package_root / folder).mkdir(parents=True, exist_ok=True)
        sources = self._sources(project)
        assets = []
        local_sources = []
        for index, source in enumerate(sources, start=1):
            suffix = Path(source.original_name).suffix.lower() or '.mov'
            safe_name = self._safe_name(Path(source.original_name).stem)
            relative_path = f'Media/{index:03d}_{safe_name}{suffix}'
            local_path = package_root / relative_path
            self._copy_field(source.field_file, local_path)
            metadata = self._probe(local_path)
            source = TimelineSource(**{**source.__dict__, 'relative_path': relative_path})
            local_sources.append((source, metadata))
            assets.append({
                'id': source.asset_id,
                'type': 'video',
                'role': source.role,
                'name': source.original_name,
                'path': f'./{relative_path}',
                # O áudio segue em A1 como WAV independente para evitar duplicação.
                'has_audio': False,
                **metadata,
            })

        sequence = self._sequence(project, local_sources)
        cuts = SpeechEditPlan.from_dict((project.configuration or {}).get('speech_edit_plan'))
        video_clips, decisions, markers = self._video_clips(project, local_sources, cuts, sequence)
        duration_ms = max((clip['timeline_out_ms'] for clip in video_clips), default=0)
        dialogue_assets, dialogue_clips = self._dialogue_assets(local_sources, video_clips, package_root)
        assets.extend(dialogue_assets)
        audio_tracks = [
            {
                'id': 'A1', 'name': 'Dialogue (áudio original separado)',
                'role': 'dialogue', 'clips': dialogue_clips,
            },
            {'id': 'A2', 'name': 'Music', 'role': 'music', 'clips': []},
            {'id': 'A3', 'name': 'SFX', 'role': 'sfx', 'clips': []},
            {'id': 'A4', 'name': 'Ambience', 'role': 'ambience', 'clips': []},
        ]

        music_asset, music_track = self._music(project, package_root, duration_ms)
        if music_asset:
            assets.append(music_asset)
            audio_tracks[1] = music_track

        lut = self._lut(project, package_root)
        if lut:
            assets.append(lut)

        captions, caption_assets = self._captions(project, package_root)
        assets.extend(caption_assets)
        caption_overlay_asset, caption_overlay_track = self._styled_caption_overlay(
            project, package_root, sequence, duration_ms,
        )
        if caption_overlay_asset:
            assets.append(caption_overlay_asset)
        effects = self._effects(project, lut)
        compatibility = self._compatibility(project, effects)
        timeline = {
            'schema': 'connect.internal_timeline.v1',
            'project': {
                'id': str(project.public_id),
                'name': project.name,
                'template': project.template_version.template.name,
                'created_by': project.created_by.name,
            },
            'sequence': {**sequence, 'duration_ms': duration_ms},
            'assets': assets,
            'video_tracks': [
                {'id': 'V1', 'name': 'Main Video', 'role': 'main', 'clips': video_clips},
                {'id': 'V2', 'name': 'Overlays', 'role': 'overlay', 'clips': []},
                {'id': 'V3', 'name': 'Titles', 'role': 'title', 'clips': []},
                {'id': 'V4', 'name': 'Graphics', 'role': 'graphic', 'clips': []},
            ] + ([caption_overlay_track] if caption_overlay_track else []),
            'audio_tracks': audio_tracks,
            'clips': video_clips,
            'captions': captions,
            'transitions': [],
            'effects': effects,
            'keyframes': [
                keyframe for clip in video_clips
                for effect in clip.get('effects', [])
                for keyframe in effect.get('keyframes', [])
            ],
            'markers': markers,
            'decisions': decisions,
            'capability_matrix': CAPABILITY_MATRIX,
            'compatibility': compatibility,
        }
        return timeline

    def _sources(self, project):
        version = project.template_version
        enabled_codes = set(version.plugins.filter(is_enabled=True).values_list('code', flat=True))
        result = []
        if version.intro_video and 'intro' in enabled_codes:
            result.append(TimelineSource('video_intro', version.intro_video, '', Path(version.intro_video.name).name, 'intro'))
        media_by_block = {}
        for item in project.block_media.select_related('block').order_by('block__order', 'position', 'pk'):
            media_by_block.setdefault(item.block_id, []).append(item)
        index = 0
        for block in version.blocks.all().order_by('order', 'pk'):
            items = media_by_block.get(block.pk, [])
            if items:
                for item in items:
                    index += 1
                    result.append(TimelineSource(
                        f'video_{index}', item.file, '', item.original_filename or Path(item.file.name).name,
                        'main', block.pk, block.key, block.name, int(item.trim_start_ms or 0),
                        int(item.trim_end_ms) if item.trim_end_ms else None, block.skip_extra_processing,
                    ))
            elif block.default_video:
                index += 1
                result.append(TimelineSource(
                    f'video_{index}', block.default_video, '', Path(block.default_video.name).name,
                    'default', block.pk, block.key, block.name, 0, None, block.skip_extra_processing,
                ))
        if version.outro_video and 'outro' in enabled_codes:
            result.append(TimelineSource('video_outro', version.outro_video, '', Path(version.outro_video.name).name, 'outro'))
        return result

    def _sequence(self, project, sources):
        preset = project.template_version.preset
        first = sources[0][1] if sources else {}
        fps = float(first.get('fps') or 30.0)
        return {
            'name': project.name,
            'width': int(preset.width or first.get('width') or 1920),
            'height': int(preset.height or first.get('height') or 1080),
            'fps': fps,
            'timebase': max(1, round(fps)),
            'ntsc': abs(fps - round(fps)) > 0.01,
            'pixel_aspect_ratio': 'square',
            'audio_sample_rate': 48000,
            'audio_channels': 2,
        }

    def _video_clips(self, project, sources, plan, sequence):
        plan = plan if plan.duration_ms > 1 or plan.cuts else SpeechEditPlan((), sum(
            max(1, int(metadata['duration_ms']) - source.trim_start_ms)
            for source, metadata in sources
        ))
        plans = (project.configuration or {}).get('auto_reframe_plans') or []
        clips, markers, decisions = [], [], []
        source_cursor = 0
        timeline_cursor = 0
        clip_index = 0
        for source_index, (source, metadata) in enumerate(sources):
            full_duration = int(metadata['duration_ms'])
            source_in = min(full_duration, max(0, source.trim_start_ms))
            source_out = min(full_duration, source.trim_end_ms or full_duration)
            effective_duration = max(1, source_out - source_in)
            global_start, global_end = source_cursor, source_cursor + effective_duration
            kept = self._subtract_cuts(global_start, global_end, plan.cuts)
            first_timeline_start = timeline_cursor
            for keep_start, keep_end in kept:
                clip_index += 1
                duration = keep_end - keep_start
                local_start = keep_start - global_start
                local_end = keep_end - global_start
                effects = []
                plan_data = plans[source_index] if source_index < len(plans) else None
                if plan_data and plan_data.get('plan') and not source.skip_extra_processing:
                    transform = self._transform_effect(
                        plan_data, local_start, local_end, timeline_cursor, sequence, metadata,
                    )
                    if transform:
                        effects.append(transform)
                clips.append({
                    'id': f'clip_{clip_index}',
                    'asset_id': source.asset_id,
                    'name': source.block_name or source.original_name,
                    'role': source.role,
                    'block': {'id': source.block_id, 'key': source.block_key, 'name': source.block_name},
                    'timeline_in_ms': timeline_cursor,
                    'timeline_out_ms': timeline_cursor + duration,
                    'source_in_ms': source_in + local_start,
                    'source_out_ms': source_in + local_end,
                    'source_duration_ms': full_duration,
                    'effects': effects,
                    'audio_enabled': False,
                    'skip_extra_processing': source.skip_extra_processing,
                })
                timeline_cursor += duration
            label = source.block_name or ('INTRO' if source.role == 'intro' else 'OUTRO' if source.role == 'outro' else source.original_name)
            markers.append({'name': label, 'time_ms': first_timeline_start, 'block_key': source.block_key})
            source_cursor = global_end
        for cut in plan.cuts:
            decisions.append({
                'operation': 'filler_word_removal' if cut.kind == 'filler' else 'silence_removal',
                'source_start_ms': cut.start_ms,
                'source_end_ms': cut.end_ms,
                'reason': cut.label or cut.kind,
                'confidence': None,
            })
        for index, plan_data in enumerate(plans):
            if plan_data and plan_data.get('plan'):
                decisions.append({'operation': 'auto_reframe', 'source_index': index, 'confidence': None})
        return clips, decisions, markers

    def _dialogue_assets(self, sources, video_clips, package_root):
        """Extrai A1 por take, deixando vídeo, diálogo e música independentes."""
        assets, clips, asset_by_video = [], [], {}
        for index, (source, _metadata) in enumerate(sources, start=1):
            source_path = package_root / source.relative_path
            if not self._has_audio(source_path):
                continue
            safe_name = self._safe_name(Path(source.original_name).stem)
            relative = f'Audio/dialogue_{index:03d}_{safe_name}.wav'
            output = package_root / relative
            self.runner.run([
                settings.FFMPEG_BINARY, '-y', '-i', str(source_path),
                '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', str(output),
            ])
            asset_id = f'{source.asset_id}_dialogue'
            asset_by_video[source.asset_id] = asset_id
            assets.append({
                'id': asset_id, 'type': 'audio', 'role': 'dialogue',
                'name': f'{Path(source.original_name).stem} — diálogo',
                'path': f'./{relative}', **self._probe(output, audio_only=True),
            })
        for video_clip in video_clips:
            asset_id = asset_by_video.get(video_clip['asset_id'])
            if asset_id:
                clips.append({**video_clip, 'asset_id': asset_id, 'audio_enabled': True})
        return assets, clips

    @staticmethod
    def _subtract_cuts(start, end, cuts):
        segments = [(start, end)]
        for cut in cuts:
            if cut.end_ms <= start or cut.start_ms >= end:
                continue
            next_segments = []
            for left, right in segments:
                if cut.end_ms <= left or cut.start_ms >= right:
                    next_segments.append((left, right))
                    continue
                if cut.start_ms > left:
                    next_segments.append((left, min(right, cut.start_ms)))
                if cut.end_ms < right:
                    next_segments.append((max(left, cut.end_ms), right))
            segments = next_segments
        return [(left, right) for left, right in segments if right - left >= 1]

    def _transform_effect(self, payload, local_start, local_end, timeline_start, sequence, metadata):
        plan = payload['plan']
        crop_width = max(1, int(plan.get('crop_width') or metadata['width']))
        crop_height = max(1, int(plan.get('crop_height') or metadata['height']))
        scale = max(sequence['width'] / crop_width, sequence['height'] / crop_height) * 100
        points = []
        raw_keyframes = sorted(
            ({
                **item,
                'source_time_ms': round(float(item.get('time_seconds', 0)) * 1000),
            } for item in plan.get('keyframes', [])),
            key=lambda item: item['source_time_ms'],
        )
        for item in self._keyframes_for_interval(raw_keyframes, local_start, local_end):
            source_time = item['source_time_ms']
            crop_x, crop_y = float(item.get('x', 0)), float(item.get('y', 0))
            center_x = crop_x + crop_width / 2
            center_y = crop_y + crop_height / 2
            scale_factor = scale / 100
            points.append({
                'time_ms': timeline_start + source_time - local_start,
                # FCP 7 XML Basic Motion uses sequence pixel coordinates for Center.
                # Moving the scaled source in the inverse direction recreates the crop
                # selected by Auto Reframe without baking it into a new video.
                'x': round(
                    sequence['width'] / 2
                    - (center_x - metadata['width'] / 2) * scale_factor,
                    4,
                ),
                'y': round(
                    sequence['height'] / 2
                    - (center_y - metadata['height'] / 2) * scale_factor,
                    4,
                ),
                'scale': round(scale, 4),
                'source_crop_x': crop_x,
                'source_crop_y': crop_y,
            })
        if not points:
            return None
        return {
            'type': 'transform',
            'portability': 'PORTABLE_PARTIAL',
            'position_unit': 'sequence_pixels',
            'crop_width': crop_width,
            'crop_height': crop_height,
            'keyframes': KeyframeSimplifier.simplify(points),
        }

    @classmethod
    def _keyframes_for_interval(cls, keyframes, start_ms, end_ms):
        """Includes interpolated boundary points so every split clip has motion data."""
        if not keyframes:
            return []
        selected = [
            dict(item) for item in keyframes
            if start_ms <= item['source_time_ms'] <= end_ms
        ]
        for boundary in (start_ms, end_ms):
            if any(item['source_time_ms'] == boundary for item in selected):
                continue
            selected.append(cls._interpolate_keyframe(keyframes, boundary))
        unique = {item['source_time_ms']: item for item in selected}
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _interpolate_keyframe(keyframes, time_ms):
        if time_ms <= keyframes[0]['source_time_ms']:
            return {**keyframes[0], 'source_time_ms': time_ms}
        if time_ms >= keyframes[-1]['source_time_ms']:
            return {**keyframes[-1], 'source_time_ms': time_ms}
        for left, right in zip(keyframes, keyframes[1:]):
            if left['source_time_ms'] <= time_ms <= right['source_time_ms']:
                span = max(1, right['source_time_ms'] - left['source_time_ms'])
                ratio = (time_ms - left['source_time_ms']) / span
                return {
                    **left,
                    'source_time_ms': time_ms,
                    'x': float(left.get('x', 0)) + (float(right.get('x', 0)) - float(left.get('x', 0))) * ratio,
                    'y': float(left.get('y', 0)) + (float(right.get('y', 0)) - float(left.get('y', 0))) * ratio,
                }
        return {**keyframes[-1], 'source_time_ms': time_ms}

    def _music(self, project, package_root, duration_ms):
        version = project.template_version
        enabled = version.plugins.filter(is_enabled=True, code='music').exists()
        track = version.background_music
        field = track.audio_file if enabled and track and track.audio_file else version.music_file if enabled else None
        if not field:
            return None, None
        suffix = Path(field.name).suffix.lower() or '.wav'
        name = self._safe_name(track.name if track else Path(field.name).stem)
        relative = f'Audio/music_{name}{suffix}'
        local = package_root / relative
        self._copy_field(field, local)
        metadata = self._probe(local, audio_only=True)
        asset = {'id': 'audio_music', 'type': 'audio', 'role': 'music', 'name': name, 'path': f'./{relative}', **metadata}
        source_duration = max(1, int(metadata['duration_ms']))
        clips, cursor, index = [], 0, 0
        while cursor < duration_ms:
            index += 1
            used = min(source_duration, duration_ms - cursor)
            clips.append({
                'id': f'music_clip_{index}', 'asset_id': 'audio_music', 'name': name,
                'timeline_in_ms': cursor, 'timeline_out_ms': cursor + used,
                'source_in_ms': 0, 'source_out_ms': used,
            })
            cursor += used
        cues = []
        if project.render_job_id:
            track_source = project.render_job.subtitle_tracks.filter(is_source=True).prefetch_related('cues').first()
            cues = [(cue.start_ms, cue.end_ms) for cue in track_source.cues.all()] if track_source else []
        ducking_settings = DuckingSettings.from_config(version.audio_mixing_config)
        # Use the same speech-gap hold configured for the platform render. This keeps
        # the Premiere A2 automation from rising during short breaths or pauses.
        blocks = group_speech_blocks(cues, gap_threshold_ms=ducking_settings.speech_gap_hold_ms)
        duck_db = float(
            ((project.configuration or {}).get('audio_metrics') or {}).get('mixing', {}).get('duck_db')
            or (version.audio_mixing_config or {}).get('base_duck_db', 8)
        )
        envelope = build_ducking_envelope(
            blocks, duration_ms, 10 ** (-duck_db / 20), ducking_settings,
        ) if version.audio_mixing_enabled and version.audio_ducking_enabled else [(0, 1), (duration_ms / 1000, 1)]
        base_volume = max(0.0001, float(version.music_volume))
        automation = [
            {'time_ms': round(time_s * 1000), 'gain_db': round(20 * math.log10(max(0.0001, gain * base_volume)), 3)}
            for time_s, gain in envelope
        ]
        return asset, {'id': 'A2', 'name': 'Music', 'role': 'music', 'clips': clips, 'volume_automation': automation}

    def _captions(self, project, package_root):
        if not project.render_job_id:
            return [], []
        captions, assets = [], []
        for track in project.render_job.subtitle_tracks.prefetch_related('cues').all():
            cues = []
            srt_lines = []
            for cue in track.cues.all():
                cues.append({'start_ms': cue.start_ms, 'end_ms': cue.end_ms, 'text': cue.text})
                srt_lines.extend([
                    str(cue.cue_index),
                    f'{self._srt_time(cue.start_ms)} --> {self._srt_time(cue.end_ms)}',
                    cue.text, '',
                ])
            relative = f'Captions/{track.language}.srt'
            (package_root / relative).write_text('\n'.join(srt_lines), encoding='utf-8')
            style = project.render_job.subtitle_style if track.is_source else project.render_job.translated_subtitle_style
            captions.append({
                'language': track.language, 'is_source': track.is_source, 'path': f'./{relative}',
                'style': self._subtitle_style(style), 'cues': cues,
            })
            assets.append({'id': f'caption_{track.language}', 'type': 'caption', 'role': 'caption', 'name': f'{track.language}.srt', 'path': f'./{relative}'})
        return captions, assets

    def _styled_caption_overlay(self, project, package_root, sequence, duration_ms):
        """Creates a faithful, transparent reference layer for Premiere.

        FCP 7 XML's legacy Text generator cannot represent the subtitle renderer's
        full design (background alpha/padding, shadow, outline and exact position).
        The package therefore keeps editable title clips *and* includes this alpha
        movie on a separate track. Editors can lock it for an exact visual result or
        hide it when they want to rebuild the captions natively in Premiere.
        """
        job = getattr(project, 'render_job', None)
        if not job or duration_ms <= 0:
            return None, None
        tracks = list(
            job.subtitle_tracks.filter(language__in=job.output_languages)
            .prefetch_related('cues')
            .order_by('pk')
        )
        if not tracks:
            return None, None
        source_tracks = [track for track in tracks if track.is_source]
        source_track = source_tracks[0] if source_tracks else tracks[0]
        translated_tracks = [track for track in tracks if track.pk != source_track.pk]
        source_language = job.original_language or source_track.language
        source_style = job.subtitle_style
        translated_style = job.translated_subtitle_style or source_style
        if not source_style:
            return None, None

        version = project.template_version
        is_dual = (
            (version.default_settings or {}).get('language_mode') == 'translated'
            and bool(translated_tracks)
        )
        selected_tracks = [source_track, translated_tracks[0]] if is_dual else [source_track]
        suffix = 'bilingual' if is_dual else self._safe_name(source_track.language or 'source')
        # Keep the ASS beside the SRTs for audit/reuse in tools that understand
        # advanced subtitle styling; Premiere itself uses the alpha movie below.
        ass_relative = f'Captions/captions_{suffix}_styled.ass'
        movie_relative = f'Graphics/captions_{suffix}_styled.mov'
        ass_path = package_root / ass_relative
        movie_path = package_root / movie_relative

        # Reuse exactly the same ASS writer as the final platform render.
        from .services import SubtitleService

        subtitle_service = SubtitleService()
        if is_dual:
            subtitle_service.write_dual_ass(
                selected_tracks, ass_path, source_style,
                sequence['width'], sequence['height'], source_language, translated_style,
            )
        else:
            subtitle_service.write_ass(
                source_track, ass_path, source_style, sequence['width'], sequence['height'],
            )
        self._render_alpha_caption_movie(ass_path, movie_path, sequence, duration_ms)
        metadata = self._probe(movie_path)
        asset_id = f'caption_overlay_{suffix}'
        asset = {
            'id': asset_id,
            'type': 'video',
            'role': 'caption_overlay',
            'name': 'Legendas estilizadas (referência visual)',
            'path': f'./{movie_relative}',
            'has_audio': False,
            **metadata,
        }
        clip = {
            'id': f'{asset_id}_clip',
            'asset_id': asset_id,
            'name': asset['name'],
            'timeline_in_ms': 0,
            'timeline_out_ms': duration_ms,
            'source_in_ms': 0,
            'source_out_ms': duration_ms,
            'audio_enabled': False,
        }
        return asset, {
            'id': 'V5',
            'name': 'Legendas estilizadas (visual final)',
            'role': 'caption_overlay',
            'clips': [clip],
            'locked': True,
        }

    def _render_alpha_caption_movie(self, ass_path, movie_path, sequence, duration_ms):
        escaped_ass_path = str(ass_path).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
        fonts_dir = Path(settings.BASE_DIR) / 'static' / 'fonts' / 'subtitles'
        if fonts_dir.is_dir() and any(fonts_dir.glob('*.[ot]tf')):
            escaped_fonts_dir = str(fonts_dir).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
            ass_filter = f"ass='{escaped_ass_path}':fontsdir='{escaped_fonts_dir}'"
        else:
            ass_filter = f"ass='{escaped_ass_path}'"
        duration_seconds = max(0.04, duration_ms / 1000)
        # ProRes 4444 is broadly supported by Premiere and preserves the alpha channel.
        # The transparent source makes the exported layer sit naturally over V1.
        source = (
            f'color=c=black@0.0:s={sequence["width"]}x{sequence["height"]}:'
            f'r={sequence["fps"]}:d={duration_seconds},format=yuva444p10le'
        )
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', source,
            '-vf', ass_filter,
            '-an', '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le',
            '-movflags', '+faststart', str(movie_path),
        ])

    def _lut(self, project, package_root):
        version = project.template_version
        if not version.lut_file or not version.plugins.filter(is_enabled=True, code='lut').exists():
            return None
        relative = f'LUTs/{self._safe_name(Path(version.lut_file.name).stem)}{Path(version.lut_file.name).suffix.lower()}'
        self._copy_field(version.lut_file, package_root / relative)
        return {'id': 'lut_template', 'type': 'lut', 'role': 'color', 'name': Path(version.lut_file.name).name, 'path': f'./{relative}'}

    @staticmethod
    def _effects(project, lut):
        version = project.template_version
        effects = []
        if lut:
            effects.append({'type': 'lut', 'asset_id': lut['id'], 'intensity': 1.0, 'portability': 'NON_PORTABLE'})
        if version.dialogue_processing_enabled:
            effects.append({
                'type': 'dialogue_processing', 'portability': 'NON_PORTABLE',
                'configuration': version.dialogue_processing_config,
                'note': 'Áudio original incluído; reaplique EQ/compressão/de-esser no Premiere se desejado.',
            })
        if version.audio_mastering_enabled:
            effects.append({
                'type': 'mastering', 'portability': 'NON_PORTABLE',
                'profile': version.mastering_profile.name if version.mastering_profile else None,
                'note': 'Masterização final recomendada após o render do Premiere.',
            })
        return effects

    @staticmethod
    def _compatibility(project, effects):
        warnings = []
        if any(effect['type'] == 'lut' for effect in effects):
            warnings.append('O LUT foi incluído no pacote e documentado, mas pode precisar ser reaplicado no Premiere.')
        if any(effect['type'] == 'dialogue_processing' for effect in effects):
            warnings.append('EQ, compressão e de-esser são metadados não portáveis; o áudio original permanece editável.')
        if project.template_version.audio_mastering_enabled:
            warnings.append('A masterização final não foi aplicada destrutivamente. Use “Masterizar Vídeo” após o Premiere.')
        warnings.append(
            'As legendas têm uma camada ProRes 4444 transparente com o visual final; '
            'os títulos e SRT permanecem editáveis em trilhas separadas.'
        )
        return {'warnings': warnings, 'matrix': CAPABILITY_MATRIX}

    def _probe(self, path, audio_only=False):
        duration = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(path),
        ]).strip()
        metadata = {'duration_ms': max(1, round(float(duration) * 1000))}
        if audio_only:
            return metadata
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,r_frame_rate', '-of', 'csv=s=x:p=0', str(path),
        ]).strip().split('x')
        width, height = int(output[0]), int(output[1])
        numerator, denominator = (output[2].split('/', 1) + ['1'])[:2]
        metadata.update({'width': width, 'height': height, 'fps': float(numerator) / max(1, float(denominator))})
        return metadata

    def _has_audio(self, path):
        return bool(self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path),
        ]).strip())

    @staticmethod
    def _copy_field(field_file, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with field_file.open('rb') as source, destination.open('wb') as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

    @staticmethod
    def _safe_name(value):
        value = re.sub(r'[^A-Za-z0-9._-]+', '_', value or 'asset').strip('._')
        return value[:100] or 'asset'

    @staticmethod
    def _srt_time(milliseconds):
        milliseconds = max(0, int(milliseconds))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'

    @staticmethod
    def _subtitle_style(style):
        if not style:
            return None
        return {
            'id': style.pk, 'name': style.name, 'font': style.font_name,
            'font_weight': style.font_weight, 'font_size': style.font_size, 'color': style.primary_color,
            'primary_opacity': style.primary_opacity,
            'alignment': style.alignment, 'margin_bottom': style.margin_bottom,
            'background_enabled': style.background_enabled,
            'background_color': style.background_color,
            'background_opacity': style.background_opacity,
            'background_padding_x': style.background_padding_x,
            'background_padding_y': style.background_padding_y,
            'background_height_percent': style.background_height_percent,
            'background_radius': style.background_radius,
            'outline_color': style.outline_color,
            'outline_width': style.outline_width,
            'shadow': style.shadow,
            'shadow_angle': style.shadow_angle,
            'shadow_size': style.shadow_size,
            'shadow_blur': style.shadow_blur,
            'shadow_opacity': style.shadow_opacity,
        }
