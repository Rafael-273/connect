/* Source switching for the interactive timeline player.
 *
 * Browser media events from an old source can arrive after a newer seek. A
 * monotonically increasing request token makes those events harmless and keeps
 * a fast scrub from showing a stale frame or resuming the wrong take.
 */
window.ConnectPreviewSource = class ConnectPreviewSource {
    constructor(video, {holdFrame = () => {}, releaseFrame = () => {}, onLoading = () => {}, onVideoChange = () => {}, play = () => {}} = {}) {
        this.video = video;
        this.holdFrame = holdFrame;
        this.releaseFrame = releaseFrame;
        this.onLoading = onLoading;
        this.onVideoChange = onVideoChange;
        this.play = play;
        this.requestId = 0;
        this.loadId = 0;
        this.target = null;
        this.loadController = null;
        this.waitController = null;
        this.finishSwap = null;
        this.pendingFrameCheck = null;
        this.warmController = null;
        this.preloader = document.createElement('video');
        this.preloader.muted = true;
        this.preloader.playsInline = true;
        this.preloader.preload = 'auto';
        this.video.classList.add('preview-source-buffer');
        this.video.style.zIndex = '1';
        this.preloader.className = 'preview-source-buffer';
        this.preloader.style.opacity = '0';
        this.preloader.style.zIndex = '0';
        video.parentNode?.append(this.preloader);
        this.preloadRequestId = 0;
        this.preloadedSource = '';
        this.preloadedTime = 0;
        this.switching = false;
        this.queuedWarm = null;
        this.waitingForPreload = null;
    }

    seek(source, timeSeconds, {autoplay = false, freeze = true} = {}) {
        const time = Math.max(0, Number(timeSeconds) || 0);
        if (this.finishSwap) this.finishSwap();
        const requestId = ++this.requestId;
        this.target = {source, time, autoplay};
        this.waitController?.abort();
        this.waitingForPreload = null;
        if (!autoplay && !this.video.paused) this.video.pause();
        if (!source) { this.loadController?.abort(); this.loadId++; this.pendingFrameCheck=null; this.onLoading(false); this.releaseFrame(); return; }
        if (this.video.dataset.source === source && !this.video.error) {
            // A second scrub can arrive before metadata for the first one.
            // The metadata callback below reads this.target, never its old time.
            if (this.video.readyState >= HTMLMediaElement.HAVE_METADATA) {
                this.video.currentTime = time;
                if(this.pendingFrameCheck)this.video.addEventListener('seeked',this.pendingFrameCheck,{once:true,signal:this.loadController.signal});
            }
            if (autoplay && this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) this.play();
            return;
        }
        // Never replace the visible decoder with an unready source at a cut.
        // Holding the last frame is preferable to a black flash; the queued
        // seek resumes as soon as the already-requested next take is usable.
        if (autoplay && this.preloader.dataset.source === source && this.preloadedSource !== source) {
            const waiting = `${source}:${time.toFixed(3)}`;
            if (this.waitingForPreload !== waiting) {
                this.waitingForPreload = waiting;
                this.waitController = new AbortController();
                const waitOptions = {once:true, signal:this.waitController.signal};
                this.onLoading(true);
                this.video.pause();
                const resume = () => {
                    if (requestId !== this.requestId || this.waitingForPreload !== waiting) return;
                    this.waitingForPreload = null;
                    this.seek(source, time, {autoplay: true, freeze});
                };
                this.preloader.addEventListener('seeked', resume, waitOptions);
                this.preloader.addEventListener('canplay', resume, waitOptions);
                this.preloader.addEventListener('error', () => {
                    if (this.waitingForPreload === waiting) { this.waitingForPreload = null; this.onLoading(false); }
                }, waitOptions);
            }
            return;
        }
        if (this.preloadedSource === source
            && this.preloader.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
            && Math.abs(this.preloader.currentTime - time) < .35) {
            this.loadController?.abort();
            this.pendingFrameCheck=null;
            this.warmController?.abort();
            this.loadId++;
            const outgoing = this.video, incoming = this.preloader;
            const outgoingMuted = outgoing.muted;
            // Start muted under the visible take. Audio changes at the same
            // moment as the visual frame, avoiding both a flash and overlap.
            incoming.muted = true;
            incoming.volume = outgoing.volume;
            incoming.playbackRate = outgoing.playbackRate;
            incoming.dataset.source = source;
            this.video = incoming;
            this.preloader = outgoing;
            this.preloadedSource = '';
            this.switching = true;
            this.onVideoChange(incoming, outgoing);
            const finishReveal = () => {
                if (this.finishSwap !== finishReveal) return;
                this.finishSwap = null;
                // `playing` can precede compositing by one frame. Put the
                // confirmed incoming frame above the outgoing one and do
                // a hard cut, matching the final render's boundary.
                incoming.style.transition = 'none';
                outgoing.style.transition = 'none';
                incoming.style.opacity = '1'; incoming.style.zIndex = '2';
                outgoing.pause();
                outgoing.style.opacity = '0'; outgoing.style.zIndex = '0';
                incoming.muted = outgoingMuted;
                requestAnimationFrame(() => {
                    incoming.style.zIndex = '1';
                });
                this.preloader = outgoing;
                this.preloader.muted = true;
                this.switching = false;
                this.onLoading(false);
                const queued = this.queuedWarm;
                this.queuedWarm = null;
                if (queued) this.warm(queued.source, queued.time);
            };
            this.finishSwap = finishReveal;
            const reveal = () => {
                if (typeof incoming.requestVideoFrameCallback === 'function') {
                    incoming.requestVideoFrameCallback(finishReveal);
                } else requestAnimationFrame(finishReveal);
            };
            if (autoplay) {
                incoming.addEventListener('playing', () => { if(requestId === this.requestId) reveal(); }, {once: true});
                this.onLoading(true);
                this.play();
            } else reveal();
            return;
        }
        const loadId = ++this.loadId;
        this.loadController?.abort();
        this.loadController = new AbortController();
        const options = {once:true, signal:this.loadController.signal};
        const decoder = this.video;
        if (freeze) this.holdFrame();
        this.onLoading(true);
        this.video.dataset.source = source;
        this.video.src = source;
        let completed = false;
        const complete = () => {
            if (loadId !== this.loadId || completed) return;
            if (decoder.seeking || Math.abs(decoder.currentTime-this.target.time) > .06) return;
            completed = true;
            this.pendingFrameCheck=null;
            this.loadController.abort();
            this.onLoading(false);
            if (freeze) this.releaseFrame();
            if (this.target.autoplay) this.play();
        };
        const fail = () => {
            if (loadId !== this.loadId) return;
            this.onLoading(false);
            if (freeze) this.releaseFrame();
        };
        const completeWhenFramePaints = () => {
            if (loadId !== this.loadId || completed) return;
            // `canplay` is not sufficient on all browsers: it may precede the
            // first composited frame after a source switch. Keep the outgoing
            // frame frozen until the incoming image is actually decoded.
            if (typeof this.video.requestVideoFrameCallback === 'function') {
                this.video.requestVideoFrameCallback(() => complete());
            } else {
                requestAnimationFrame(complete);
            }
        };
        const waitForTargetFrame = () => {
            if (loadId !== this.loadId) return;
            if (this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
                completeWhenFramePaints();
            } else {
                this.video.addEventListener('loadeddata', completeWhenFramePaints, options);
            }
        };
        this.pendingFrameCheck=waitForTargetFrame;
        this.video.addEventListener('loadedmetadata', () => {
            if (loadId !== this.loadId) return;
            decoder.currentTime = this.target.time;
            this.video.addEventListener('seeked', waitForTargetFrame, options);
            // At timestamp zero, some engines do not dispatch `seeked`.
            if (this.target.time < 0.01) {
                this.video.addEventListener('loadeddata', waitForTargetFrame, options);
            }
        }, options);
        this.video.addEventListener('error', fail, options);
    }

    warm(source, timeSeconds = 0) {
        const time = Math.max(0, Number(timeSeconds) || 0);
        if (this.switching) {
            this.queuedWarm = {source, time};
            return;
        }
        if (!source || source === this.video.dataset.source) return;
        if (source === this.preloader.dataset.source && Math.abs(this.preloadedTime - time) < 0.05) return;
        const requestId = ++this.preloadRequestId;
        this.warmController?.abort();this.warmController=new AbortController();
        const warmOptions={once:true,signal:this.warmController.signal},preloader=this.preloader;
        this.preloadedSource = '';
        this.preloadedTime = time;
        this.preloader.dataset.source = source;
        this.preloader.src = source;
        this.preloader.addEventListener('loadedmetadata', () => {
            if (requestId !== this.preloadRequestId || preloader !== this.preloader) return;
            this.preloader.currentTime = Math.min(time, Math.max(0, this.preloader.duration - 0.05));
        }, warmOptions);
        this.preloader.addEventListener('seeked', () => {
            if (requestId !== this.preloadRequestId || preloader !== this.preloader) return;
            this.preloadedSource = source;
        }, warmOptions);
        this.preloader.load();
    }
};
