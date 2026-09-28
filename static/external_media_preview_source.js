/* Source switching for the interactive timeline player.
 *
 * Browser media events from an old source can arrive after a newer seek. A
 * monotonically increasing request token makes those events harmless and keeps
 * a fast scrub from showing a stale frame or resuming the wrong take.
 */
window.ConnectPreviewSource = class ConnectPreviewSource {
    constructor(video, {holdFrame = () => {}, releaseFrame = () => {}, onLoading = () => {}, play = () => {}} = {}) {
        this.video = video;
        this.holdFrame = holdFrame;
        this.releaseFrame = releaseFrame;
        this.onLoading = onLoading;
        this.play = play;
        this.requestId = 0;
        this.preloader = document.createElement('video');
        this.preloader.muted = true;
        this.preloader.playsInline = true;
        this.preloader.preload = 'auto';
    }

    seek(source, timeSeconds, {autoplay = false, freeze = true} = {}) {
        const time = Math.max(0, Number(timeSeconds) || 0);
        if (this.video.dataset.source === source) {
            this.video.currentTime = time;
            if (autoplay) this.play();
            return;
        }
        const requestId = ++this.requestId;
        if (freeze) this.holdFrame();
        this.onLoading(true);
        this.video.dataset.source = source;
        this.video.src = source;
        let completed = false;
        const complete = () => {
            if (requestId !== this.requestId || completed) return;
            completed = true;
            this.onLoading(false);
            if (freeze) this.releaseFrame();
            if (autoplay) this.play();
        };
        const fail = () => {
            if (requestId !== this.requestId) return;
            this.onLoading(false);
            if (freeze) this.releaseFrame();
        };
        this.video.addEventListener('loadedmetadata', () => {
            if (requestId !== this.requestId) return;
            this.video.currentTime = time;
            // `seeked` only means the timestamp changed; it can still paint a
            // black frame while the decoder catches up. `canplay` guarantees
            // the target source has enough decoded data to replace the held
            // frame without a visible flash.
            this.video.addEventListener('canplay', complete, {once: true});
        }, {once: true});
        this.video.addEventListener('error', fail, {once: true});
    }

    warm(source) {
        if (!source || source === this.video.dataset.source || source === this.preloader.dataset.source) return;
        this.preloader.dataset.source = source;
        this.preloader.src = source;
        this.preloader.load();
    }
};
