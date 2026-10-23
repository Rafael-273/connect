"""Pure soundtrack transition geometry. Keep in sync with the browser module.

A transition belongs to the incoming clip and names its outgoing neighbour.
Clip bounds stay editorial; only the audible ranges extend into real handles.
Half-clip limits prevent transitions on adjacent joins from colliding.
"""


def transition_window(left, right, source_duration_ms, incoming_duration_ms=None):
    spec = right.get('audio_transition')
    if not isinstance(spec, dict) or spec.get('type') != 'CONSTANT_POWER':
        return None
    expected = left.get('clip_id') or left.get('track_id', left.get('id'))
    source = spec.get('from_clip_id') or spec.get('from_track_id')
    if str(source) != str(expected):
        return None
    cut = int(right['start_ms'])
    if int(left['end_ms']) != cut:
        return None
    requested = max(0, min(10000, int(spec.get('duration_ms') or 0)))
    left_length = cut - int(left['start_ms'])
    right_length = int(right['end_ms']) - cut
    head = min(max(0, int(right.get('source_in_ms') or 0)), left_length // 2)
    if incoming_duration_ms is not None and int(right.get('source_in_ms') or 0) > incoming_duration_ms:
        head = 0  # A loop iteration is not an original-file handle.
    tail = min(max(0, int(source_duration_ms) - int(left.get('source_in_ms') or 0) - left_length), right_length // 2)
    before = min(requested // 2, head)
    after = min(requested - before, tail)
    before = min(requested - after, head)
    if before + after <= 0:
        return None
    return {'start_ms': cut - before, 'end_ms': cut + after, 'duration_ms': before + after}
