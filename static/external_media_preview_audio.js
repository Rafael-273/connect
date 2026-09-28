/* Shared audio clock for the interactive media editor.
 *
 * Keep it framework-free: the preview page owns its timeline data while this
 * module owns only the HTMLAudioElement state.  That boundary makes pauses,
 * seeks and music replacements deterministic and independently testable.
 */
window.ConnectPreviewAudio = class ConnectPreviewAudio {
    constructor(audio) {
        this.audio = audio;
    }

    configure(config) {
        if (!config) {
            this.audio.pause();
            this.audio.removeAttribute('src');
            this.audio.dataset.source = '';
            return;
        }
        if (this.audio.dataset.source !== config.url) {
            this.audio.dataset.source = config.url;
            this.audio.src = config.url;
            this.audio.load();
        }
        this.audio.volume = this.baseVolume(config);
    }

    sync(config, globalMs) {
        if (!config || !Number.isFinite(this.audio.duration) || this.audio.duration <= 0) return;
        const target = (Math.max(0, Number(globalMs) || 0) / 1000) % this.audio.duration;
        if (Math.abs(this.audio.currentTime - target) > .55) this.audio.currentTime = target;
    }

    updateVolume(config, sequenceDurationMs, globalMs, captions) {
        if (!config) return;
        const nowMs = Math.max(0, Number(globalMs) || 0);
        const durationMs = Math.max(1, Number(sequenceDurationMs) || 1);
        let factor = 1;
        const fadeInMs = Math.max(0, Number(config.fade_in_seconds) || 0) * 1000;
        const fadeOutMs = Math.max(0, Number(config.fade_out_seconds) || 0) * 1000;
        if (fadeInMs) factor = Math.min(factor, nowMs / fadeInMs);
        if (fadeOutMs) factor = Math.min(factor, Math.max(0, (durationMs - nowMs) / fadeOutMs));
        if (config.ducking_enabled) factor *= this.duckingFactor(config, nowMs, captions);
        this.audio.volume = Math.max(0, Math.min(1, this.baseVolume(config) * factor));
    }

    pause() {
        this.audio.pause();
    }

    play() {
        return this.audio.play();
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
        const beds = [];
        [...(captions || [])]
            .sort((left, right) => Number(left.start_ms) - Number(right.start_ms))
            .forEach(cue => {
                const start = Number(cue.start_ms), end = Number(cue.end_ms);
                if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
                const previous = beds.at(-1);
                if (previous && start - previous.end <= gap) previous.end = Math.max(previous.end, end);
                else beds.push({start, end});
            });
        for (const bed of beds) {
            if (nowMs < bed.start || nowMs > bed.end + hold + release) continue;
            if (nowMs < bed.start + attack) return 1 - (1 - duck) * ((nowMs - bed.start) / attack);
            if (nowMs <= bed.end + hold) return duck;
            return duck + (1 - duck) * ((nowMs - bed.end - hold) / release);
        }
        return 1;
    }
};
