/* Source switching for the interactive timeline player.
 *
 * Browser media events from an old source can arrive after a newer seek. A
 * monotonically increasing request token makes those events harmless and keeps
 * a fast scrub from showing a stale frame or resuming the wrong take.
 */
window.ConnectPreviewSource = class ConnectPreviewSource {
    constructor(video, {holdFrame = () => {}, releaseFrame = () => {}, onLoading = () => {}, onVideoChange = () => {}, onTransition = () => {}, frameTolerance = 1 / 30, play = () => {}} = {}) {
        this.video = video;
        this.holdFrame = holdFrame;
        this.releaseFrame = releaseFrame;
        this.onLoading = onLoading;
        this.onVideoChange = onVideoChange;
        this.play = play;
        this.onTransition = onTransition;
        this.frameTolerance = frameTolerance;
        this.requestId = 0;
        this.loadId = 0;
        this.target = null;
        this.loadController = null;
        this.waitController = null;
        this.finishSwap = null;
        this.pendingFrameCheck = null;
        this.frameCallback = null;
        this.warmController = null;
        this.preloader = document.createElement('video');
        this.preloader.muted = true;
        this.preloader.playsInline = true;
        this.preloader.preload = 'auto';
        this.video.preload = 'auto';
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
        this.resumePreload = null;
    }

    seek(source, timeSeconds, {autoplay = false, freeze = true} = {}) {
        const time = Math.max(0, Number(timeSeconds) || 0);
        if (this.finishSwap) this.finishSwap();
        this.cancelFrameCallback();
        if (this.waitingForPreload && (!autoplay || this.target?.source !== source || Math.abs(this.target.time - time) > .001)) this.queuedWarm = null;
        const wasWaiting = Boolean(this.waitingForPreload);
        const requestId = ++this.requestId;
        this.target = {source, time, autoplay};
        this.waitController?.abort();
        this.resumePreload = null;
        this.waitingForPreload = null;
        if (!autoplay && !this.video.paused) this.video.pause();
        if (!source) { this.loadController?.abort(); this.loadId++; this.pendingFrameCheck=null; this.onLoading(false); this.releaseFrame(); return; }
        // A reserved decoder belongs to this transition until it is consumed.
        // canplay/seeked alone are not proof that the requested frame is ready.
        const matchesWarm = this.preloader.dataset.source === source
            && Math.abs(this.preloadedTime - time) < .001 && !this.preloader.error;
        if (autoplay && matchesWarm && !this.isPrepared(source, time, true)) {
            const waiting = `${source}:${time}`;
            this.waitingForPreload = waiting;
            this.waitController = new AbortController();
            const waitOptions = {signal:this.waitController.signal};
            this.onLoading(true);
            this.video.pause();
            this.onTransition({phase:'waiting', source, time});
            const resume = () => {
                if (requestId !== this.requestId || this.waitingForPreload !== waiting) return;
                if (!this.preloader.error && !this.isPrepared(source, time, true)) return;
                this.seek(source, time, {autoplay:true, freeze});
            };
            this.resumePreload = resume;
            for (const event of ['seeked', 'loadeddata', 'canplay', 'progress', 'error']) {
                this.preloader.addEventListener(event, resume, waitOptions);
            }
            // Readiness may have changed while the listeners were installed.
            resume();
            return;
        }
        if (this.isPrepared(source, time, autoplay)) {
            this.loadController?.abort();
            this.pendingFrameCheck=null;
            this.warmController?.abort();
            this.loadId++;
            const outgoing = this.video, incoming = this.preloader;
            const outgoingMuted = outgoing.muted;
            // The requested frame is already decoded by warm(). Do not wait
            // for playing and then another video frame: that freezes the old
            // image and discards the beginning of the incoming dialogue.
            incoming.muted = true;
            incoming.volume = outgoing.volume;
            incoming.playbackRate = outgoing.playbackRate;
            incoming.dataset.source = source;
            this.video = incoming;
            this.preloader = outgoing;
            this.preloadedSource = '';
            this.switching = true;
            this.onTransition({phase:'swap', source, time, actualTime:incoming.currentTime, bufferedAhead:this.bufferedAhead(incoming, time)});
            this.onVideoChange(incoming, outgoing, {continuous:autoplay && !outgoing.paused});
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
                this.flushWarm();
            };
            this.finishSwap = finishReveal;
            finishReveal();
            if (autoplay) {
                this.play();
            }
            return;
        }
        if (this.video.dataset.source === source && !this.video.error) {
            if (wasWaiting && !this.pendingFrameCheck) this.onLoading(false);
            // A second scrub can arrive before metadata for the first one.
            // The metadata callback below reads this.target, never its old time.
            if (this.video.readyState >= HTMLMediaElement.HAVE_METADATA) {
                this.video.currentTime = time;
                if(this.pendingFrameCheck)this.video.addEventListener('seeked',this.pendingFrameCheck,{once:true,signal:this.loadController.signal});
            }
            if (autoplay && this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) this.play();
            this.flushWarm();
            return;
        }
        const loadId = ++this.loadId;
        this.loadController?.abort();
        this.loadController = new AbortController();
        const options = {once:true, signal:this.loadController.signal};
        const decoder = this.video;
        this.onTransition({phase:'cold', source, time});
        if (freeze) this.holdFrame();
        this.onLoading(true);
        this.video.dataset.source = source;
        this.video.src = source;
        let completed = false;
        const complete = () => {
            if (loadId !== this.loadId || completed) return;
            if (decoder.seeking || Math.abs(decoder.currentTime-this.target.time) > .06) return;
            completed = true;
            this.cancelFrameCallback();
            this.pendingFrameCheck=null;
            this.loadController.abort();
            this.onLoading(false);
            if (freeze) this.releaseFrame();
            if (this.target.autoplay) this.play();
            this.flushWarm();
        };
        const fail = () => {
            if (loadId !== this.loadId) return;
            this.cancelFrameCallback();
            this.onLoading(false);
            if (freeze) this.releaseFrame();
        };
        const completeWhenFramePaints = () => {
            if (loadId !== this.loadId || completed) return;
            // `canplay` is not sufficient on all browsers: it may precede the
            // first composited frame after a source switch. Keep the outgoing
            // frame frozen until the incoming image is actually decoded.
            if (typeof decoder.requestVideoFrameCallback === 'function') {
                this.cancelFrameCallback();
                this.frameCallback = {video:decoder, id:decoder.requestVideoFrameCallback(() => complete())};
                // A paused decoder may have already submitted its only frame
                // before this callback was registered. It will not produce a
                // second callback until play(), which itself waits for complete.
                // seeked + HAVE_CURRENT_DATA confirm the target; allow two paint
                // opportunities to break that startup deadlock without playing
                // any frames ahead of the requested edit point.
                requestAnimationFrame(() => requestAnimationFrame(complete));
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

    isAt(source, time) {
        return this.video.dataset.source === source && !this.video.error && !this.video.seeking
            && !this.pendingFrameCheck && !this.waitingForPreload
            && this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
            && Math.abs(this.video.currentTime - time) <= this.frameTolerance;
    }

    flushWarm() {
        if (this.switching || this.waitingForPreload || !this.queuedWarm) return;
        const queued = this.queuedWarm;
        this.queuedWarm = null;
        this.warm(queued.source, queued.time);
    }

    cancelFrameCallback() {
        if (this.frameCallback) this.frameCallback.video.cancelVideoFrameCallback?.(this.frameCallback.id);
        this.frameCallback = null;
    }

    pause() {
        // A loading transition has a paused outgoing element but still has play
        // intent. Cancel that intent without discarding the prepared decoder.
        this.requestId++;
        if (this.target) this.target.autoplay = false;
        this.waitController?.abort();
        this.resumePreload = null;
        this.waitingForPreload = null;
        this.queuedWarm = null;
        this.video.pause();
        if (!this.pendingFrameCheck) this.onLoading(false);
    }

    bufferedAhead(video, time) {
        const ranges = video.buffered;
        for (let index = 0; index < (ranges?.length || 0); index++) {
            if (ranges.start(index) <= time && time < ranges.end(index)) return ranges.end(index) - time;
        }
        return 0;
    }

    isPrepared(source, time, autoplay) {
        const incoming = this.preloader;
        if (this.preloadedSource !== source || incoming.error || incoming.seeking
            || incoming.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
            || Math.abs(incoming.currentTime - time) > this.frameTolerance) return false;
        if (!autoplay) return true;
        // A decoded still frame is insufficient for a running handoff. Keep a
        // small future window, bounded by the remaining resource duration.
        const remaining = Number.isFinite(incoming.duration) ? incoming.duration - time : .12;
        const needed = Math.min(.12 * Math.max(1, this.video.playbackRate), Math.max(0, remaining));
        return incoming.readyState >= 3 && this.bufferedAhead(incoming, time) + .001 >= needed;
    }

    warm(source, timeSeconds = 0) {
        const time = Math.max(0, Number(timeSeconds) || 0);
        if (this.switching || this.waitingForPreload) {
            this.queuedWarm = {source, time};
            return;
        }
        if (!source) return;
        if (source === this.preloader.dataset.source && Math.abs(this.preloadedTime - time) < .001 && !this.preloader.error) return;
        const requestId = ++this.preloadRequestId;
        this.warmController?.abort();this.warmController=new AbortController();
        const warmOptions={signal:this.warmController.signal},preloader=this.preloader;
        this.preloadedSource = '';
        this.preloadedTime = time;
        this.preloader.dataset.source = source;
        this.preloader.src = source;
        // Starting a fully paused media pipeline at the cut can still cost
        // several frames even with all bytes buffered. Prime it muted, then
        // rewind while hidden; presentation never consumes the edit's opening.
        let priming = typeof preloader.requestVideoFrameCallback === 'function';
        const valid = () => requestId === this.preloadRequestId && preloader === this.preloader;
        const markReady = () => {
            if (!valid() || priming) return;
            if (preloader.seeking || preloader.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
            if (Math.abs(preloader.currentTime - time) > this.frameTolerance) return;
            this.preloadedSource = source;
            this.resumePreload?.();
        };
        this.preloader.addEventListener('loadedmetadata', () => {
            if (requestId !== this.preloadRequestId || preloader !== this.preloader) return;
            const target = Math.min(time, Math.max(0, preloader.duration - .001));
            // Assigning zero to an already-zero decoder may never emit seeked.
            if (Math.abs(preloader.currentTime - target) > .001) preloader.currentTime = target;
            if (priming) {
                preloader.play().then(() => {
                    if (!valid()) return;
                    preloader.pause();
                    priming = false;
                    if (Math.abs(preloader.currentTime - target) > .001) preloader.currentTime = target;
                    markReady();
                }).catch(() => { if (valid()) { priming = false; markReady(); } });
            } else markReady();
        }, {...warmOptions,once:true});
        this.preloader.addEventListener('seeked', markReady, warmOptions);
        this.preloader.addEventListener('loadeddata', markReady, warmOptions);
        this.preloader.addEventListener('canplay', markReady, warmOptions);
        this.preloader.addEventListener('progress', markReady, warmOptions);
        this.onTransition({phase:'warm', source, time});
        this.preloader.load();
    }
};
