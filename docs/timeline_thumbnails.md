Timeline take thumbnails
=======================

The timeline loads a persisted 240 × 135 JPEG for each source proxy. Cuts using
the same proxy share its thumbnail, including across page reloads and users.
The thumbnail is a representative first frame of the source, rather than a new
frame extracted for every trim point. Video B-roll thumbnails now use the
derived-media cache documented in `editor_preview_cache.md`.

`ProjectSourceProxy.metadata.timeline_thumbnail` stores the storage name,
version, token, status and request time. No database migration is required.
The version incorporates the proxy file name and its update timestamp, so a
rebuilt proxy invalidates its old image. Thumbnail writes intentionally do not
change that timestamp or the playback URL.

Proxy preparation schedules generation on the existing `media_previews` queue.
For old projects, the authenticated source endpoint with `thumbnail=1` schedules
the missing image and returns HTTP 202 with `Cache-Control: no-store`. The browser
retries at bounded intervals, displaying the image once ready. It does not open
a video decoder to generate take thumbnails. Duplicate requests are deduplicated
under a database lock, and the worker claims the generation token before FFmpeg.
The first-frame extraction uses one thread and a 60-second timeout.

Ready images use the existing protected storage delivery. Local JPEG responses
have a private one-day cache; signed-storage redirects have a shorter cache to
avoid retaining expired download URLs. Project deletion includes the stored
thumbnail. Updating application code and restarting the preview worker is
required for the new Celery task to be available. No production deployment is
performed by this change.
