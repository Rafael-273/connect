"""Move linked picture/sound takes without changing their source coordinates."""
from copy import deepcopy


def clip_range(clip):
    return {
        'source_id': str(clip['asset_id']),
        'start_ms': int(clip['master_in_ms']),
        'end_ms': int(clip['master_out_ms']),
    }


def order_clips(clips, order):
    if not order:
        return clips
    def rank(clip):
        for index, entry in enumerate(order):
            if (str(clip['asset_id']) == entry['source_id']
                    and entry['start_ms'] <= clip['master_in_ms'] < entry['end_ms']):
                return index, clip['master_in_ms']
        return len(order), clip['master_in_ms']
    clips.sort(key=rank)
    cursor = 0
    for clip in clips:
        duration = clip['source_out_ms'] - clip['source_in_ms']
        clip['timeline_in_ms'], clip['timeline_out_ms'] = cursor, cursor + duration
        cursor += duration
    return clips


def timeline_mapping(before, after):
    """Piecewise translations from the previous edited clock to the new one."""
    mapping = []
    for old in before:
        for new in after:
            if str(old['asset_id']) != str(new['asset_id']):
                continue
            left = max(old['master_in_ms'], new['master_in_ms'])
            right = min(old['master_out_ms'], new['master_out_ms'])
            if right <= left:
                continue
            start = old['timeline_in_ms'] + left - old['master_in_ms']
            end = start + right - left
            offset = new['timeline_in_ms'] + left - new['master_in_ms'] - start
            mapping.append((start, end, offset))
    merged = []
    for start, end, offset in sorted(mapping):
        if merged and merged[-1][1] == start and merged[-1][2] == offset:
            merged[-1] = (merged[-1][0], end, offset)
        else:
            merged.append((start, end, offset))
    return merged


def relocate_item(item, mapping):
    parts = []
    for start, end, offset in mapping:
        left, right = max(item['start_ms'], start), min(item['end_ms'], end)
        if right <= left:
            continue
        part = deepcopy(item)
        part['start_ms'], part['end_ms'] = left + offset, right + offset
        if 'source_in_ms' in part:
            part['source_in_ms'] += left - item['start_ms']
        parts.append(part)
    return parts
