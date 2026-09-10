from __future__ import annotations

import re


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def linear_gain_from_db(db):
    return 10 ** (db / 20)


def build_piecewise_expression(keyframes):
    """Builds an ffmpeg-friendly linear-interpolation expression for `volume=eval=frame`.

    Shared by every audio stage that needs a smooth, time-varying gain (ducking,
    dialogue leveling, ...). Mirrors the keyframe interpolation used for Auto Reframe
    pan/zoom, but for a scalar gain value over time instead of a crop position.
    """
    if not keyframes:
        return '1.0'
    ordered = sorted(keyframes, key=lambda item: item[0])
    if len(ordered) == 1:
        return f'{ordered[0][1]:.4f}'
    first_time, first_value = ordered[0]
    expression = f'{ordered[-1][1]:.4f}'
    for (start, start_value), (end, end_value) in reversed(list(zip(ordered, ordered[1:]))):
        duration = max(0.001, end - start)
        interpolated = (
            f'{start_value:.4f}+({end_value - start_value:.4f})'
            f'*(t-{start:.3f})/{duration:.3f}'
        )
        expression = f'if(lt(t\\,{end:.3f})\\,{interpolated}\\,{expression})'
    if first_time > 0:
        return f'if(lt(t\\,{first_time:.3f})\\,{first_value:.4f}\\,{expression})'
    return expression


def settings_from_config(cls, config):
    """Generic `Settings.from_config` for frozen dataclasses of bool/int/float fields.

    Only keys already present as fields on `cls` are honoured, and each value is cast
    to the type of its default so admin-provided JSON (e.g. `{"attack_ms": "250"}`)
    can't accidentally change a field's type.
    """
    config = config or {}
    defaults = cls()
    kwargs = {}
    for attribute in defaults.__dataclass_fields__:
        if attribute in config:
            kwargs[attribute] = type(getattr(defaults, attribute))(config[attribute])
    return cls(**kwargs)


def parse_mean_volume_db(stderr_text: str) -> float | None:
    """Extracts the `mean_volume` measurement ffmpeg's `volumedetect` filter prints to stderr."""
    match = re.search(r'mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB', stderr_text)
    return float(match.group(1)) if match else None
