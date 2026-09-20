"""Renderer-neutral B-roll decisions, automatic placement and final composition."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.urls import reverse

from website.ai import AIServiceError, get_ai_service
from website.models.external_media import MediaTemplatePlugin, ProjectBrollAsset, SubtitleCue

from .ffmpeg_runner import FFmpegRunner


logger = logging.getLogger(__name__)


class BrollTimelineService:
    DEFAULTS = {
        'enabled': True,
        'allow_video': True,
        'allow_image': True,
        'allow_overlay': True,
        'auto_placement': True,
        'smart_crop': False,
        'image_motion': 'AUTO',
        'default_transition': 'FADE',
    }

    @classmethod
    def configuration(cls, project):
        plugin = project.template_version.plugins.filter(
            code=MediaTemplatePlugin.Code.BROLL, is_enabled=True,
        ).first()
        return {**cls.DEFAULTS, **(plugin.configuration or {})} if plugin else {'enabled': False}

    @classmethod
    def ensure_auto_decisions(cls, project, ai_service=None):
        config = cls.configuration(project)
        assets = list(project.broll_assets.filter(is_enabled=True).select_related('block', 'custom_block'))
        if not config.get('enabled') or not assets:
            return []
        existing = {
            str(item.get('asset_id')): item
            for item in ((project.configuration or {}).get('broll_decisions') or [])
        }
        missing = [asset for asset in assets if str(asset.public_id) not in existing]
        if missing:
            generated = cls._generate(project, missing, config, ai_service=ai_service)
            existing.update({str(item['asset_id']): item for item in generated})
            project.configuration = {
                **(project.configuration or {}),
                'broll_decisions': list(existing.values()),
            }
            project.save(update_fields=['configuration', 'update_at'])
        return [deepcopy(existing[str(asset.public_id)]) for asset in assets if str(asset.public_id) in existing]

    @classmethod
    def purge_asset_references(cls, project, asset, member=None):
        """Defensive cleanup for when a B-roll asset is removed directly from
        the block media manager instead of through the interactive review's
        own remove-broll action.

        Rendering already tolerates a decision that points at a missing
        asset (it is skipped, see ``compose``/``BrollRenderService.apply``),
        but leaving the stale reference around means the project keeps
        "remembering" media that no longer exists on disk. Purge it from the
        flat decision list and, when there is an interactive timeline
        revision, drop the clip from it too so a stale ``asset_id`` never
        has to rely on the render-time fallback.
        """
        asset_id = str(asset.public_id)

        decisions = (project.configuration or {}).get('broll_decisions') or []
        remaining = [item for item in decisions if str(item.get('asset_id')) != asset_id]
        if len(remaining) != len(decisions):
            project.configuration = {**(project.configuration or {}), 'broll_decisions': remaining}
            project.save(update_fields=['configuration', 'update_at'])

        if not member:
            return
        revision = project.current_timeline_revision
        if not revision:
            return
        stale_ids = [
            item.get('id') for item in (revision.timeline.get('brolls') or [])
            if str(item.get('asset_id')) == asset_id
        ]
        if not stale_ids:
            return
        # Imported lazily: preview.py imports BrollTimelineService, so a
        # module-level import here would create a circular import.
        from .preview import TimelineRevisionService

        for broll_id in stale_ids:
            try:
                TimelineRevisionService.mutate_broll(project, member, broll_id, {}, delete=True)
            except ValueError:
                logger.warning(
                    'broll_purge_stale_reference_failed project=%s asset_id=%s broll_id=%s',
                    project.public_id, asset_id, broll_id,
                )

    @classmethod
    def compose(cls, project, clips, duration_ms, time_mapper=None):
        config = cls.configuration(project)
        if not config.get('enabled'):
            return [], []
        assets = {
            str(asset.public_id): asset
            for asset in project.broll_assets.filter(is_enabled=True).select_related('block', 'custom_block')
        }
        decisions = (project.configuration or {}).get('broll_decisions') or []
        block_ranges = cls._clip_block_ranges(clips)
        result = []
        for decision in decisions:
            asset = assets.get(str(decision.get('asset_id')))
            if not asset:
                logger.warning(
                    'broll_decision_skipped_missing_asset project=%s asset_id=%s',
                    project.public_id, decision.get('asset_id'),
                )
                continue
            block_key = cls._asset_block_key(asset)
            block_start, block_end = block_ranges.get(block_key, (0, duration_ms))
            raw_start = int(decision.get('start_ms') or block_start)
            raw_end = int(decision.get('end_ms') or raw_start + 5000)
            mapped_start = time_mapper(raw_start) if time_mapper else raw_start
            mapped_end = time_mapper(raw_end) if time_mapper else raw_end
            start = max(block_start, min(mapped_start, block_end))
            end = min(block_end, max(start + 1, mapped_end))
            payload = cls._normalize_decision(asset, decision, start, end, config)
            result.append(payload)
        asset_payload = [
            {
                'id': f'broll-asset-{asset.public_id}',
                'asset_id': str(asset.public_id),
                'name': asset.original_filename,
                'description': asset.description,
                'media_type': asset.media_type,
                'duration_ms': asset.duration_ms,
                'url': reverse('external_media_project_preview_broll_asset', kwargs={
                    'public_id': project.public_id, 'asset_id': asset.public_id,
                }),
            }
            for asset in assets.values()
        ]
        return result, asset_payload

    @classmethod
    def _generate(cls, project, assets, config, ai_service=None):
        ranges = cls._configured_block_ranges(project)
        cues = list(SubtitleCue.objects.filter(
            track__job=project.render_job, track__is_source=True,
        ).values('start_ms', 'end_ms', 'text').order_by('start_ms')) if project.render_job_id else []
        fallback = [cls._fallback_decision(asset, ranges, cues, config, index) for index, asset in enumerate(assets)]
        if not config.get('auto_placement') or not cues:
            return fallback
        request_assets = []
        for asset in assets:
            block_key = cls._asset_block_key(asset)
            start, end = ranges.get(block_key, (0, max((cue['end_ms'] for cue in cues), default=5000)))
            request_assets.append({
                'asset_id': str(asset.public_id), 'media_type': asset.media_type,
                'description': asset.description or asset.original_filename,
                'duration_ms': asset.duration_ms, 'block_key': block_key,
                'allowed_range_ms': [start, end],
            })
        prompt = json.dumps({'assets': request_assets, 'transcript': cues}, ensure_ascii=False)
        instructions = """Você é um editor de vídeo. Posicione cada asset de B-roll no momento semanticamente mais relevante da transcrição, sempre dentro de allowed_range_ms. Escolha FULLSCREEN para fotos/vídeos ilustrativos e OVERLAY para QR Codes, logos e cards. Não sobreponha assets quando puder evitar. Responda somente JSON válido: {"placements":[{"asset_id":"uuid","start_ms":0,"end_ms":5000,"source_in_ms":0,"source_out_ms":5000,"display_mode":"FULLSCREEN|OVERLAY","confidence":0.0}]}."""
        try:
            raw = (ai_service or get_ai_service()).generate_text(
                prompt, instructions=instructions,
                model=getattr(settings, 'EXTERNAL_MEDIA_BROLL_MODEL', settings.EXTERNAL_MEDIA_OFF_CONTEXT_MODEL),
                max_output_tokens=max(800, len(assets) * 180),
            )
            data = cls._parse_json(raw)
            by_id = {str(item.get('asset_id')): item for item in data.get('placements', [])}
            return [
                cls._validated_ai_decision(asset, by_id.get(str(asset.public_id)), ranges, config, fallback[index])
                for index, asset in enumerate(assets)
            ]
        except (AIServiceError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.exception('broll_auto_placement_failed project=%s', project.public_id)
            return fallback

    @classmethod
    def _fallback_decision(cls, asset, ranges, cues, config, index):
        block_start, block_end = ranges.get(cls._asset_block_key(asset), (0, max((c['end_ms'] for c in cues), default=5000)))
        words = set(re.findall(r'[\wÀ-ÿ]{4,}', (asset.description or asset.original_filename).lower()))
        relevant = [cue for cue in cues if block_start <= cue['start_ms'] < block_end]
        best = max(relevant, key=lambda cue: len(words & set(re.findall(r'[\wÀ-ÿ]{4,}', cue['text'].lower()))), default=None)
        start = int(best['start_ms'] if best else block_start + index * 1000)
        duration = min(6000, max(2500, int(asset.duration_ms or 5000)))
        start = min(max(block_start, start), max(block_start, block_end - 1))
        end = min(block_end, start + duration)
        description = (asset.description or asset.original_filename).lower()
        overlay = config.get('allow_overlay') and any(token in description for token in ('qr', 'logo', 'card', 'inscri'))
        return cls._decision(asset, start, max(start + 1, end), 'OVERLAY' if overlay else 'FULLSCREEN', .55, config)

    @classmethod
    def _validated_ai_decision(cls, asset, candidate, ranges, config, fallback):
        if not candidate:
            return fallback
        block_start, block_end = ranges.get(cls._asset_block_key(asset), (0, 5000))
        start = min(block_end - 1, max(block_start, int(candidate.get('start_ms') or block_start)))
        end = min(block_end, max(start + 1, int(candidate.get('end_ms') or start + 5000)))
        mode = str(candidate.get('display_mode') or fallback['display_mode']).upper()
        if mode == 'OVERLAY' and not config.get('allow_overlay'):
            mode = 'FULLSCREEN'
        result = cls._decision(asset, start, end, mode, float(candidate.get('confidence') or .75), config)
        trim_start = int(getattr(asset, 'trim_start_ms', 0) or 0)
        trim_end = int(getattr(asset, 'trim_end_ms', 0) or 0)
        result['source_in_ms'] = max(trim_start, int(candidate.get('source_in_ms') or trim_start))
        requested_out = int(candidate.get('source_out_ms') or result['source_in_ms'] + (end - start))
        result['source_out_ms'] = max(result['source_in_ms'] + 1, requested_out)
        if trim_end:
            result['source_out_ms'] = min(trim_end, result['source_out_ms'])
        return result

    @classmethod
    def _decision(cls, asset, start, end, display_mode, confidence, config):
        motion = str((asset.defaults or {}).get('motion') or config.get('image_motion') or 'AUTO').upper()
        if motion == 'AUTO':
            motion = 'ZOOM_IN' if asset.media_type == ProjectBrollAsset.MediaType.IMAGE else 'NONE'
        transition = str(config.get('default_transition') or 'FADE').upper()
        source_in = int(getattr(asset, 'trim_start_ms', 0) or 0)
        source_out = int(getattr(asset, 'trim_end_ms', 0) or 0)
        if not source_out:
            source_out = source_in + max(1, int(end) - int(start))
        return {
            'id': f'broll-{asset.public_id}', 'type': 'BROLL', 'asset_id': str(asset.public_id),
            'media_type': asset.media_type, 'block_key': cls._asset_block_key(asset),
            'start_ms': int(start), 'end_ms': int(end), 'source_in_ms': source_in,
            'source_out_ms': max(source_in + 1, source_out), 'display_mode': display_mode,
            'fit': 'COVER', 'crop_mode': 'SMART' if config.get('smart_crop') else 'CENTER',
            'transform': {'x': .5, 'y': .5, 'scale': 1.0},
            'motion': {'type': motion, 'from_scale': 1.0, 'to_scale': 1.06, 'easing': 'EASE_IN_OUT'},
            'entry': {'type': transition, 'duration_ms': 300, 'easing': 'ease-out'},
            'exit': {'type': 'FADE' if transition != 'NONE' else 'NONE', 'duration_ms': 250, 'easing': 'ease-in'},
            'source': 'AI' if config.get('auto_placement') else 'TEMPLATE',
            'confidence': max(0, min(1, confidence)), 'enabled': True,
        }

    @classmethod
    def _normalize_decision(cls, asset, decision, start, end, config):
        base = cls._decision(asset, start, end, decision.get('display_mode', 'FULLSCREEN'), decision.get('confidence', 1), config)
        for key in ('source_in_ms', 'source_out_ms', 'display_mode', 'fit', 'crop_mode', 'transform', 'motion', 'entry', 'exit', 'source', 'enabled'):
            if key in decision:
                base[key] = deepcopy(decision[key])
        base['auto'] = {key: deepcopy(value) for key, value in base.items() if key != 'auto'}
        return base

    @staticmethod
    def _asset_block_key(asset):
        return f'custom-{asset.custom_block_id}' if asset.custom_block_id else str(asset.block.key)

    @staticmethod
    def _configured_block_ranges(project):
        result = {}
        for item in (project.configuration or {}).get('block_ranges') or []:
            key = str(item.get('block_key') or '')
            if not key:
                continue
            current = result.setdefault(key, [int(item.get('start_ms') or 0), int(item.get('end_ms') or 0)])
            current[0] = min(current[0], int(item.get('start_ms') or 0))
            current[1] = max(current[1], int(item.get('end_ms') or 0))
        return {key: tuple(value) for key, value in result.items()}

    @staticmethod
    def _clip_block_ranges(clips):
        result = {}
        for clip in clips:
            block = clip.get('block') or {}
            key = str(block.get('key') or '')
            if not key:
                continue
            current = result.setdefault(key, [clip['timeline_in_ms'], clip['timeline_out_ms']])
            current[0] = min(current[0], clip['timeline_in_ms'])
            current[1] = max(current[1], clip['timeline_out_ms'])
        return {key: tuple(value) for key, value in result.items()}

    @staticmethod
    def _parse_json(raw):
        value = str(raw or '').strip()
        match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', value, flags=re.DOTALL)
        return json.loads(match.group(1) if match else value)


class BrollRenderService:
    """Executes approved timeline decisions without making editorial choices."""

    def __init__(self, runner=None, storage=None):
        self.runner = runner or FFmpegRunner()
        self.storage = storage

    def apply(self, project, video_path, output_path, decisions, width, height):
        decisions = [item for item in decisions if item.get('enabled', True) and item.get('end_ms', 0) > item.get('start_ms', 0)]
        if not decisions:
            return video_path
        asset_ids = [item.get('asset_id') for item in decisions if item.get('asset_id')]
        assets = {
            str(item.public_id): item
            for item in project.broll_assets.filter(public_id__in=asset_ids, is_enabled=True)
        }
        command = [settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path)]
        available = []
        for item in decisions:
            asset = assets.get(str(item.get('asset_id')))
            if not asset:
                logger.warning(
                    'broll_render_skipped_missing_asset project=%s asset_id=%s',
                    project.public_id, item.get('asset_id'),
                )
                continue
            source = self.storage.ffmpeg_input(asset.file) if self.storage else asset.file.path
            if asset.media_type == ProjectBrollAsset.MediaType.IMAGE:
                command.extend(['-loop', '1', '-i', FFmpegRunner.input_arg(source)])
            else:
                command.extend(['-stream_loop', '-1', '-i', FFmpegRunner.input_arg(source)])
            available.append((item, asset))
        if not available:
            return video_path
        filters, previous = [], '[0:v]'
        for index, (item, asset) in enumerate(available, start=1):
            start = float(item['start_ms']) / 1000
            end = float(item['end_ms']) / 1000
            duration = max(.001, end - start)
            mode = str(item.get('display_mode') or 'FULLSCREEN').upper()
            transform = item.get('transform') or {}
            focus_x = min(1, max(0, float(transform.get('x', .5))))
            focus_y = min(1, max(0, float(transform.get('y', .5))))
            input_label = f'[{index}:v]'
            prepared = f'[broll{index}]'
            if mode == 'FULLSCREEN':
                target_w, target_h = width, height
            else:
                target_w = max(80, round(width * min(.9, max(.08, float(transform.get('scale') or .28)))))
                target_h = -2
            chain = []
            if asset.media_type == ProjectBrollAsset.MediaType.IMAGE and mode == 'FULLSCREEN' and str((item.get('motion') or {}).get('type', 'NONE')).upper() != 'NONE':
                motion = str((item.get('motion') or {}).get('type')).upper()
                frames = max(1, round(duration * 30))
                if motion == 'ZOOM_OUT': zoom = f'1.06-0.06*on/{frames}'
                elif motion in {'PAN_LEFT', 'PAN_RIGHT'}: zoom = '1.04'
                else: zoom = f'1+0.06*on/{frames}'
                x = 'iw-iw/zoom' if motion == 'PAN_LEFT' else ('0' if motion == 'PAN_RIGHT' else 'iw/2-(iw/zoom/2)')
                chain.append(f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},zoompan=z='{zoom}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30")
            elif mode == 'FULLSCREEN':
                chain.append(
                    f'scale={width}:{height}:force_original_aspect_ratio=increase,'
                    f"crop={width}:{height}:x='(iw-ow)*{focus_x}':y='(ih-oh)*{focus_y}'"
                )
            else:
                chain.append(f'scale={target_w}:{target_h}')
            source_in = max(0, float(item.get('source_in_ms') or 0) / 1000)
            entry, exit_ = item.get('entry') or {}, item.get('exit') or {}
            entry_type = str(entry.get('type') or 'NONE').upper()
            exit_type = str(exit_.get('type') or 'NONE').upper()
            entry_duration = min(duration / 2, max(.05, float(entry.get('duration_ms') or 300) / 1000))
            exit_duration = min(duration / 2, max(.05, float(exit_.get('duration_ms') or 250) / 1000))
            chain.extend([f'trim=start={source_in}:duration={duration}', f'setpts=PTS-STARTPTS+{start}/TB'])
            scale_factor = '1'
            if entry_type == 'SCALE':
                scale_factor = f'if(lt(t,{start + entry_duration}),0.82+0.18*(t-{start})/{entry_duration},1)'
            if exit_type == 'SCALE':
                exit_start = end - exit_duration
                scale_factor = f'if(gt(t,{exit_start}),0.82+0.18*({end}-t)/{exit_duration},{scale_factor})'
            if scale_factor != '1':
                chain.append(
                    f"scale=w='trunc(iw*({scale_factor})/2)*2':"
                    f"h='trunc(ih*({scale_factor})/2)*2':eval=frame"
                )
            chain.append('format=rgba')
            if entry_type in {'FADE', 'SCALE'}:
                chain.append(f'fade=t=in:st={start}:d={entry_duration}:alpha=1')
            if exit_type in {'FADE', 'SCALE'}:
                fade_d = exit_duration
                chain.append(f'fade=t=out:st={max(start, end-fade_d)}:d={fade_d}:alpha=1')
            filters.append(f'{input_label}{",".join(chain)}{prepared}')
            if mode == 'FULLSCREEN':
                x = '(W-w)/2' if 'SCALE' in {entry_type, exit_type} else '0'
                y = '(H-h)/2' if 'SCALE' in {entry_type, exit_type} else '0'
            else:
                x = f'{float(transform.get("x", .82))}*W-w/2'
                y = f'{float(transform.get("y", .78))}*H-h/2'
            if entry_type == 'SLIDE':
                x = f'if(lt(t,{start + entry_duration}),({x})-W*(1-(t-{start})/{entry_duration}),({x}))'
            if exit_type == 'SLIDE':
                exit_start = end - exit_duration
                x = f'if(gt(t,{exit_start}),({x})+W*((t-{exit_start})/{exit_duration}),({x}))'
            output = f'[v{index}]'
            filters.append(f"{previous}{prepared}overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'{output}")
            previous = output
        command.extend(['-filter_complex', ';'.join(filters), '-map', previous, '-map', '0:a?', '-c:v', 'libx264'])
        if settings.EXTERNAL_MEDIA_RENDER_PRESET:
            command.extend(['-preset', settings.EXTERNAL_MEDIA_RENDER_PRESET])
        command.extend(['-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', str(output_path)])
        self.runner.run(command, timeout=settings.EXTERNAL_MEDIA_BROLL_RENDER_TIMEOUT)
        return output_path
