"""Timeline transition validation and FFmpeg composition shared by visual layers."""

import math


EFFECTS = {'blur_fade', 'slide', 'zoom', 'wipe'}
DIRECTIONS = {'left', 'right', 'top', 'bottom'}
EASINGS = {'linear', 'ease_in', 'ease_out', 'ease_in_out'}


def number(value, low, high):
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise ValueError('Valor inválido na transição.')
    return max(low, min(high, value))


def normalize_transitions(value):
    if not isinstance(value, dict) or value.keys() - {'in', 'out'}:
        raise ValueError('Configuração de transições inválida.')
    result = {}
    for side, edge in value.items():
        if not isinstance(edge, dict) or not isinstance(edge.get('effects'), list):
            raise ValueError('Informe os efeitos da transição.')
        easing = edge.get('easing', 'ease_out' if side == 'in' else 'ease_in')
        if easing not in tuple(EASINGS):
            raise ValueError('Curva de transição inválida.')
        effects, seen = [], set()
        for effect in edge['effects']:
            kind = effect.get('type') if isinstance(effect, dict) else None
            if kind not in tuple(EFFECTS) or kind in seen:
                raise ValueError('Use uma instância de cada efeito por transição.')
            seen.add(kind)
            item = {'type': kind}
            if kind in ('slide', 'wipe'):
                direction = effect.get('direction', 'left')
                if direction not in tuple(DIRECTIONS):
                    raise ValueError('Direção de transição inválida.')
                item['direction'] = direction
            if kind == 'slide':
                mode = effect.get('mode', 'outside')
                if mode not in ('outside', 'smooth'):
                    raise ValueError('Modo de deslocamento inválido.')
                item.update(mode=mode, distance=number(effect.get('distance', 20), 0, 100))
            if kind == 'zoom':
                item['scale'] = number(effect.get('scale', .8), .25, 2)
            if kind == 'wipe':
                item['softness'] = number(effect.get('softness', 0), 0, 20)
            if kind == 'blur_fade':
                item['blur'] = number(effect.get('blur', 18), 0, 32)
            effects.append(item)
        if effects:
            result[side] = {'duration_ms': round(number(edge.get('duration_ms', 800), 1, 5000)),
                            'easing': easing, 'effects': effects}
    return result


def effective_edges(item, duration_ms):
    edges = item.get('transitions') or {}
    total = sum(edge['duration_ms'] for edge in edges.values())
    if duration_ms <= 0 or not total:
        return []
    factor = min(1, duration_ms / total)
    return [(side, edge, edge['duration_ms'] * factor / 1000) for side, edge in edges.items()]


class TransitionFilters:
    def __init__(self, item, start_ms, end_ms, clock_origin_ms, pixel_ratio=1, *, segment_end_ms=None, base=None):
        self.effects = []
        for side, edge, duration in effective_edges(item, end_ms - start_ms):
            if segment_end_ms is not None:
                if side == 'in' and clock_origin_ms >= start_ms + duration * 1000:
                    continue
                if side == 'out' and segment_end_ms <= end_ms - duration * 1000:
                    continue
            self.effects.extend((side, effect, duration, edge['easing']) for effect in edge['effects'])
        self.start = (start_ms - clock_origin_ms) / 1000
        self.end = (end_ms - clock_origin_ms) / 1000
        self.pixel_ratio = pixel_ratio
        self.rotation = number((base or {}).get('rotation', 0), -360, 360)
        self.opacity = number((base or {}).get('opacity', 1), 0, 1)

    def progress(self, side, duration, easing, clock):
        elapsed = f'({clock}-{self.start:.9f})' if side == 'in' else f'({self.end:.9f}-{clock})'
        p = f'clip({elapsed}/{duration:.9f},0,1)'
        if easing == 'ease_in':
            return f'pow({p},2)'
        if easing == 'ease_out':
            return f'(1-pow(1-{p},2))'
        if easing == 'ease_in_out':
            return f'({p}*{p}*(3-2*{p}))'
        return p

    @staticmethod
    def _sample_progress(p, easing):
        p = max(0, min(1, p))
        if easing == 'ease_in':
            return p ** 2
        if easing == 'ease_out':
            return 1 - (1 - p) ** 2
        if easing == 'ease_in_out':
            return p * p * (3 - 2 * p)
        return p

    def blur_filter(self, label, effects):
        # FFmpeg supports runtime sigma changes. Bound commands regardless of
        # clip length and sample within the original transition clock.
        samples = {0.0: 0.0}
        windows = []
        for side, sigma, duration, easing in effects:
            left = self.start if side == 'in' else self.end - duration
            right = left + duration
            if right <= 0:
                continue
            windows.append(f"gte(t,{left:.9f})*lt(t,{right:.9f})")
            for timestamp in {max(0, left), *(left + i * duration / 32 for i in range(33))}:
                if timestamp < 0:
                    continue
                progress = (timestamp - left) / duration
                if side == 'out':
                    progress = 1 - progress
                samples[timestamp] = sigma * self.pixel_ratio * (1 - self._sample_progress(progress, easing))
        commands = ';'.join(f'{t:.9f} gblur@{label} sigma {sigma:.5f},gblur@{label} sigmaV {sigma:.5f}'
                            for t, sigma in sorted(samples.items()))
        return [f"sendcmd=c='{commands}'", f"gblur@{label}=sigma=0:planes=15:enable='{'+'.join(windows)}'"] if windows else []

    def prepare(self, source, output, filters, *, base_scale=None):
        alpha = [str(self.opacity)] if self.opacity != 1 else []
        blur, scale, masking = [], [base_scale] if base_scale else [], []
        for side, effect, duration, easing in self.effects:
            p = self.progress(side, duration, easing, 'T')
            kind = effect['type']
            if kind in ('blur_fade', 'wipe'):
                masking.append(f'lt({self.progress(side, duration, easing, "t")},1)')
            if kind == 'blur_fade':
                alpha.append(p)
                if effect['blur']:
                    blur.append((side, effect['blur'], duration, easing))
            elif kind == 'wipe':
                coordinate = {'left': '(X+.5)/W', 'right': '1-(X+.5)/W',
                              'top': '(Y+.5)/H', 'bottom': '1-(Y+.5)/H'}[effect['direction']]
                softness = effect.get('softness', 0) / 100
                if softness:
                    alpha.append(f'clip(({p}*{1 + softness}-{coordinate})/{softness},0,1)')
                else:
                    alpha.append(f'lte({coordinate},{p})')
            elif kind == 'zoom':
                p = self.progress(side, duration, easing, 't')
                scale.append(f"(1+({effect['scale']}-1)*(1-{p}))")
        chain = [*filters, 'format=gbrap' if alpha or blur else 'format=rgba']
        if blur:
            chain.extend(self.blur_filter(output.strip('[]'), blur))
        if alpha:
            enable = f":enable='{'+'.join(masking)}'" if self.opacity == 1 else ''
            chain.append("geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                         f"a='alpha(X,Y)*{'*'.join(alpha)}'{enable}")
        if self.rotation:
            angle = self.rotation * math.pi / 180
            chain.append(f"rotate={angle}:ow='ceil(rotw({angle}))':oh='ceil(roth({angle}))':c=none")
        if scale:
            factor = '*'.join(scale)
            chain.append(f"scale=w='max(2,round(iw*({factor})))':h='max(2,round(ih*({factor})))':eval=frame")
        return f'{source}{",".join(chain + ["format=rgba"])}{output}'

    def position(self, x, y):
        for side, effect, duration, easing in self.effects:
            if effect['type'] != 'slide':
                continue
            p = self.progress(side, duration, easing, 't')
            direction = effect['direction']
            horizontal = direction in ('left', 'right')
            base, frame, edge = (x, 'W', 'w') if horizontal else (y, 'H', 'h')
            negative = direction in ('left', 'top')
            if effect['mode'] == 'outside':
                delta = f'-max(0,({base})+{edge})' if negative else f'max(0,{frame}-({base}))'
            else:
                delta = f"{'-' if negative else ''}{frame}*{effect['distance']/100}"
            position = f'({base})+({delta})*(1-{p})'
            if horizontal:
                x = position
            else:
                y = position
        return x, y
