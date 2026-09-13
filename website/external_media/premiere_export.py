from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from .exceptions import ExternalMediaError


def json_compatible(value):
    """Converts model numeric values into portable JSON primitives for exports."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    return value


@dataclass(frozen=True)
class PremiereTransformPoint:
    time_ms: float
    center_x: float
    center_y: float
    scale: float


class PremiereTransformAdapter:
    """Converts renderer-neutral framing into FCP 7 XML Motion values."""

    @classmethod
    def adapt(cls, effect, asset, sequence, clip_start_ms=0):
        if effect.get('coordinate_space') != 'normalized_source':
            return cls._legacy_pixels(effect, sequence, clip_start_ms)

        source_width = max(1.0, float(effect.get('source_width') or asset.get('width') or 1))
        source_height = max(1.0, float(effect.get('source_height') or asset.get('height') or 1))
        pixel_aspect_ratio = max(0.01, float(effect.get('pixel_aspect_ratio') or 1))
        sequence_width = max(1.0, float(sequence['width']))
        sequence_height = max(1.0, float(sequence['height']))
        base_scale = max(
            sequence_width / (source_width * pixel_aspect_ratio),
            sequence_height / source_height,
        )
        result = []
        for point in effect.get('keyframes') or []:
            zoom = max(0.01, float(point.get('zoom') or 1))
            scale_factor = base_scale * zoom
            position_x = sequence_width / 2 - (
                float(point.get('center_x', 0.5)) - 0.5
            ) * source_width * pixel_aspect_ratio * scale_factor
            position_y = sequence_height / 2 - (
                float(point.get('center_y', 0.5)) - 0.5
            ) * source_height * scale_factor
            result.append(PremiereTransformPoint(
                time_ms=max(0.0, float(point.get('time_ms') or 0) - clip_start_ms),
                center_x=cls._xmeml_center(position_x, sequence_width),
                center_y=cls._xmeml_center(position_y, sequence_height),
                scale=round(scale_factor * 100, 6),
            ))
        return result

    @classmethod
    def _legacy_pixels(cls, effect, sequence, clip_start_ms):
        return [
            PremiereTransformPoint(
                time_ms=max(0.0, float(point.get('time_ms') or 0) - clip_start_ms),
                center_x=cls._xmeml_center(float(point.get('x') or 0), float(sequence['width'])),
                center_y=cls._xmeml_center(float(point.get('y') or 0), float(sequence['height'])),
                scale=float(point.get('scale') or 100),
            )
            for point in effect.get('keyframes') or []
        ]

    @staticmethod
    def _xmeml_center(position, dimension):
        # XMEML Basic Motion uses -100..100, where zero is the sequence center.
        normalized = (position - dimension / 2) / (dimension / 2) * 100
        return round(max(-100.0, min(100.0, normalized)), 6)


class PremiereExporter:
    """Isola o formato de interchange escolhido (FCP 7 XML importável no Premiere)."""

    format_name = 'Final Cut Pro 7 XML'

    def export(self, timeline: dict, output_path: Path):
        self._declared_files = set()
        root = ET.Element('xmeml', version='5')
        project_node = ET.SubElement(root, 'project')
        self._text(project_node, 'name', timeline['project']['name'])
        children = ET.SubElement(project_node, 'children')
        sequence = ET.SubElement(children, 'sequence', {
            'id': 'sequence-1', 'TL.SQAVDividerPosition': '0.5',
        })
        self._text(sequence, 'name', timeline['sequence']['name'])
        rate = self._rate(sequence, timeline['sequence'])
        duration_frames = self._frames(timeline['sequence']['duration_ms'], timeline['sequence']['fps'])
        self._text(sequence, 'duration', duration_frames)
        self._text(sequence, 'in', '-1')
        self._text(sequence, 'out', '-1')

        media = ET.SubElement(sequence, 'media')
        video = ET.SubElement(media, 'video')
        self._video_format(video, timeline['sequence'])
        files = {asset['id']: asset for asset in timeline['assets']}
        for video_track_index, track_data in enumerate(timeline['video_tracks'], start=1):
            track = ET.SubElement(video, 'track')
            self._text(track, 'name', track_data.get('name') or f'Video {video_track_index}')
            self._text(track, 'enabled', 'TRUE')
            self._text(track, 'locked', 'TRUE' if track_data.get('locked') else 'FALSE')
            for clip in track_data['clips']:
                self._clipitem(
                    track, clip, files[clip['asset_id']], timeline['sequence'], media_type='video',
                    track_index=video_track_index,
                    linked_audio_track_index=1 if clip.get('audio_enabled', True) else None,
                )

        # FCP 7 XML has no portable native-caption representation that Premiere
        # imports consistently. Generate editable legacy text/title clips in addition
        # to the SRT files, preserving every cue and its timing in the sequence.
        self._caption_tracks(video, timeline.get('captions') or [], timeline['sequence'])

        audio = ET.SubElement(media, 'audio')
        audio_format = ET.SubElement(audio, 'format')
        sample = ET.SubElement(audio_format, 'samplecharacteristics')
        self._text(sample, 'depth', 16)
        self._text(sample, 'samplerate', timeline['sequence']['audio_sample_rate'])
        for audio_track_index, track_data in enumerate(timeline['audio_tracks'], start=1):
            track = ET.SubElement(audio, 'track')
            self._text(track, 'name', track_data.get('name') or f'Audio {audio_track_index}')
            self._text(track, 'enabled', 'TRUE')
            self._text(track, 'locked', 'FALSE')
            for clip in track_data['clips']:
                self._clipitem(
                    track, clip, files[clip['asset_id']], timeline['sequence'], media_type='audio',
                    automation=track_data.get('volume_automation') or [], track_index=audio_track_index,
                    linked_video_track_index=1 if track_data.get('role') == 'dialogue' else None,
                )

        for marker in timeline.get('markers', []):
            marker_node = ET.SubElement(sequence, 'marker')
            self._text(marker_node, 'name', marker.get('name') or 'Marker')
            frame = self._frames(marker.get('time_ms', 0), timeline['sequence']['fps'])
            self._text(marker_node, 'in', frame)
            self._text(marker_node, 'out', frame)

        tree = ET.ElementTree(root)
        ET.indent(tree, space='  ')
        tree.write(output_path, encoding='utf-8', xml_declaration=True)
        return output_path

    def _clipitem(
        self, parent, clip, asset, sequence, media_type, automation=None, track_index=1,
        linked_audio_track_index=None, linked_video_track_index=None,
    ):
        clip_id = f"{clip['id']}-{media_type}"
        node = ET.SubElement(parent, 'clipitem', id=clip_id)
        self._text(node, 'name', clip.get('name') or asset['name'])
        self._text(node, 'enabled', 'TRUE')
        self._text(node, 'alphatype', asset.get('alpha_mode') or 'none')
        source_fps = float(asset.get('fps') or sequence['fps'])
        self._rate(node, {
            'fps': source_fps,
            'timebase': max(1, round(source_fps)),
            'ntsc': abs(source_fps - round(source_fps)) > 0.01,
        })
        self._text(node, 'start', self._frames(clip['timeline_in_ms'], sequence['fps']))
        self._text(node, 'end', self._frames(clip['timeline_out_ms'], sequence['fps']))
        self._text(node, 'in', self._frames(clip['source_in_ms'], source_fps))
        self._text(node, 'out', self._frames(clip['source_out_ms'], source_fps))
        self._file(node, asset, sequence, media_type)
        self._source_track(node, media_type)
        self._links(
            node, clip['id'], media_type, track_index,
            linked_audio_track_index=linked_audio_track_index,
            linked_video_track_index=linked_video_track_index,
        )
        if media_type == 'video':
            for effect in clip.get('effects', []):
                if effect.get('type') == 'transform':
                    self._motion_filter(node, effect, asset, sequence, clip)
        elif automation:
            self._volume_filter(node, automation, clip, sequence['fps'])
        return node

    def _source_track(self, clip_node, media_type):
        """Disambiguates video/audio streams in MOV files for Premiere's importer."""
        source_track = ET.SubElement(clip_node, 'sourcetrack')
        self._text(source_track, 'mediatype', media_type)
        self._text(source_track, 'trackindex', 1)

    def _links(
        self, clip_node, clip_id, media_type, track_index, linked_audio_track_index=None,
        linked_video_track_index=None,
    ):
        links = [(f'{clip_id}-{media_type}', media_type, track_index)]
        if linked_audio_track_index:
            links.append((f'{clip_id}-audio', 'audio', linked_audio_track_index))
        if linked_video_track_index:
            links.append((f'{clip_id}-video', 'video', linked_video_track_index))
        for link_id, link_type, linked_track in links:
            link = ET.SubElement(clip_node, 'link')
            self._text(link, 'linkclipref', link_id)
            self._text(link, 'mediatype', link_type)
            self._text(link, 'trackindex', linked_track)
            self._text(link, 'clipindex', 1)

    def _caption_tracks(self, video, captions, sequence):
        for caption_track_index, caption in enumerate(captions, start=1):
            track = ET.SubElement(video, 'track')
            language = (caption.get('language') or 'caption').upper()
            self._text(track, 'name', f'Captions {language}')
            self._text(track, 'enabled', 'TRUE')
            self._text(track, 'locked', 'FALSE')
            for cue_index, cue in enumerate(caption.get('cues') or [], start=1):
                self._caption_generator(
                    track, cue, sequence,
                    item_id=f'caption-{language.lower()}-{cue_index}',
                    name=f'Caption {language} {cue_index}',
                    style=caption.get('style') or {},
                )

    def _caption_generator(self, parent, cue, sequence, item_id, name, style):
        node = ET.SubElement(parent, 'generatoritem', id=item_id)
        self._text(node, 'name', name)
        duration = self._frames(sequence['duration_ms'], sequence['fps'])
        self._text(node, 'duration', duration)
        self._rate(node, sequence)
        self._text(node, 'start', self._frames(cue['start_ms'], sequence['fps']))
        self._text(node, 'end', self._frames(cue['end_ms'], sequence['fps']))
        self._text(node, 'in', 0)
        self._text(node, 'out', self._frames(cue['end_ms'] - cue['start_ms'], sequence['fps']))
        effect = ET.SubElement(node, 'effect')
        self._text(effect, 'name', 'Text')
        self._text(effect, 'effectid', 'Text')
        self._text(effect, 'effectcategory', 'Text')
        self._text(effect, 'effecttype', 'generator')
        self._text(effect, 'mediatype', 'video')
        self._generator_parameter(effect, 'str', 'Text', cue.get('text') or '')
        # These are advisory values for compatible importers. The complete style
        # remains in timeline.json because legacy XML cannot faithfully carry all
        # current platform caption styling.
        self._generator_parameter(effect, 'font', 'Font', style.get('font') or 'Arial')
        self._generator_parameter(effect, 'fontsize', 'Font Size', style.get('font_size') or 48)

    def _generator_parameter(self, effect, parameter_id, name, value):
        parameter = ET.SubElement(effect, 'parameter')
        self._text(parameter, 'parameterid', parameter_id)
        self._text(parameter, 'name', name)
        self._text(parameter, 'value', value)

    def _file(self, clip_node, asset, sequence, media_type):
        file_id = f"file-{asset['id']}"
        file_node = ET.SubElement(clip_node, 'file', id=file_id)
        if file_id in self._declared_files:
            return
        self._declared_files.add(file_id)
        self._text(file_node, 'name', asset['name'])
        # timeline.xml lives in Project/, so the portable media reference starts one
        # directory above it. No server path is ever exposed.
        relative_path = str(asset['path']).removeprefix('./')
        self._text(file_node, 'pathurl', f'../{relative_path}')
        asset_fps = float(asset.get('fps') or sequence['fps'])
        self._rate(file_node, {
            'fps': asset_fps,
            'timebase': max(1, round(asset_fps)),
            'ntsc': abs(asset_fps - round(asset_fps)) > 0.01,
        })
        self._text(file_node, 'duration', self._frames(asset.get('duration_ms', 1), asset_fps))
        media = ET.SubElement(file_node, 'media')
        if asset.get('type') == 'video':
            video = ET.SubElement(media, 'video')
            if asset.get('alpha_mode'):
                self._text(video, 'alphatype', asset['alpha_mode'])
            sample = ET.SubElement(video, 'samplecharacteristics')
            self._text(sample, 'width', asset.get('width') or sequence['width'])
            self._text(sample, 'height', asset.get('height') or sequence['height'])
        if asset.get('has_audio', True):
            audio = ET.SubElement(media, 'audio')
            sample = ET.SubElement(audio, 'samplecharacteristics')
            self._text(sample, 'depth', 16)
            self._text(sample, 'samplerate', sequence['audio_sample_rate'])
            self._text(audio, 'channelcount', sequence['audio_channels'])

    def _motion_filter(self, clip_node, effect, asset, sequence, clip):
        points = PremiereTransformAdapter.adapt(
            effect, asset, sequence, clip_start_ms=clip.get('timeline_in_ms', 0),
        )
        if not points:
            return
        filter_node = ET.SubElement(clip_node, 'filter')
        effect_node = ET.SubElement(filter_node, 'effect')
        self._text(effect_node, 'name', 'Basic Motion')
        self._text(effect_node, 'effectid', 'basic')
        self._text(effect_node, 'effectcategory', 'motion')
        self._text(effect_node, 'effecttype', 'motion')
        for parameter_id, label, key in (
            ('center', 'Center', None), ('scale', 'Scale', 'scale'),
        ):
            parameter = ET.SubElement(effect_node, 'parameter')
            self._text(parameter, 'parameterid', parameter_id)
            self._text(parameter, 'name', label)
            for point in points:
                keyframe = ET.SubElement(parameter, 'keyframe')
                self._text(keyframe, 'when', self._frames(point.time_ms, sequence['fps']))
                if key is None:
                    value = ET.SubElement(keyframe, 'value')
                    self._text(value, 'horiz', point.center_x)
                    self._text(value, 'vert', point.center_y)
                else:
                    self._text(keyframe, 'value', point.scale)
                interpolation = ET.SubElement(keyframe, 'interpolation')
                self._text(interpolation, 'name', 'FCPCurve')

    def _volume_filter(self, clip_node, automation, clip, fps):
        relevant = [
            point for point in automation
            if clip['timeline_in_ms'] <= point['time_ms'] <= clip['timeline_out_ms']
        ]
        if not relevant:
            return
        filter_node = ET.SubElement(clip_node, 'filter')
        effect_node = ET.SubElement(filter_node, 'effect')
        self._text(effect_node, 'name', 'Audio Levels')
        self._text(effect_node, 'effectid', 'audiolevels')
        self._text(effect_node, 'effecttype', 'audiolevels')
        parameter = ET.SubElement(effect_node, 'parameter')
        self._text(parameter, 'parameterid', 'level')
        self._text(parameter, 'name', 'Level')
        for point in relevant:
            keyframe = ET.SubElement(parameter, 'keyframe')
            self._text(keyframe, 'when', self._frames(point['time_ms'], fps))
            self._text(keyframe, 'value', round(10 ** (point['gain_db'] / 20), 6))
            self._text(keyframe, 'interp', 'smooth')

    def _video_format(self, video, sequence):
        fmt = ET.SubElement(video, 'format')
        sample = ET.SubElement(fmt, 'samplecharacteristics')
        self._rate(sample, sequence)
        self._text(sample, 'width', sequence['width'])
        self._text(sample, 'height', sequence['height'])
        self._text(sample, 'anamorphic', 'FALSE')
        self._text(sample, 'pixelaspectratio', 'square')
        self._text(sample, 'fielddominance', 'none')

    def _rate(self, parent, sequence):
        rate = ET.SubElement(parent, 'rate')
        self._text(rate, 'timebase', sequence.get('timebase') or round(sequence['fps']))
        self._text(rate, 'ntsc', 'TRUE' if sequence.get('ntsc') else 'FALSE')
        return rate

    @staticmethod
    def _frames(milliseconds, fps):
        return max(0, round(float(milliseconds) / 1000 * float(fps)))

    @staticmethod
    def _text(parent, tag, value):
        node = ET.SubElement(parent, tag)
        node.text = str(value)
        return node


class ExportValidationService:
    def validate(self, package_root: Path, timeline: dict, xml_path: Path, *, virtual_assets=()):
        virtual_assets = {str(path).removeprefix('./') for path in virtual_assets}

        def exists_in_package(path):
            try:
                relative = path.resolve().relative_to(package_root.resolve()).as_posix()
            except ValueError:
                return False
            return path.exists() or relative in virtual_assets

        errors, warnings = [], list(timeline.get('compatibility', {}).get('warnings') or [])
        xml_root = None
        try:
            xml_root = ET.parse(xml_path).getroot()
        except (ET.ParseError, OSError) as exc:
            errors.append(f'XML inválido: {exc}')
        for asset in timeline.get('assets', []):
            relative = str(asset.get('path') or '').removeprefix('./')
            if not relative or relative.startswith('/') or '..' in Path(relative).parts:
                errors.append(f'Path de asset inválido: {asset.get("path")}')
                continue
            if not exists_in_package(package_root / relative):
                errors.append(f'Asset ausente: {relative}')
        if xml_root is not None:
            for node in xml_root.findall('.//pathurl'):
                value = (node.text or '').strip()
                if not value or value.startswith('/') or value.startswith('file:'):
                    errors.append(f'Referência não portátil no XML: {value or "vazia"}')
                    continue
                resolved = (xml_path.parent / value).resolve()
                try:
                    resolved.relative_to(package_root.resolve())
                except ValueError:
                    errors.append(f'Referência fora do pacote: {value}')
                    continue
                if not exists_in_package(resolved):
                    errors.append(f'Mídia offline no XML: {value}')
        if not timeline.get('clips'):
            errors.append('A timeline não possui clips.')
        if timeline.get('sequence', {}).get('duration_ms', 0) <= 0:
            errors.append('A timeline possui duração inválida.')
        if not timeline.get('video_tracks'):
            errors.append('Nenhuma trilha de vídeo foi criada.')
        offline = [
            asset['path'] for asset in timeline.get('assets', [])
            if not exists_in_package(package_root / asset['path'].removeprefix('./'))
        ]
        report = {
            'valid': not errors,
            'errors': errors,
            'warnings': warnings,
            'asset_count': len(timeline.get('assets', [])),
            'clip_count': len(timeline.get('clips', [])),
            'video_track_count': len(timeline.get('video_tracks', [])),
            'audio_track_count': len(timeline.get('audio_tracks', [])),
            'caption_track_count': len(timeline.get('captions', [])),
            'offline_media': offline,
        }
        if errors:
            raise ExternalMediaError('Falha ao validar exportação: ' + ' '.join(errors))
        return report


class PremierePackageService:
    def build(self, project, package_root: Path, output_zip: Path, timeline_builder, progress=None):
        progress = progress or (lambda *args: None)
        timeline = json_compatible(timeline_builder.build(project, package_root))
        source_files = dict(getattr(timeline_builder, 'package_source_files', {}))
        progress('CONVERTING', 48, 'Convertendo a timeline para Adobe Premiere')
        timeline_path = package_root / 'Metadata' / 'timeline.json'
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf-8')
        xml_path = package_root / 'Project' / 'timeline.xml'
        PremiereExporter().export(timeline, xml_path)
        progress('PACKAGING_ASSETS', 66, 'Organizando originais, áudio, legendas e LUTs')
        progress('VALIDATING', 78, 'Validando XML, referências e mídia offline')
        report = json_compatible(ExportValidationService().validate(
            package_root, timeline, xml_path, virtual_assets=source_files,
        ))
        (package_root / 'Metadata' / 'validation.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        caption_overlay_available = any(
            asset.get('role') == 'caption_overlay' for asset in timeline.get('assets', [])
        )
        caption_instructions = (
            'A trilha “Legendas estilizadas (visual final)” é um ProRes 4444 com transparência '
            'e reproduz o visual final das legendas. Ela vem bloqueada: mantenha-a visível para '
            'fidelidade visual ou oculte-a para editar os títulos/SRT nativos.\n'
            'Os SRTs ficam em Captions/. As trilhas de títulos PT/EN permanecem editáveis, mas o ProRes '
            'é a referência fiel para fundo, opacidade, sombra, contorno e posicionamento.\n'
            if caption_overlay_available else
            'A camada ProRes de referência visual das legendas não pôde ser gerada neste worker. '
            'Os SRTs em Captions/ e os títulos PT/EN no XML continuam disponíveis para edição.\n'
        )
        (package_root / 'README.txt').write_text(
            'Abra Project/timeline.xml no Adobe Premiere Pro.\n'
            'Media contém os vídeos originais. Audio contém os WAVs de diálogo e a música de fundo.\n'
            'A1 é o diálogo separado; A2 é a música, com keyframes de volume equivalentes ao ducking do render.\n'
            + caption_instructions +
            'LUT, tratamento de diálogo e masterização constam nos metadados; reaplique-os no Premiere '\
            'quando quiser uma edição não destrutiva.\n'
            'Consulte Metadata/timeline.json e Metadata/validation.json para detalhes.\n'
            'A masterização final deve ser feita após a edição, usando “Masterizar Vídeo”.\n',
            encoding='utf-8',
        )
        progress('COMPRESSING', 88, 'Compactando o pacote completo')
        with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_STORED) as archive:
            # S3 source files are copied in chunks directly into the ZIP. They
            # are deliberately absent from package_root, avoiding the former
            # package/Media + output ZIP peak that exhausted Render's 2 GB disk.
            for relative, field_file in sorted(source_files.items()):
                with field_file.open('rb') as source, archive.open(relative, 'w', force_zip64=True) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            for path in sorted(package_root.rglob('*')):
                if path.is_file():
                    relative = path.relative_to(package_root)
                    compression = (
                        zipfile.ZIP_STORED if relative.parts[0] in {'Media', 'Audio', 'Graphics'}
                        else zipfile.ZIP_DEFLATED
                    )
                    archive.write(path, relative, compress_type=compression)
        return timeline, report, timeline_path, output_zip
