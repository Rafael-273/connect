/* Shared audio clock for the interactive media editor.
 *
 * Keep it framework-free: the preview page owns its timeline data while this
 * module owns only the HTMLAudioElement state.  That boundary makes pauses,
 * seeks and music replacements deterministic and independently testable.
 */
window.ConnectPreviewAudio = class ConnectPreviewAudio {
    constructor(audio) {
        this.audio = audio;
        this.channels = [audio];
        this.tracks = [];
        this.signature = '';
        this.position = {index: 0, local: 0, overlap: 0};
        this.isPlaying = false;
        this.bedCache = null;
        this.automationCache = new WeakMap();
        this.globalMs = 0;
        this.sequenceDurationMs = 0;
        this.captions = [];
    }

    invalidateTiming() { this.bedCache=null; this.automationCache=new WeakMap(); }

    configure(config) {
        this.invalidateTiming();
        if (!config) {
            this.pause();
            this.tracks = [];
            this.signature = '';
            this.audio.removeAttribute('src');
            this.audio.dataset.source = '';
            return;
        }
        const tracks = (config.tracks?.length ? config.tracks : [config])
            .filter(track => track?.url);
        const signature = JSON.stringify(tracks.map(track => [
            track.clip_id || track.id || track.url, track.url,
        ]));
        this.tracks = tracks;
        if (this.signature !== signature) {
            this.pause();
            this.signature = signature;
            this.position = {index: -1, local: 0, overlap: 0, active: []};
            this.tracks = tracks;
            this.channels = tracks.map((track, index) => index === 0 ? this.audio : new Audio());
            this.channels.forEach((channel, index) => {
                channel.loop = true;
                channel.preload = 'auto';
                channel.dataset.source = tracks[index].url;
                channel.src = tracks[index].url;
                channel.load();
            });
        }
        this.applyVolumes(config, this.globalMs, this.volumeFactorAt(config, this.globalMs));
    }

    sync(config, globalMs, {force = false} = {}) {
        if (!config || !this.channels.length) return;
        const position = this.positionAt(globalMs);
        if (!position) return;
        this.position = position;
        this.globalMs = Math.max(0, Number(globalMs) || 0);
        // Apply the final gain before starting a newly active channel. Never
        // pass through mute or the unattenuated volume on the video clock.
        this.applyVolumes(config, this.globalMs, this.volumeFactorAt(config, this.globalMs));
        this.channels.forEach((channel, index) => {
            const shouldBeActive = position.active ? position.active.some(item => item.index === index) : index === position.index || index === position.index + 1 && position.overlap > 0;
            if (!shouldBeActive) {
                channel.pause();
                return;
            }
            const target = position.active ? position.active.find(item => item.index === index).local : index === position.index ? position.local : (position.nextLocal ?? Math.max(0, position.local - this.duration(index) + position.overlap));
            if (Math.abs(channel.currentTime - target) > (force ? .025 : .55)) channel.currentTime = target;
            if (this.isPlaying && channel.paused) channel.play().catch(() => {});
        });
    }

    updateVolume(config, sequenceDurationMs, globalMs, captions) {
        if (!config) return;
        this.sequenceDurationMs = Math.max(1, Number(sequenceDurationMs) || 1);
        this.captions = captions || [];
        this.sync(config, globalMs);
    }

    volumeFactorAt(config, globalMs) {
        const nowMs = Math.max(0, Number(globalMs) || 0);
        let factor = 1;
        const fadeInMs = Math.max(0, Number(config.fade_in_seconds) || 0) * 1000;
        const fadeOutMs = Math.max(0, Number(config.fade_out_seconds) || 0) * 1000;
        if (fadeInMs) factor = Math.min(factor, nowMs / fadeInMs);
        if (fadeOutMs && this.sequenceDurationMs) factor = Math.min(factor, Math.max(0, (this.sequenceDurationMs - nowMs) / fadeOutMs));
        if (config.ducking_enabled) factor *= this.duckingFactor(config, nowMs, this.captions);
        factor *= this.manualAutomationFactor(config.audio_mix?.music_keyframes, nowMs);
        return factor;
    }

    pause() {
        this.isPlaying = false;
        this.channels.forEach(channel => channel.pause());
    }

    play() {
        this.isPlaying = true;
        if (this.position.index < 0) return Promise.resolve();
        if (this.position.active) return Promise.all(this.position.active.map(item => this.channels[item.index].play())).then(() => undefined);
        const current = this.channels[this.position.index] || this.audio;
        const next = this.channels[this.position.index + 1];
        const promises = [current.play()];
        if (next && this.position.overlap > 0) promises.push(next.play());
        return Promise.all(promises).then(() => undefined);
    }

    duration(index) {
        const duration = this.channels[index]?.duration;
        return Number.isFinite(duration) && duration > 0 ? duration : 0;
    }

    positionAt(globalMs) {
        if (!this.tracks.length || this.channels.some((_, index) => !this.duration(index))) return null;
        const nowMs = Math.max(0, Number(globalMs) || 0);
        const scheduled = this.tracks.every(track => Number.isFinite(Number(track.start_ms)) && Number.isFinite(Number(track.end_ms)) && Number(track.end_ms) > Number(track.start_ms));
        if (scheduled) {
            const ranges = this.tracks.map(track => ({start:Number(track.start_ms),end:Number(track.end_ms),source:Number(track.source_in_ms)||0, gain:Math.max(0, Math.min(1, Number(track.volume ?? 1)))}));
            this.tracks.slice(1).forEach((track, offset) => {
                const index=offset+1,left=this.tracks[offset];
                let fadeWindow=window.ConnectMusicTransitions?.windowFor(left,track,this.duration(offset)*1000,this.duration(index)*1000)||null;
                if (!track.audio_transition && String(track.transition||'NONE').toUpperCase()==='CONSTANT_POWER') {
                    const start=Math.max(Number(left.start_ms),Number(track.start_ms));
                    const duration=Math.min(Math.max(0,Number(left.end_ms)-start),Number(track.end_ms)-start,Math.max(0,Number(track.transition_duration_ms??2000)));
                    if(duration>0)fadeWindow={start_ms:start,end_ms:start+duration,duration_ms:duration};
                }
                if(!fadeWindow)return;
                if(track.audio_transition){
                    ranges[offset].end=fadeWindow.end_ms;
                    ranges[index].source-=Number(track.start_ms)-fadeWindow.start_ms;
                    ranges[index].start=fadeWindow.start_ms;
                }
                const progress=Math.max(0,Math.min(1,(nowMs-fadeWindow.start_ms)/fadeWindow.duration_ms));
                ranges[offset].gain*=Math.cos(progress*Math.PI/2);
                ranges[index].gain*=Math.sin(progress*Math.PI/2);
            });
            const active=ranges.flatMap((range,index)=> nowMs>=range.start&&nowMs<range.end ? [{index,local:(range.source+(nowMs-range.start))/1000%this.duration(index),gain:range.gain}]:[]);
            return {index:active[0]?.index??-1,local:active[0]?.local??0,overlap:0,active};
        }
        const spans = this.channels.map((_, index) => this.duration(index));
        const overlaps = spans.map((_, index) => index && String(this.tracks[index].transition||'CONSTANT_POWER').toUpperCase()==='CONSTANT_POWER' ? Math.max(0,Math.min(
            spans[index - 1] - .05, spans[index] - .05,
            Math.max(0, Number(this.tracks[index].transition_duration_ms ?? 2000) / 1000),
        )) : 0);
        const playlistDuration = spans.reduce((total, value) => total + value, 0) - overlaps.reduce((total, value) => total + value, 0);
        const target = nowMs / 1000 % Math.max(.01, playlistDuration);
        let start=0;
        for(let index=0;index<spans.length;index++){
            const end=start+spans[index],overlap=overlaps[index+1]||0;
            if(target<end){
                const inOverlap=overlap>0&&target>=end-overlap;
                return {index,local:target-start,overlap:inOverlap?overlap:0,nextLocal:inOverlap?target-(end-overlap):0,overlapProgress:inOverlap?(target-(end-overlap))/overlap:0};
            }
            start=end-overlap;
        }
        return {index:0,local:0,overlap:0};
    }

    applyVolumes(config, globalMs, factor = 1) {
        const base = this.baseVolume(config) * factor;
        const volumes = this.channels.map(() => 0);
        if (this.position.active) {
            this.position.active.forEach(item => { volumes[item.index] = base * item.gain; });
        } else if (this.channels[this.position.index]) {
            if (this.position.overlap > 0) {
                const progress = this.position.overlapProgress ?? Math.max(0, Math.min(1, (this.position.local - (this.duration(this.position.index) - this.position.overlap)) / this.position.overlap));
                volumes[this.position.index] = base * Math.cos(progress * Math.PI / 2);
                volumes[this.position.index + 1] = base * Math.sin(progress * Math.PI / 2);
            } else volumes[this.position.index] = base;
        }
        this.channels.forEach((channel, index) => {
            const gain = volumes[index];
            const volume = Number.isFinite(gain) ? Math.max(0, Math.min(1, gain)) : 0;
            // Repeated HTMLMediaElement.volume writes reach the browser audio
            // mixer. Write only the final value, and only when it changes.
            if (channel.volume !== volume) channel.volume = volume;
        });
    }

    baseVolume(config) {
        return Math.max(0, Math.min(1, Number(config?.volume ?? .15)));
    }

    duckingFactor(config, nowMs, captions) {
        const attack = Math.max(1, Number(config.attack_ms) || 140);
        const hold = Math.max(0, Number(config.hold_ms) || 300);
        const release = Math.max(1, Number(config.release_ms) || 850);
        const gap = Math.max(0, Number(config.speech_gap_hold_ms) || 1800);
        const duck = Math.pow(10, -Math.max(0, Number(config.duck_db) || 14) / 20);
        let beds = this.bedCache?.captions === captions && this.bedCache.gap === gap ? this.bedCache.beds : null;
        if (!beds) {
            beds = [];
            [...(captions || [])]
                .sort((left, right) => Number(left.start_ms) - Number(right.start_ms))
                .forEach(cue => {
                    const start = Number(cue.start_ms), end = Number(cue.end_ms);
                    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
                    const previous = beds.at(-1);
                    if (previous && start - previous.end <= gap) previous.end = Math.max(previous.end, end);
                    else beds.push({start, end});
                });
            this.bedCache = {captions, gap, beds};
        }
        let low = 0, high = beds.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (beds[middle].end + hold + release < nowMs) low = middle + 1;
            else high = middle;
        }
        const bed = beds[low];
        if (bed && nowMs >= bed.start) {
            if (nowMs < bed.start + attack) return 1 - (1 - duck) * ((nowMs - bed.start) / attack);
            if (nowMs <= bed.end + hold) return duck;
            return duck + (1 - duck) * ((nowMs - bed.end - hold) / release);
        }
        return Math.max(0, Number(config.between_speech_gain ?? 1));
    }

    manualAutomationFactor(keyframes, nowMs) {
        if (!Array.isArray(keyframes) || !keyframes.length) return 1;
        let points = this.automationCache.get(keyframes);
        if (!points) {
            points = [...keyframes]
                .map(point => ({time: Number(point.time_ms), gain: Math.pow(10, Number(point.gain_db || 0) / 20)}))
                .filter(point => Number.isFinite(point.time) && Number.isFinite(point.gain))
                .sort((left, right) => left.time - right.time);
            this.automationCache.set(keyframes, points);
        }
        if (!points.length) return 1;
        if (nowMs <= points[0].time) return points[0].gain;
        let low = 1, high = points.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (points[middle].time < nowMs) low = middle + 1;
            else high = middle;
        }
        if (low < points.length) {
            const right = points[low], left = points[low - 1];
            const progress = (nowMs - left.time) / Math.max(1, right.time - left.time);
            return left.gain + (right.gain - left.gain) * progress;
        }
        return points.at(-1).gain;
    }
};

/* The soundtrack follows decoded video frames, not the user's play intent.
 * A watchdog also covers decoder freezes that emit no waiting event. */
window.ConnectPreviewSoundtrackClock = class ConnectPreviewSoundtrackClock {
    constructor(controller, {sync, setTimer = (callback, delay) => setTimeout(callback, delay), clearTimer = timer => clearTimeout(timer), timeoutMs = 400} = {}) {
        this.controller = controller;
        this.sync = sync;
        this.setTimer = setTimer;
        this.clearTimer = clearTimer;
        this.timeoutMs = timeoutMs;
        this.bound = new WeakSet();
        this.video = null;
        this.loading = false;
        this.playing = false;
        this.advancing = false;
        this.lastTime = null;
        this.timer = null;
    }

    stop({retainTime = false} = {}) {
        this.advancing = false;
        if (!retainTime) this.lastTime = null;
        this.clearTimer(this.timer);
        this.timer = null;
        this.controller.pause();
    }

    setVideo(video) {
        this.stop();
        this.video = video;
        this.playing = false;
        if (this.bound.has(video)) return;
        this.bound.add(video);
        const block = () => {
            if (video !== this.video) return;
            this.playing = false;
            this.stop();
        };
        for (const name of ['play', 'pause', 'waiting', 'seeking', 'loadstart', 'emptied', 'error', 'ended']) video.addEventListener(name, block);
        for (const name of ['playing', 'seeked']) video.addEventListener(name, () => {
            if (video === this.video && !video.paused && !video.seeking && !video.error) this.playing = true;
        });
        // A stalled download does not necessarily stop playback while buffered
        // frames remain. Only block here when the decoder lacks future data.
        video.addEventListener('stalled', () => { if (video.readyState < 3) block(); });
    }

    setLoading(value) {
        this.loading = value;
        if (value) this.stop();
    }

    canPlay() {
        return this.playing && this.advancing && !this.loading && this.video
            && !this.video.paused && !this.video.seeking && !this.video.error && this.video.readyState >= 3;
    }

    frame(video, time) {
        if (video !== this.video || !this.playing || this.loading || video.paused || video.seeking || video.error || video.readyState < 3) return;
        if (!Number.isFinite(time) || time === this.lastTime) return;
        this.lastTime = time;
        this.advancing = true;
        this.clearTimer(this.timer);
        this.timer = this.setTimer(() => this.stop({retainTime:true}), this.timeoutMs);
        if (!this.controller.isPlaying) {
            this.sync();
            this.controller.play().then(() => {
                if (!this.canPlay()) this.controller.pause();
            }).catch(() => {});
        }
    }
};
