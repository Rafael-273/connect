from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import qrcode
from PIL import Image, ImageColor, ImageDraw, ImageFont
from django.conf import settings

from ..models.external_media import OverlayPreset, ProjectOverlay, get_external_media_storage
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner


ANIMATIONS = {'NONE', 'FADE', 'SLIDE_UP', 'SLIDE_DOWN', 'SLIDE_LEFT', 'SLIDE_RIGHT', 'POP'}
EASINGS = {'ease', 'ease-in', 'ease-out', 'ease-in-out', 'linear'}


def deep_merge(base, override):
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class OverlayTimelineService:
    """Turns template declarations/project values into renderer-neutral timeline data."""

    @classmethod
    def ensure_project_overlays(cls, project):
        presets = {item.code: item for item in OverlayPreset.objects.filter(is_active=True)}
        existing = {item.overlay_id: item for item in project.overlays.all()}
        for block in project.template_version.blocks.all():
            for definition in block.overlay_definitions or []:
                key = str(definition.get('key') or '').strip()
                if not key:
                    continue
                overlay_id = f'template-{block.pk}-{key}'
                schema = definition.get('fields') or {}
                defaults = {
                    field_key: options.get('default', options.get('default_value', ''))
                    for field_key, options in schema.items()
                    if isinstance(options, dict)
                    and options.get('default', options.get('default_value')) not in (None, '')
                }
                preset = presets.get(definition.get('preset'))
                values = {
                    'block': block,
                    'source': ProjectOverlay.Source.TEMPLATE,
                    'overlay_type': definition.get('type', OverlayPreset.Type.TEXT),
                    'purpose': definition.get('purpose', ''),
                    'preset': preset,
                    'content_schema': schema,
                    'content': defaults,
                    'position': definition.get('position') or {},
                    'style': definition.get('style') or {},
                    'animation': definition.get('animation') or {},
                    'timing_mode': definition.get('timing') or getattr(preset, 'timing_mode', ProjectOverlay.TimingMode.BLOCK_START),
                    'start_ms': max(0, int(definition.get('start_ms') or 0)),
                    'end_ms': definition.get('end_ms'),
                    'duration_ms': max(250, int(definition.get('duration_ms') or getattr(preset, 'duration_ms', 5000))),
                    'allowed_overrides': definition.get('allowed_overrides') or ['content'],
                    'portability': definition.get('portability', ProjectOverlay.Portability.APPROXIMATE),
                    'is_required': bool(definition.get('required')),
                    'is_enabled': True,
                }
                if overlay_id not in existing:
                    existing[overlay_id] = ProjectOverlay.objects.create(
                        project=project, overlay_id=overlay_id, **values,
                    )
        return list(project.overlays.select_related('preset', 'block').all())

    @classmethod
    def compose(cls, project, clips, duration_ms):
        instances = cls.ensure_project_overlays(project)
        block_ranges = {}
        for clip in clips:
            block_id = (clip.get('block') or {}).get('id')
            if not block_id:
                continue
            current = block_ranges.setdefault(block_id, [clip['timeline_in_ms'], clip['timeline_out_ms']])
            current[0] = min(current[0], clip['timeline_in_ms'])
            current[1] = max(current[1], clip['timeline_out_ms'])
        result = []
        for instance in instances:
            if not instance.is_enabled:
                continue
            content = dict(instance.content or {})
            if not cls.has_renderable_content(instance.overlay_type, content, instance.image_file):
                continue
            block_start, block_end = block_ranges.get(instance.block_id, [0, duration_ms])
            start, end = cls.resolve_timing(instance, block_start, block_end, duration_ms)
            preset = instance.preset
            animation = deep_merge(getattr(preset, 'animation', {}), instance.animation)
            animation_type = str(animation.get('type') or 'NONE').upper()
            animation['type'] = animation_type if animation_type in ANIMATIONS else 'NONE'
            easing = str(animation.get('easing') or 'ease-out')
            animation['easing'] = easing if easing in EASINGS else 'ease-out'
            result.append({
                'id': instance.overlay_id,
                'type': instance.overlay_type,
                'purpose': instance.purpose,
                'start_ms': start,
                'end_ms': end,
                'position': deep_merge(getattr(preset, 'position', {}), instance.position),
                'style': deep_merge(getattr(preset, 'style', {}), instance.style),
                'animation': animation,
                'content': content,
                'content_schema': instance.content_schema or {},
                'allowed_overrides': instance.allowed_overrides or [],
                'source': instance.source,
                'block_id': instance.block_id,
                'preset': preset.code if preset else None,
                'portability': instance.portability,
                'required': instance.is_required,
                'image_storage_name': instance.image_file.name if instance.image_file else None,
            })
        return result

    @staticmethod
    def resolve_timing(instance, block_start, block_end, timeline_end):
        duration = max(250, int(instance.duration_ms or 5000))
        if instance.timing_mode == ProjectOverlay.TimingMode.MANUAL:
            start = int(instance.start_ms or 0)
            end = int(instance.end_ms or start + duration)
        elif instance.timing_mode == ProjectOverlay.TimingMode.BLOCK_END:
            end = block_end
            start = max(block_start, end - duration)
        else:  # BLOCK_START and AUTO_BEST_MOMENT fallback
            start = block_start + int(instance.start_ms or 0)
            end = min(block_end, start + duration)
        return max(0, min(start, timeline_end)), max(1, min(max(start + 1, end), timeline_end))

    @staticmethod
    def has_renderable_content(overlay_type, content, image_file=None):
        if overlay_type == OverlayPreset.Type.TEXT:
            return bool(str(content.get('text') or next(iter(content.values()), '')).strip())
        if overlay_type in {OverlayPreset.Type.QR_CODE, OverlayPreset.Type.QR_CODE_CARD}:
            return bool(str(content.get('payload') or '').strip())
        return bool(image_file or content.get('url'))

    @classmethod
    def validate_content(cls, overlay_type, schema, content, image_file=None):
        clean = {}
        for key, options in (schema or {}).items():
            options = options if isinstance(options, dict) else {}
            value = str((content or {}).get(key, '')).strip()
            default = options.get('default', options.get('default_value'))
            if not value and default not in (None, ''):
                value = str(default).strip()
            if options.get('required') and not value:
                raise ValueError(f'{options.get("label") or key} é obrigatório.')
            max_length = int(options.get('max_length') or 2048)
            clean[key] = value[:max_length]
        for key, value in (content or {}).items():
            if key not in clean:
                clean[key] = str(value or '').strip()[:2048]
        if overlay_type in {OverlayPreset.Type.QR_CODE, OverlayPreset.Type.QR_CODE_CARD}:
            payload = clean.get('payload', '')
            if not payload:
                raise ValueError('Informe o conteúdo do QR Code.')
            parsed = urlparse(payload)
            if parsed.scheme and parsed.scheme not in {'http', 'https'}:
                raise ValueError('O QR Code aceita URL HTTP/HTTPS ou texto sem protocolo.')
            if parsed.scheme in {'http', 'https'} and not parsed.netloc:
                raise ValueError('Informe uma URL válida para o QR Code.')
        if overlay_type == OverlayPreset.Type.IMAGE and not image_file and not clean.get('url'):
            raise ValueError('Selecione uma imagem para este elemento visual.')
        return clean


class OverlayAssetRenderer:
    """Creates transparent raster assets; preview and final render use the same data."""

    def render(self, overlay, canvas_width, canvas_height, output_path):
        overlay_type = overlay['type']
        style = overlay.get('style') or {}
        configured_width = (overlay.get('position') or {}).get('width') or style.get('max_width') or 0.24
        configured_width = float(configured_width)
        width = max(80, round(configured_width * canvas_width if configured_width <= 1 else configured_width))
        width = min(canvas_width, width)
        if overlay_type == OverlayPreset.Type.TEXT:
            image = self._text(overlay.get('content') or {}, style, width)
        elif overlay_type == OverlayPreset.Type.QR_CODE:
            image = self._qr((overlay.get('content') or {}).get('payload'), width, style)
        elif overlay_type == OverlayPreset.Type.QR_CODE_CARD:
            image = self._qr_card(overlay.get('content') or {}, style, width)
        elif overlay_type == OverlayPreset.Type.IMAGE:
            image = self._image(overlay, width)
        else:
            raise ExternalMediaError('Tipo de overlay não suportado.')
        image.save(output_path, 'PNG')
        return image.size

    @staticmethod
    def _font(style, size):
        names = [style.get('font'), 'Montserrat-Bold.ttf', 'Arial.ttf', 'DejaVuSans-Bold.ttf']
        roots = [Path(settings.BASE_DIR) / 'static' / 'fonts' / 'subtitles', Path('/usr/share/fonts/truetype/dejavu')]
        for name in names:
            if not name:
                continue
            for root in roots:
                matches = list(root.rglob(str(name))) if root.exists() else []
                if matches:
                    return ImageFont.truetype(str(matches[0]), size)
        return ImageFont.load_default()

    @staticmethod
    def _color(value, opacity=255):
        rgb = ImageColor.getrgb(str(value or '#FFFFFF'))
        return (*rgb[:3], max(0, min(255, int(opacity))))

    def _text(self, content, style, width):
        text = str(content.get('text') or next(iter(content.values()), ''))
        padding = max(8, int(style.get('padding') or 24))
        size = max(12, int(style.get('font_size') or round(width * 0.11)))
        font = self._font(style, size)
        draw_probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        words, lines, current = text.split(), [], ''
        for word in words:
            candidate = f'{current} {word}'.strip()
            if current and draw_probe.textbbox((0, 0), candidate, font=font)[2] > width - padding * 2:
                lines.append(current); current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        bbox = draw_probe.multiline_textbbox((0, 0), '\n'.join(lines), font=font, spacing=round(size * .25), align=style.get('alignment', 'center'))
        height = max(size + padding * 2, bbox[3] - bbox[1] + padding * 2)
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=max(0, int(style.get('border_radius') or 0)),
            fill=self._color(
                style.get('background', '#000000'),
                round(255 * float(style.get('background_opacity', .72))),
            ),
        )
        alignment = str(style.get('alignment') or 'center').lower()
        alignment = alignment if alignment in {'left', 'center', 'right'} else 'center'
        if alignment == 'left':
            origin, anchor = (padding, padding), 'la'
        elif alignment == 'right':
            origin, anchor = (width - padding, padding), 'ra'
        else:
            origin, anchor = (width / 2, padding), 'ma'
        draw.multiline_text(
            origin, '\n'.join(lines), font=font,
            fill=self._color(style.get('color', '#FFFFFF')),
            anchor=anchor, align=alignment, spacing=round(size * .25),
        )
        return image

    def _qr(self, payload, width, style):
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=12, border=4)
        qr.add_data(str(payload)); qr.make(fit=True)
        foreground, background = style.get('qr_color', '#000000'), style.get('qr_background', '#FFFFFF')
        if self._contrast(foreground, background) < 4.5:
            foreground, background = '#000000', '#FFFFFF'
        image = qr.make_image(fill_color=foreground, back_color=background).convert('RGBA')
        return image.resize((width, width), Image.Resampling.NEAREST)

    def _qr_card(self, content, style, width):
        padding = max(12, int(style.get('padding') or width * .08))
        cta = str(content.get('cta') or '')
        title_size = max(12, int(style.get('font_size') or width * .085))
        horizontal = style.get('card_layout') == 'horizontal'
        if horizontal:
            qr_width = max(64, round((width - padding * 3) * .42))
            text_width = max(1, width - padding * 3 - qr_width)
            title_height = round(title_size * 1.5) if cta else 0
            height = max(qr_width + padding * 2, title_height + padding * 2)
        else:
            qr_width = width - padding * 2
            title_height = round(title_size * 1.7) if cta else 0
            height = padding + title_height + qr_width + padding
        card = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=max(0, int(style.get('border_radius') or 18)),
            fill=self._color(style.get('background', '#FFFFFF'), 255),
        )
        if horizontal:
            card.alpha_composite(self._qr(content.get('payload'), qr_width, style), (padding, round((height - qr_width) / 2)))
            if cta:
                draw.multiline_text((padding * 2 + qr_width, height / 2), cta, font=self._font(style, title_size), fill=self._color(style.get('color', '#111827')), anchor='lm', spacing=round(title_size * .2), align='left')
        else:
            if cta:
                draw.text((width / 2, padding), cta, font=self._font(style, title_size), fill=self._color(style.get('color', '#111827')), anchor='ma')
            card.alpha_composite(self._qr(content.get('payload'), qr_width, style), (padding, padding + title_height))
        return card

    @staticmethod
    def _image(overlay, width):
        storage_name = overlay.get('image_storage_name')
        if not storage_name:
            raise ExternalMediaError('A imagem deste overlay não está disponível.')
        try:
            with get_external_media_storage().open(storage_name, 'rb') as handle:
                image = Image.open(handle).convert('RGBA')
                image.load()
        except Exception as exc:
            raise ExternalMediaError('A imagem deste overlay não pôde ser aberta.') from exc
        height = max(1, round(width * image.height / max(1, image.width)))
        return image.resize((width, height), Image.Resampling.LANCZOS)

    @staticmethod
    def _contrast(first, second):
        def luminance(value):
            channels = [component / 255 for component in ImageColor.getrgb(str(value))[:3]]
            channels = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
            return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]
        lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
        return (lighter + .05) / (darker + .05)


class OverlayRenderService:
    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()
        self.assets = OverlayAssetRenderer()

    def apply(self, video_path, output_path, overlays, width, height, workdir):
        overlays = [
            item for item in overlays
            if item.get('end_ms', 0) > (
                item.get('start_ms', 0)
                + max(0, float((item.get('animation') or {}).get('delay') or 0)) * 1000
            )
        ]
        if not overlays:
            return video_path
        command = [settings.FFMPEG_BINARY, '-y', '-i', str(video_path)]
        rendered = []
        for index, overlay in enumerate(overlays):
            path = Path(workdir) / f'overlay_{index:03d}.png'
            size = self.assets.render(overlay, width, height, path)
            rendered.append((overlay, size))
            command.extend(['-loop', '1', '-i', str(path)])
        filters, previous = [], '[0:v]'
        for index, (overlay, (asset_width, asset_height)) in enumerate(rendered):
            start = float(overlay['start_ms']) / 1000
            end = float(overlay['end_ms']) / 1000
            position = overlay.get('position') or {}
            center_x = float(position.get('x', .5)) * width
            center_y = float(position.get('y', .82)) * height
            animation = overlay.get('animation') or {}
            kind = str(animation.get('type') or 'NONE').upper()
            start = min(end, start + max(0, float(animation.get('delay') or 0)))
            anim_duration = min((end - start) / 2, max(.05, float(animation.get('duration') or .35)))
            input_label = f'[{index + 1}:v]'
            prepared = f'[overlay{index}]'
            image_filters = ['format=rgba']
            if kind in {'FADE', 'POP'}:
                image_filters.extend([f'fade=t=in:st={start}:d={anim_duration}:alpha=1', f'fade=t=out:st={max(start, end-anim_duration)}:d={anim_duration}:alpha=1'])
            if kind == 'POP':
                scale = f'0.82+0.18*min(1,max(0,(t-{start})/{anim_duration}))'
                image_filters.append(f"scale=w='iw*({scale})':h='ih*({scale})':eval=frame")
            filters.append(f'{input_label}{",".join(image_filters)}{prepared}')
            progress = f'min(1,max(0,(t-{start})/{anim_duration}))'
            base_x = f'{center_x}-overlay_w/2'
            base_y = f'{center_y}-overlay_h/2'
            if kind == 'SLIDE_UP': y_expr = f'{base_y}+overlay_h*(1-{progress})'
            elif kind == 'SLIDE_DOWN': y_expr = f'{base_y}-overlay_h*(1-{progress})'
            else: y_expr = base_y
            if kind == 'SLIDE_LEFT': x_expr = f'{base_x}+overlay_w*(1-{progress})'
            elif kind == 'SLIDE_RIGHT': x_expr = f'{base_x}-overlay_w*(1-{progress})'
            else: x_expr = base_x
            output = f'[v{index + 1}]'
            filters.append(f"{previous}{prepared}overlay=x='{x_expr}':y='{y_expr}':enable='between(t,{start},{end})'{output}")
            previous = output
        command.extend(['-filter_complex', ';'.join(filters), '-map', previous, '-map', '0:a?', '-c:v', 'libx264'])
        if settings.EXTERNAL_MEDIA_RENDER_PRESET:
            command.extend(['-preset', settings.EXTERNAL_MEDIA_RENDER_PRESET])
        command.extend(['-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', str(output_path)])
        self.runner.run(command)
        return output_path
