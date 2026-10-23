"""Audio alignment and revisioned camera selections; the master owns all audio.

Clock contract: secondary_ms = primary_ms * rate + offset_ms. Selections use
absolute source time; compose projects them through the existing kept clips.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
import hashlib
import json
import uuid
import wave

import numpy as np
from django.conf import settings
from django.urls import reverse

from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner


class AudioSynchronizer:
    VERSION = 1
    SAMPLE_RATE = 8000
    HOP = 80
    ANALYSIS_SECONDS = 180

    @classmethod
    def features(cls, samples):
        samples = np.asarray(samples, dtype=np.float64)
        if len(samples) < 320 or not np.isfinite(samples).all():
            return np.zeros((0, 6))
        frames = np.lib.stride_tricks.sliding_window_view(samples, 320)[::cls.HOP]
        spectrum = abs(np.fft.rfft(frames * np.hanning(320), axis=1)) ** 2
        bands = [(4, 10), (10, 20), (20, 35), (35, 55), (55, 90), (90, 150)]
        energy = np.stack([np.log1p(spectrum[:, a:b].mean(axis=1) * 100) for a, b in bands], axis=1)
        # Per-band normalization tolerates gain, polarity and microphone EQ.
        scale = energy.std(axis=0)
        usable = scale > 1e-5
        energy = (energy - energy.mean(axis=0)) / np.maximum(scale, 1e-5)
        energy[:, ~usable] = 0
        return energy

    @classmethod
    def estimate(cls, primary, secondary):
        a, b = cls.features(primary), cls.features(secondary)
        failure = {'status': 'REVIEW', 'offset_ms': 0, 'rate': 1.0, 'confidence': 0.0,
                   'reason': 'Áudio insuficiente ou sem correspondência confiável.'}
        minimum = 500  # At least five seconds of corresponding audio.
        if min(len(a), len(b)) < minimum or min(np.std(a), np.std(b)) < .1:
            return failure
        n = 1 << (len(a) + len(b) - 1).bit_length()
        cross = np.fft.irfft(np.fft.rfft(b, n, axis=0) * np.conj(np.fft.rfft(a, n, axis=0)), n, axis=0).sum(axis=1)
        lags = np.arange(-len(a) + minimum, len(b) - minimum + 1)
        left = np.maximum(0, -lags)
        right = np.minimum(len(a), len(b) - lags)
        pa = np.r_[0., np.cumsum((a * a).sum(axis=1))]
        pb = np.r_[0., np.cumsum((b * b).sum(axis=1))]
        denominator = np.sqrt((pa[right] - pa[left]) * (pb[right + lags] - pb[left + lags]))
        scores = cross[lags % n] / np.maximum(denominator, 1e-9)
        peak = int(np.argmax(scores))
        lag, score = int(lags[peak]), float(scores[peak])
        remote_peaks = scores[abs(lags - lag) > 50]
        margin = score - float(remote_peaks.max(initial=0))
        start, end = max(0, -lag), min(len(a), len(b) - lag)
        # Validate independent windows and detect inconsistent or drifting clocks.
        measurements = []
        window = min(1000, (end - start) // 3)
        for center in np.linspace(start + window // 2, end - window // 2, 3).astype(int):
            x = a[center - window // 2:center + window // 2]
            options = []
            for delta in range(-20, 21):
                pos = center - window // 2 + lag + delta
                y = b[max(0, pos):pos + len(x)]
                if pos < 0 or len(y) != len(x):
                    continue
                value = float(np.sum(x * y) / max(1e-9, np.linalg.norm(x) * np.linalg.norm(y)))
                options.append((value, delta))
            if not options:
                return failure
            value, delta = max(options)
            measurements.append((center, lag + delta, value))
        slope, intercept = np.polyfit([p[0] for p in measurements], [p[1] for p in measurements], 1)
        residual = max(abs(p[1] - (slope * p[0] + intercept)) for p in measurements)
        confidence = min(score, min(p[2] for p in measurements))
        trusted = confidence >= .55 and margin >= .08 and residual <= 2 and abs(slope) <= .001
        return {'status': 'ALIGNED' if trusted else 'REVIEW', 'offset_ms': round(intercept * 10),
                'rate': float(1 + slope), 'confidence': round(max(0., min(1., confidence)), 4),
                'peak_margin': round(margin, 4), 'drift_ppm': round(slope * 1e6, 1),
                'validated_until_primary_ms': end * 10,
                'reason': 'Correspondência acústica validada em três janelas.' if trusted else failure['reason']}

    @classmethod
    def extract(cls, source, output, runner):
        runner.run([settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(source),
                    '-t', str(cls.ANALYSIS_SECONDS), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', str(cls.SAMPLE_RATE),
                    '-af', 'asetpts=PTS-STARTPTS', '-c:a', 'pcm_s16le', str(output)])
        with wave.open(str(output), 'rb') as handle:
            return np.frombuffer(handle.readframes(cls.SAMPLE_RATE * cls.ANALYSIS_SECONDS), dtype='<i2').astype(float) / 32768


class MulticamService:
    @classmethod
    def prepare(cls, project, storage, runner, workdir):
        from .canonical import SourceManifestBuilder
        manifest = SourceManifestBuilder.build(project)
        sources = {s['id']: s for s in manifest['sources']}
        alignments = {}
        cached = (project.configuration or {}).get('multicam_alignments') or {}
        for group in manifest.get('multicam_groups', []):
            reference = sources.get(group.get('reference_source_id'))
            if not reference:
                continue
            reference_audio = None
            for source_id in group['source_ids']:
                if source_id in (group.get('reference_source_ids') or [reference['id']]):
                    continue
                source = sources[source_id]
                previous = cached.get(source_id) or {}
                if (previous.get('algorithm_version') == AudioSynchronizer.VERSION
                    and previous.get('status') == 'ALIGNED'
                    and previous.get('storage_name') == source['storage_name']
                    and previous.get('reference_storage_name') == reference['storage_name']
                    and previous.get('reference_source_id') == reference['id']):
                    alignments[source_id] = deepcopy(previous)
                    continue
                try:
                    if reference_audio is None:
                        reference_audio = AudioSynchronizer.extract(storage.ffmpeg_input(SourceManifestBuilder.resolve_field(project, reference)), Path(workdir) / 'multicam-primary.wav', runner)
                    audio = AudioSynchronizer.extract(storage.ffmpeg_input(SourceManifestBuilder.resolve_field(project, source)), Path(workdir) / 'multicam-secondary.wav', runner)
                    alignment = AudioSynchronizer.estimate(reference_audio, audio)
                except (ExternalMediaError, OSError, wave.Error, ValueError):
                    alignment = {'status': 'REVIEW', 'offset_ms': 0, 'rate': 1., 'confidence': 0., 'reason': 'Não foi possível analisar o áudio. Ajuste manualmente.'}
                alignments[source_id] = {**alignment, 'algorithm_version': AudioSynchronizer.VERSION, 'reference_source_id': reference['id'],
                                         'storage_name': source['storage_name'], 'reference_storage_name': reference['storage_name']}
        project.configuration = {**(project.configuration or {}), 'multicam_alignments': alignments}
        project.save(update_fields=['configuration', 'update_at'])
        return alignments

    @staticmethod
    def candidate_boundaries(words):
        result = []
        transitions = {'mas', 'porém', 'entretanto', 'agora', 'portanto', 'finalmente'}
        for index, (previous, word) in enumerate(zip(words, words[1:])):
            gap = int(word['start_ms']) - int(previous['end_ms'])
            text = word.get('text', '').strip().lower().strip('.,!?:;')
            # Transcript providers do not always preserve punctuation. A clear
            # silence is still an editorial boundary, while a shorter pause
            # needs an explicit discourse cue to be eligible.
            is_discourse_boundary = text in transitions or previous.get('text', '').rstrip().endswith(('.', '!', '?'))
            if gap >= 180 and (is_discourse_boundary or gap >= 500):
                point = int(previous['end_ms']) + gap // 2
                result.append({'id': f'pause-{point}', 'time_ms': point, 'emphasis': is_discourse_boundary or gap >= 500,
                               'context': ' '.join(w.get('text', '') for w in words[max(0, index - 20):index + 35])[:1200]})
        return result

    @classmethod
    def review_candidates(cls, project, ai_service=None):
        """One bounded request to the existing editorial model, outside DB locks.

        The model selects IDs of measured word pauses, never invents timestamps.
        Failure retains the conservative local proposal; an explicit empty model
        selection means keep the main camera throughout.
        """
        from website.ai import AIServiceError, get_ai_service
        configuration = project.configuration or {}
        source_info = {item['id']: item for item in (configuration.get('source_manifest') or {}).get('sources', [])}
        cameras = [{'id': key, 'reference_source_id': value.get('reference_source_id'),
                    'label': (source_info.get(key, {}).get('metadata') or {}).get('camera_label') or source_info.get(key, {}).get('filename') or key,
                    'hint': (source_info.get(key, {}).get('metadata') or {}).get('camera_hint', 'AUTO')}
                   for key, value in (configuration.get('multicam_alignments') or {}).items()
                   if value.get('status') == 'ALIGNED']
        words = configuration.get('multicam_words') or []
        candidates = cls.candidate_boundaries(words)[:120]
        if not cameras or len(candidates) < 2 or configuration.get('multicam_semantic_enabled') is False:
            return None
        payload = {'cameras': cameras, 'pauses': candidates}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = configuration.get('multicam_editorial_review') or {}
        if previous.get('fingerprint') == fingerprint and previous.get('status') == 'REVIEWED':
            return previous
        review = {'fingerprint': fingerprint, 'status': 'LOCAL_FALLBACK', 'selections': []}
        instructions = (
            'Você é editor multicâmera. A principal é a referência. Selecione apenas pausas em que '
            'uma câmera extra melhora a narrativa: mudança de pensamento, ênfase ou novo argumento. '
            'Não alterne em intervalos fixos, não preencha todas as pausas e prefira poucas mudanças. '
            'Pode não escolher nenhuma. Cada escolha inicia uma passagem pela câmera extra; '
            'o sistema retorna à principal em outra pausa segura. Os nomes das câmeras não garantem '
            'a presença de um locutor: não invente informação visual. '
            'Responda JSON: {"selections":[{"pause_id":"id fornecido","camera_id":"id fornecido",'
            '"confidence":0.0,"reason":"justificativa curta"}]}. Máximo 12 escolhas. '
            'Somente IDs fornecidos; jamais gere timestamps.'
        )
        try:
            raw = (ai_service or get_ai_service()).generate_text(
                json.dumps(payload, ensure_ascii=False), instructions=instructions,
                model=settings.EXTERNAL_MEDIA_BROLL_MODEL, max_output_tokens=1800,
            )
            data = json.loads(str(raw).strip())
            if not isinstance(data, dict) or not isinstance(data.get('selections'), list):
                raise ValueError('Invalid editorial response')
            valid_pauses, valid_cameras = {c['id'] for c in candidates}, {c['id'] for c in cameras}
            selected = []
            for item in data['selections'][:12]:
                confidence = float(item.get('confidence', 0))
                if item.get('pause_id') in valid_pauses and item.get('camera_id') in valid_cameras and math.isfinite(confidence) and .75 <= confidence <= 1:
                    selected.append({'pause_id': item['pause_id'], 'camera_id': item['camera_id'],
                                     'confidence': confidence, 'reason': str(item.get('reason') or '')[:240]})
            review.update(status='REVIEWED', selections=selected, model=settings.EXTERNAL_MEDIA_BROLL_MODEL)
        except (AIServiceError, ValueError, TypeError, KeyError, AttributeError):
            # A failed optional editorial request must not discard a project.
            pass
        project.configuration = {**configuration, 'multicam_editorial_review': review}
        project.save(update_fields=['configuration', 'update_at'])
        return review

    @staticmethod
    def compose(manifest, operations, clips, project_id):
        sources = {s['id']: s for s in manifest.get('sources', [])}
        groups = deepcopy(manifest.get('multicam_groups') or [])
        cameras = []
        for group in groups:
            for source_id in group['source_ids']:
                source = sources[source_id]
                cameras.append({'id': source_id, 'reference_source_id': group['reference_source_id'],
                                'label': ((source.get('metadata') or {}).get('camera_label') or source.get('filename') or source_id) + (f" · trecho {source['segment_index']}" if source.get('segment_index') else ''),
                                'is_primary': source_id in (group.get('reference_source_ids') or [group['reference_source_id']]),
                                'alignment': (source.get('metadata') or {}).get('multicam_alignment'),
                                'url': reverse('external_media_project_preview_source', kwargs={'public_id': project_id, 'source_id': source_id})})
        segments = []
        for operation in operations:
            if operation.get('type') != 'camera_selection' or not operation.get('enabled', True):
                continue
            camera = sources.get(operation.get('camera_source_id'))
            if not camera:
                raise ValueError('Câmera selecionada não está disponível.')
            alignment = operation['alignment']
            for clip in clips:
                if clip['asset_id'] != operation['source_id']:
                    continue
                left = max(clip['source_in_ms'], operation['source_in_ms'])
                right = min(clip['source_out_ms'], operation['source_out_ms'])
                if right <= left:
                    continue
                source_in = left * alignment['rate'] + alignment['offset_ms']
                source_out = right * alignment['rate'] + alignment['offset_ms']
                trim = camera.get('trim') or {}
                bounds = [int(value) for value in (trim.get('end_ms'), camera.get('duration_ms')) if value]
                upper = min(bounds) if bounds else None
                if source_in < int(trim.get('start_ms') or 0) or not upper or source_out > upper:
                    raise ValueError('A câmera não cobre todo o intervalo selecionado.')
                start = clip['timeline_in_ms'] + left - clip['source_in_ms']
                segments.append({'id': operation['id'], 'camera_source_id': camera['id'], 'reference_source_id': operation['source_id'],
                                 'start_ms': start, 'end_ms': start + right - left,
                                 'source_in_ms': round(source_in), 'source_out_ms': round(source_out),
                                 'rate': alignment['rate'], 'origin': operation.get('origin', 'USER')})
        segments.sort(key=lambda s: s['start_ms'])
        if any(a['end_ms'] > b['start_ms'] for a, b in zip(segments, segments[1:])):
            raise ValueError('As seleções de câmera não podem se sobrepor.')
        return cameras, segments

    @staticmethod
    def selection(manifest, *, reference_id, camera_id, start, end, offset=None, rate=None, selection_id=None, origin='USER'):
        sources = {s['id']: s for s in manifest['sources']}
        if not any(reference_id in (g.get('reference_source_ids') or [g.get('reference_source_id')]) and camera_id in g['source_ids'] for g in manifest.get('multicam_groups', [])):
            raise ValueError('A câmera não pertence à mesma gravação principal.')
        if camera_id == reference_id or (sources[camera_id].get('metadata') or {}).get('camera_role') == 'PRIMARY':
            raise ValueError('Remova a alternância para usar a câmera principal.')
        start, end = int(start), int(end)
        if start < 0 or end - start < 100:
            raise ValueError('Selecione um intervalo de pelo menos 0,1 segundo.')
        alignment = deepcopy((sources[camera_id].get('metadata') or {}).get('multicam_alignment') or {})
        if offset is not None:
            alignment.update(status='MANUAL', offset_ms=float(offset), rate=float(rate if rate is not None else alignment.get('rate', 1)))
        if alignment.get('status') not in {'ALIGNED', 'MANUAL'}:
            raise ValueError('Sincronização requer revisão. Informe o deslocamento manual.')
        if not math.isfinite(float(alignment.get('offset_ms', 0))) or not math.isfinite(float(alignment.get('rate', 1))) or not .999 <= float(alignment.get('rate', 1)) <= 1.001:
            raise ValueError('Deslocamento ou drift inválido.')
        alignment['offset_ms'], alignment['rate'] = float(alignment.get('offset_ms', 0)), float(alignment.get('rate', 1))
        if alignment.get('status') == 'ALIGNED' and end > alignment.get('validated_until_primary_ms', 0):
            raise ValueError('Intervalo fora da região acústica validada. Revise e confirme o deslocamento manual.')
        return {'id': selection_id or f'camera-{uuid.uuid4().hex}', 'type': 'camera_selection',
                'source_id': reference_id, 'camera_source_id': camera_id, 'source_in_ms': start, 'source_out_ms': end,
                'alignment': alignment, 'enabled': True, 'origin': origin, 'reason': 'Alternância de câmera'}

    @classmethod
    def propose(cls, project, manifest, timeline):
        """Conservative local editorial proposal from word pauses and thought transitions."""
        words = (project.configuration or {}).get('multicam_words') or []
        minimum = max(2000, int((project.configuration or {}).get('multicam_min_shot_ms') or 6000))
        operations, last_end = [], -minimum
        maximum = cls.automatic_selection_limit(
            timeline, (project.configuration or {}).get('multicam_max_auto_selections'),
        )
        candidates = cls.candidate_boundaries(words)
        review = (project.configuration or {}).get('multicam_editorial_review') or {}
        semantic = review.get('status') == 'REVIEWED' and (project.configuration or {}).get('multicam_semantic_enabled') is not False
        selections = {item['pause_id']: item for item in review.get('selections', [])}
        for index, candidate in enumerate(candidates):
            start, selected = candidate['time_ms'], selections.get(candidate['id'])
            if (semantic and not selected) or (not semantic and not candidate['emphasis']) or start - last_end < minimum:
                continue
            end = next((item['time_ms'] for item in candidates[index + 1:] if item['time_ms'] - start >= minimum), None)
            if end is None:
                continue
            clip = next((c for c in timeline['clips'] if c['timeline_in_ms'] <= start and end <= c['timeline_out_ms']), None)
            if not clip:
                continue
            secondary = next((s for s in manifest['sources'] if (not selected or s['id'] == selected['camera_id']) and ((s.get('metadata') or {}).get('multicam_alignment') or {}).get('status') == 'ALIGNED' and any(clip['asset_id'] in (g.get('reference_source_ids') or [g.get('reference_source_id')]) and s['id'] in g['source_ids'] for g in manifest.get('multicam_groups', []))), None)
            if not secondary:
                continue
            alignment = secondary['metadata']['multicam_alignment']
            local_start = start - clip['timeline_in_ms'] + clip['source_in_ms']
            local_end = end - clip['timeline_in_ms'] + clip['source_in_ms']
            # Never extrapolate a short acoustic validation across a long recording.
            if local_end > alignment.get('validated_until_primary_ms', 0):
                continue
            operation = cls.selection(manifest, reference_id=clip['asset_id'], camera_id=secondary['id'], start=local_start, end=local_end, origin='AUTO')
            try:
                cls.compose(manifest, [operation], timeline['clips'], project.public_id)
            except ValueError:
                continue
            if selected:
                operation.update(origin='AI', confidence=selected['confidence'], reason=selected['reason'] or 'Transição narrativa')
            operations.append(operation)
            last_end = end
            if len(operations) >= maximum:
                break
        return operations

    @staticmethod
    def automatic_selection_limit(timeline, configured_maximum=None):
        if configured_maximum is not None:
            return max(1, min(18, int(configured_maximum)))
        duration_ms = int((timeline.get('sequence') or {}).get('duration_ms') or max(
            (clip.get('timeline_out_ms') or 0) for clip in timeline.get('clips') or [{}]
        ))
        # A sparse rhythm keeps the principal camera as the default: about one
        # candidate every two minutes, from two short-video changes up to
        # eighteen in a long conversation.
        return max(2, min(18, round(duration_ms / 120000)))


class MulticamRenderService:
    """Shared preview/delivery compositor; never selects or decodes camera audio."""
    def __init__(self, runner, storage):
        self.runner, self.storage = runner, storage

    def apply(self, project, revision, video, output, width, height):
        from .canonical import SourceManifestBuilder
        segments = (revision.timeline or {}).get('camera_segments') or []
        if not segments:
            return video
        sources = {s['id']: s for s in revision.source_manifest['sources']}
        operations = (getattr(revision, 'edit_decision_set', None) or {}).get('operations') or []

        def camera_visual_filters(segment):
            """Return the manual framing and grade for this exact camera take."""
            source_id = str(segment['camera_source_id'])
            reframes = [item for item in operations if (
                item.get('type') == 'reframe' and item.get('enabled', True)
                and str(item.get('source_id')) == source_id
            )]
            scoped = [item for item in reframes if (
                int((item.get('metadata') or {}).get('segment_start_ms', -1))
                <= int(segment['source_in_ms'])
                < int((item.get('metadata') or {}).get('segment_end_ms', -1))
            )]
            selected = next((item for item in reversed(scoped or reframes)
                             if (item.get('metadata') or {}).get('manual_transform') is not None), None)
            transform = (selected or {}).get('metadata', {}).get('manual_transform') or {}
            scale = min(2.0, max(1.0, float(transform.get('scale') or 1)))
            x = min(1.0, max(-1.0, float(transform.get('x') or 0)))
            y = min(1.0, max(-1.0, float(transform.get('y') or 0)))
            crop = '' if scale == 1 else (
                f',crop=trunc(iw/{scale:.5f}/2)*2:trunc(ih/{scale:.5f}/2)*2:'
                f'(iw-ow)/2-({x:.5f})*(iw-ow)/2:(ih-oh)/2-({y:.5f})*(ih-oh)/2'
            )
            grade = next(((item.get('metadata') or {}).get('adjustment') for item in reversed(operations) if (
                item.get('type') == 'color_adjustment' and item.get('enabled', True)
                and str(item.get('source_id')) == source_id
            )), None) or {}
            if not grade:
                return crop
            brightness = min(100, max(-100, float(grade.get('brightness') or 0))) / 200
            contrast = min(200, max(0, float(grade.get('contrast') or 100))) / 100
            saturation = min(200, max(0, float(grade.get('saturation') or 100))) / 100
            temperature = min(100, max(-100, float(grade.get('temperature') or 0))) / 100
            red = float(grade.get('red') or 0) + float(grade.get('yellow') or 0) + float(grade.get('magenta') or 0)
            green = float(grade.get('green') or 0) + float(grade.get('yellow') or 0) + float(grade.get('cyan') or 0)
            blue = float(grade.get('blue') or 0) + float(grade.get('cyan') or 0) + float(grade.get('magenta') or 0)
            red = max(0.0, min(2.0, 1 + ((red / 300) + temperature) * .28))
            green = max(0.0, min(2.0, 1 + ((green / 300)) * .28))
            blue = max(0.0, min(2.0, 1 + ((blue / 300) - temperature) * .28))
            return crop + (
                f',eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}'
                f',colorchannelmixer=rr={red:.4f}:gg={green:.4f}:bb={blue:.4f}'
            )
        # Bound simultaneously opened decoders even for long edits. Each pass
        # paints up to eight selections and stream-copies the master audio.
        current = video
        for batch_start in range(0, len(segments), 8):
            batch = segments[batch_start:batch_start + 8]
            target = Path(output) if batch_start + 8 >= len(segments) else Path(output).with_name(f'multicam-pass-{batch_start}.mp4')
            command = [settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(current)]
            duration_ms = ((revision.timeline or {}).get('sequence') or {}).get('duration_ms')
            duration_filter = f',trim=duration={int(duration_ms) / 1000:.6f}' if duration_ms else ''
            filters, label = [f'[0:v]fps=30{duration_filter},setpts=PTS-STARTPTS[base]'], 'base'
            for index, item in enumerate(batch, 1):
                source = sources[item['camera_source_id']]
                field = SourceManifestBuilder.resolve_field(project, source)
                if source.get('storage_name') and getattr(field, 'name', None) != source['storage_name']:
                    raise ExternalMediaError('O arquivo da câmera mudou após a revisão. Reprocesse o projeto.')
                command += ['-ss', f"{item['source_in_ms'] / 1000:.6f}", '-t', f"{(item['source_out_ms'] - item['source_in_ms']) / 1000:.6f}", '-i', FFmpegRunner.input_arg(self.storage.ffmpeg_input(field))]
                start, end = item['start_ms'] / 1000, item['end_ms'] / 1000
                visual = camera_visual_filters(item)
                filters.append(f'[{index}:v]setpts=(PTS-STARTPTS)/{item["rate"]:.9f}+{start:.6f}/TB{visual},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30[cam{index}]')
                # Hold the last 24/25 fps frame through its display interval;
                # the half-open enable expression still ends on the exact cut.
                filters.append(f"[{label}][cam{index}]overlay=eof_action=repeat:repeatlast=1:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[out{index}]")
                label = f'out{index}'
            command += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters), '-map', f'[{label}]', '-map', '0:a?', '-c:a', 'copy', '-c:v', 'libx264', '-preset', settings.EXTERNAL_MEDIA_RENDER_PRESET or 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-r', '30', '-movflags', '+faststart', str(target)]
            self.runner.run(command)
            if current != video and Path(current).name.startswith('multicam-pass-'):
                Path(current).unlink(missing_ok=True)
            current = target
        return current
