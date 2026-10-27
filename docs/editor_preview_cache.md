Editor preview caches
=====================

The editor persists derived waveform JSON, video B-roll JPEG thumbnails and
Original/Treated noise comparison clips in `ProjectPreviewArtifact`. The
timeline remains the source of editorial decisions; these rows are disposable
derived media, not new revisions. Source-take JPEGs retain the existing
`ProjectSourceProxy.metadata.timeline_thumbnail` cache.

Cache keys include the cache schema version, artifact kind, source file identity,
source version and generation parameters. Volume/timing edits do not invalidate
a source waveform. Replacing the source or changing noise processing parameters
creates a new key. Original and Treated variants have independent keys. Ready
files survive page reloads and are shared by project members. Project deletion
removes their stored files as well as the database rows.

Generation runs on the existing `media_previews` Celery queue. A database unique
constraint plus a transaction/lease prevents duplicate generation for the same
key. Workers claim a job before running FFmpeg and reject obsolete leases.
Pending/error responses are not cached. Requests only schedule work and serve
finished files; they never run FFmpeg. Comparisons poll a lightweight status URL
before starting playback, including cancellation when switching variants.

Waveforms use a one-time mono PCM extraction at 8 kHz to compute 4096 visual
peak buckets with bounded reads. The browser receives only duration and peaks,
and no longer downloads/decodes entire media files to draw the waveform. This
sampling rate applies only to the visual data; preview/export audio is unchanged.
B-roll thumbnails use a single 240 × 135 JPEG frame. Noise comparison encoding
retains the existing cleanup provider and AAC settings. These changes do not
change the video preview resolution or final render settings.

Scheduled music channels load within a small window around the playhead, including
transition handles and the upcoming source, and release distant decoders after
seeking. Source durations are reused across cuts. Legacy unscheduled playlists
load duration metadata for their sources and preload only the current/next
music for playback.

Deployment
----------

Run `python manage.py migrate` to apply `0119_projectpreviewartifact`, then
restart the web application and the `media_previews` worker to register
`external_media.create_preview_artifact`. Refresh editor tabs after updating
because `waveform=1` now returns persisted JSON rather than full audio bytes.
Keep preview-worker concurrency at one on small instances. The first request
for an uncached source can show a preparation state while its job is queued;
subsequent opens reuse the stored output.

Validation
----------

JavaScript tests cover pending/cache status polling, cancellation, stored peak
loading, independent clip gains, crossfades, scrubbing and bounded music loading.
Python unit tests cover cache identity/invalidation, duplicate requests, worker
leases, persisted-file reuse, normalized PCM peaks and bounded streaming.
`EditorPersistenceTests` also includes database-backed cache-reopening coverage.
