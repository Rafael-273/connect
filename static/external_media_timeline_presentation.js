/* The timeline keeps its editorial nodes. This controller owns only their presentation. */
window.ConnectTimelinePresentation = function ({scroller, tracks, dialog, preferenceKey, onLayout}) {
    function read(key) {
        try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; }
    }
    function write(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* Private browsing. */ }
    }
    const modeKey = 'connect.timeline.presentation';
    const collapsedKey = `connect.timeline.collapsed.${preferenceKey}`;
    const storedRows = read(collapsedKey);
    const collapsed = new Set(Array.isArray(storedRows) ? storedRows : []);
    let compact = read(modeKey) === 'compact', focused = false, selectedTrack = null, opener;
    const modeButton = dialog.querySelector('[data-compact]');
    const detailedButton = dialog.querySelector('[data-detailed]');
    const focusButton = dialog.querySelector('[data-focus-track]');
    const trackControls = dialog.querySelector('[data-track-controls]');
    const visible = new Set(), observed = new Set(), displayed = new Set(), cache = new Map();
    let frame = 0, retryTimer, pending = null, enabled = true;
    const schedule = () => { if (!frame) frame = requestAnimationFrame(paint); };

    const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
        entries.forEach(({target, isIntersecting}) => {
            if (isIntersecting) visible.add(target);
            else visible.delete(target);
        });
        schedule();
    }, {root: scroller}) : null;

    function clearImage(clip) {
        clip.style.removeProperty('background-image');
        clip.classList.remove('has-thumbnail');
        displayed.delete(clip);
    }

    function fetchImage(url) {
        const image = new Image();
        const state = cache.get(url) || {attempts: 0};
        cache.set(url, state);
        pending = url;
        const finish = success => {
            clearTimeout(timeout);
            image.onload = image.onerror = null;
            if (!success) image.removeAttribute('src');
            state.ready = success;
            state.attempts++;
            state.retryAt = state.attempts >= 6 ? Infinity : Date.now() + Math.min(30000, 5000 * 2 ** (state.attempts - 1));
            pending = null;
            schedule();
        };
        const timeout = setTimeout(() => finish(false), 20000);
        image.onload = () => finish(true);
        image.onerror = () => finish(false);
        image.src = url;
    }

    function paint() {
        frame = 0;
        clearTimeout(retryTimer);
        const bounds = scroller.getBoundingClientRect(), now = Date.now();
        const candidates = [...visible].filter(clip => clip.isConnected && !clip.closest('.is-track-collapsed'))
            .map(clip => ({clip, box: clip.getBoundingClientRect()}))
            .sort((a, b) => a.box.left - b.box.left);
        const wanted = new Set(), lastBySource = new Map(), lastByLane = new Map(), labelEdges = new Map();
        let next = null, retryAt = Infinity;
        for (const {clip, box} of candidates) {
            const lane = clip.parentElement;
            const track = lane.parentElement;
            if (!labelEdges.has(track)) labelEdges.set(track, track.firstElementChild.getBoundingClientRect().right);
            const left = Math.max(box.left, bounds.left, labelEdges.get(track));
            const visibleWidth = Math.min(box.right, bounds.right) - left;
            const minimum = compact ? 130 : 82;
            if (visibleWidth < minimum - (displayed.has(clip) ? 8 : 0) || box.bottom <= bounds.top || box.top >= bounds.bottom) continue;
            const source = clip.dataset.filmstripSource, row = track.dataset.trackKey;
            const key = `${row}:${source}`, detailed = !compact && box.width >= 170;
            const previous = lastBySource.get(key);
            if (previous !== undefined && (!detailed || left - previous < 160)) continue;
            if (!detailed && left - (lastByLane.get(row) ?? -Infinity) < (compact ? 155 : 92)) continue;
            lastBySource.set(key, left);
            lastByLane.set(row, left);
            wanted.add(clip);
            const state = cache.get(source);
            if (state?.ready) {
                if (!displayed.has(clip)) {
                    clip.style.backgroundImage = `url(${JSON.stringify(source)})`;
                    clip.classList.add('has-thumbnail');
                    displayed.add(clip);
                }
            } else if (!state || state.retryAt <= now) next ||= source;
            else retryAt = Math.min(retryAt, state.retryAt);
        }
        for (const clip of displayed) if (!wanted.has(clip)) clearImage(clip);
        if (enabled && !pending && next) fetchImage(next);
        else if (enabled && !pending && Number.isFinite(retryAt)) retryTimer = setTimeout(schedule, Math.max(0, retryAt - now));
    }

    function refresh() {
        const clips = new Set(tracks.querySelectorAll('[data-filmstrip-source]'));
        for (const clip of observed) {
            if (clips.has(clip)) continue;
            observer?.unobserve(clip);
            visible.delete(clip);
            observed.delete(clip);
        }
        for (const clip of clips) {
            if (observed.has(clip)) continue;
            observed.add(clip);
            if (observer) observer.observe(clip);
            else visible.add(clip);
        }
        applyTrackState();
        schedule();
    }

    function applyTrackState() {
        scroller.classList.toggle('is-timeline-compact', compact);
        scroller.classList.toggle('has-track-focus', focused && Boolean(selectedTrack));
        for (const row of tracks.children) {
            const current = row.dataset.trackKey === selectedTrack;
            row.classList.toggle('is-track-focused', focused && current);
            row.classList.toggle('is-track-collapsed', collapsed.has(row.dataset.trackKey) && !(focused && current));
            row.querySelector('[data-collapse-track]')?.setAttribute('aria-expanded', String(!row.classList.contains('is-track-collapsed')));
        }
        modeButton.setAttribute('aria-pressed', String(compact));
        detailedButton.setAttribute('aria-pressed', String(!compact));
        focusButton.setAttribute('aria-pressed', String(focused));
        focusButton.disabled = !selectedTrack && !focused;
    }

    function updateLayout() {
        applyTrackState();
        schedule();
        onLayout();
    }
    function toggleTrack(row) {
        const key = row.dataset.trackKey;
        if (focused && key === selectedTrack) focused = false;
        if (row.classList.contains('is-track-collapsed')) collapsed.delete(key);
        else collapsed.add(key);
        write(collapsedKey, [...collapsed]);
        updateLayout();
    }

    function updateControls() {
        trackControls.replaceChildren(...[...tracks.children].map(row => {
            const button = document.createElement('button');
            const expanded = !row.classList.contains('is-track-collapsed');
            button.type = 'button';
            button.dataset.track = row.dataset.trackKey;
            button.setAttribute('aria-expanded', String(expanded));
            button.textContent = row.dataset.trackLabel;
            button.insertAdjacentHTML('beforeend', `<span>${expanded ? 'Recolher' : 'Expandir'}</span>`);
            button.onclick = () => { toggleTrack(row); updateControls(); trackControls.querySelector(`[data-track="${CSS.escape(button.dataset.track)}"]`)?.focus(); };
            return button;
        }));
    }

    dialog.addEventListener('close', () => opener?.focus({preventScroll:true}));
    dialog.querySelector('[data-close-display]').onclick = () => dialog.close();
    function setCompact(value) {
        compact = value;
        write(modeKey, compact ? 'compact' : 'detailed');
        updateLayout();
    }
    modeButton.onclick = () => setCompact(true);
    detailedButton.onclick = () => setCompact(false);
    focusButton.onclick = () => { focused = !focused; updateLayout(); updateControls(); };
    tracks.addEventListener('click', event => {
        const control = event.target.closest('[data-collapse-track]');
        if (control) toggleTrack(control.closest('.editor-track'));
    });
    scroller.addEventListener('scroll', schedule, {passive:true});

    return {
        refresh, schedule, clearImage,
        setResourcesEnabled(value) { enabled = value; if (value) schedule(); },
        select(key) {
            if (key === selectedTrack) return;
            selectedTrack = key;
            if (focused) updateLayout();
            else focusButton.disabled = !key;
        },
        open(trigger) {
            opener = trigger;
            applyTrackState();
            updateControls();
            dialog.showModal();
        },
    };
};
