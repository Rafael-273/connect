"""Visibility of subtitle tracks in an editorial revision."""


def visible_caption_tracks(tracks, revision):
    hidden = {str(value) for value in ((revision.timeline if revision else {}) or {}).get('hidden_caption_track_ids', [])}
    return [track for track in tracks if str(track.pk) not in hidden]
