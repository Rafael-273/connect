(function (root) {
    'use strict';
    const clamp = value => Math.max(0, Math.min(1, value));

    function edges(transitions, duration) {
        const entries = Object.entries(transitions || {}).filter(([, edge]) => edge.effects?.length && edge.duration_ms > 0);
        const total = entries.reduce((sum, [, edge]) => sum + edge.duration_ms, 0);
        const factor = total ? Math.max(0, Math.min(1, duration / total)) : 0;
        return entries.map(([side, edge]) => [side, edge, edge.duration_ms * factor]);
    }

    function ease(value, curve) {
        if (curve === 'ease_in') return value ** 2;
        if (curve === 'ease_out') return 1 - (1 - value) ** 2;
        if (curve === 'ease_in_out') return value ** 2 * (3 - 2 * value);
        return value;
    }

    function sample(transitions, elapsed, duration) {
        const state = {opacity: 1, blur: 0, scale: 1, slide: null, clip: '', mask: ''};
        for (const [side, edge, length] of edges(transitions, duration)) {
            if (!length) continue;
            const p = ease(clamp((side === 'in' ? elapsed : duration - elapsed) / length), edge.easing);
            if (p >= 1) continue;
            for (const effect of edge.effects) {
                if (effect.type === 'blur_fade') {
                    state.opacity *= p;
                    state.blur = effect.blur * (1 - p);
                } else if (effect.type === 'zoom') {
                    state.scale *= 1 + (effect.scale - 1) * (1 - p);
                } else if (effect.type === 'slide') {
                    state.slide = {...effect, amount: 1 - p};
                } else if (effect.type === 'wipe') {
                    if (effect.softness) {
                        const direction = {left: 'right', right: 'left', top: 'bottom', bottom: 'top'}[effect.direction];
                        const boundary = p * (100 + effect.softness);
                        state.mask = `linear-gradient(to ${direction}, black ${boundary-effect.softness}%, transparent ${boundary}%)`;
                    } else {
                        const insets = [0, 0, 0, 0];
                        insets[{left: 1, right: 3, top: 2, bottom: 0}[effect.direction]] = (1 - p) * 100;
                        state.clip = 'inset(' + insets.map(value => value + '%').join(' ') + ')';
                    }
                }
            }
        }
        return state;
    }

    function apply(node, item, elapsed, duration, base) {
        const state = sample(item.transitions, elapsed, duration);
        const stage = node.parentElement;
        let dx = 0, dy = 0;
        if (state.slide) {
            const {direction, mode, amount, distance} = state.slide;
            const horizontal = ['left', 'right'].includes(direction);
            const negative = ['left', 'top'].includes(direction);
            const frame = horizontal ? stage.clientWidth : stage.clientHeight;
            const center = frame * (horizontal ? base.centerX : base.centerY);
            const radians = (base.rotation || 0) * Math.PI / 180;
            const w = node.offsetWidth, h = node.offsetHeight;
            const extent = horizontal ? w * Math.abs(Math.cos(radians)) + h * Math.abs(Math.sin(radians))
                : h * Math.abs(Math.cos(radians)) + w * Math.abs(Math.sin(radians));
            const half = extent * base.scale * state.scale / 2;
            const delta = mode === 'outside' ? Math.max(0, negative ? center + half : frame - center + half)
                : frame * distance / 100;
            if (horizontal) dx = delta * amount * (negative ? -1 : 1);
            else dy = delta * amount * (negative ? -1 : 1);
        }
        node.style.transform = `translate(${dx}px,${dy}px) translate(${base.x},${base.y}) rotate(${base.rotation || 0}deg) scale(${base.scale * state.scale})`;
        node.style.opacity = String((base.opacity ?? 1) * state.opacity);
        node.style.filter = state.blur ? `blur(${state.blur * stage.clientWidth / 1920}px)` : '';
        node.style.clipPath = state.clip;
        node.style.maskImage = state.mask;
    }

    const api = {edges, sample, apply};
    if (typeof module !== 'undefined') module.exports = api;
    else root.ConnectVisualTransitions = api;
})(globalThis);
