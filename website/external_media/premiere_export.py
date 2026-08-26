from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .exceptions import ExternalMediaError


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
                    self._motion_filter(node, effect, sequence['fps'])
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
            sample = ET.SubElement(video, 'samplecharacteristics')
            self._text(sample, 'width', asset.get('width') or sequence['width'])
            self._text(sample, 'height', asset.get('height') or sequence['height'])
        if asset.get('has_audio', True):
            audio = ET.SubElement(media, 'audio')
            sample = ET.SubElement(audio, 'samplecharacteristics')
            self._text(sample, 'depth', 16)
            self._text(sample, 'samplerate', sequence['audio_sample_rate'])
            self._text(audio, 'channelcount', sequence['audio_channels'])

    def _motion_filter(self, clip_node, effect, fps):
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
            for point in effect.get('keyframes', []):
                keyframe = ET.SubElement(parameter, 'keyframe')
                self._text(keyframe, 'when', self._frames(point['time_ms'], fps))
                if key is None:
                    value = ET.SubElement(keyframe, 'value')
                    self._text(value, 'horiz', point.get('x', 0))
                    self._text(value, 'vert', point.get('y', 0))
                else:
                    self._text(keyframe, 'value', point.get(key, 100))
                self._text(keyframe, 'interp', 'smooth')

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
    def validate(self, package_root: Path, timeline: dict, xml_path: Path):
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
            if not (package_root / relative).exists():
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
                if not resolved.exists():
                    errors.append(f'Mídia offline no XML: {value}')
        if not timeline.get('clips'):
            errors.append('A timeline não possui clips.')
        if timeline.get('sequence', {}).get('duration_ms', 0) <= 0:
            errors.append('A timeline possui duração inválida.')
        if not timeline.get('video_tracks'):
            errors.append('Nenhuma trilha de vídeo foi criada.')
        offline = [asset['path'] for asset in timeline.get('assets', []) if not (package_root / asset['path'].removeprefix('./')).exists()]
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
        timeline = timeline_builder.build(project, package_root)
        progress('CONVERTING', 48, 'Convertendo a timeline para Adobe Premiere')
        timeline_path = package_root / 'Metadata' / 'timeline.json'
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf-8')
        xml_path = package_root / 'Project' / 'timeline.xml'
        PremiereExporter().export(timeline, xml_path)
        progress('PACKAGING_ASSETS', 66, 'Organizando originais, áudio, legendas e LUTs')
        progress('VALIDATING', 78, 'Validando XML, referências e mídia offline')
        report = ExportValidationService().validate(package_root, timeline, xml_path)
        (package_root / 'Metadata' / 'validation.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        (package_root / 'README.txt').write_text(
            'Abra Project/timeline.xml no Adobe Premiere Pro.\n'
            'Media contém os vídeos originais. Audio contém os WAVs de diálogo e a música de fundo.\n'
            'A1 é o diálogo separado; A2 é a música, com keyframes de volume equivalentes ao ducking do render.\n'
            'A trilha “Legendas estilizadas (visual final)” é um ProRes 4444 com transparência '\
            'e reproduz o visual final das legendas. Ela vem bloqueada: mantenha-a visível para '\
            'fidelidade visual ou oculte-a para editar os títulos/SRT nativos.\n'
            'Os SRTs ficam em Captions/. As trilhas de títulos PT/EN permanecem editáveis, mas o ProRes '\
            'é a referência fiel para fundo, opacidade, sombra, contorno e posicionamento.\n'
            'LUT, tratamento de diálogo e masterização constam nos metadados; reaplique-os no Premiere '\
            'quando quiser uma edição não destrutiva.\n'
            'Consulte Metadata/timeline.json e Metadata/validation.json para detalhes.\n'
            'A masterização final deve ser feita após a edição, usando “Masterizar Vídeo”.\n',
            encoding='utf-8',
        )
        progress('COMPRESSING', 88, 'Compactando o pacote completo')
        with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_STORED) as archive:
            for path in sorted(package_root.rglob('*')):
                if path.is_file():
                    relative = path.relative_to(package_root)
                    compression = (
                        zipfile.ZIP_STORED if relative.parts[0] in {'Media', 'Audio', 'Graphics'}
                        else zipfile.ZIP_DEFLATED
                    )
                    archive.write(path, relative, compress_type=compression)
        return timeline, report, timeline_path, output_zip
