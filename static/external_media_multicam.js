/* Camera decisions share the existing timeline clock and persistence queue. */
(function (root) {
    'use strict';
    const at = (segments, time) => (segments || []).find(item => item.start_ms <= time && time < item.end_ms);
    const sourceTime = (segment, time) => (segment.source_in_ms + (time - segment.start_ms) * segment.rate) / 1000;
    function create(options) {
        const {panel, stage, getTimeline, getTime, save, seek, editor: showEditor = true} = options;
        let mainVideo;
        const boundVideos = new WeakSet();
        const image = document.createElement('video');
        image.muted = true; image.playsInline = true; image.preload = 'metadata'; image.hidden = true;
        Object.assign(image.style, {position: 'absolute', inset: '0', width: '100%', height: '100%', zIndex: '2', pointerEvents: 'none', objectFit: 'cover'});
        stage.append(image);
        // The principal player uses the document-level temperature matrix. A
        // secondary camera needs its own matrix so a color adjustment does not
        // tint the primary image underneath it.
        const filterId = `preview-camera-grade-${Math.random().toString(36).slice(2)}`;
        const filterSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        filterSvg.setAttribute('aria-hidden', 'true'); filterSvg.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
        const filter = document.createElementNS(filterSvg.namespaceURI, 'filter'); filter.id = filterId;
        const matrix = document.createElementNS(filterSvg.namespaceURI, 'feColorMatrix'); matrix.setAttribute('type', 'matrix');
        filter.append(matrix); filterSvg.append(filter); stage.append(filterSvg);
        let signature = '', currentId = null, hasFrame = false, correctingSeek = false;
        const element = (tag, text, parent) => { const node = document.createElement(tag); if (text) node.textContent = text; if (parent) parent.append(node); return node; };
        const visualAdjustment = (timeline, segment) => {
            const operations = timeline.decisions || [], sourceId = String(segment.camera_source_id);
            const reframe = operations.filter(item => item.type === 'reframe' && item.enabled !== false && String(item.source_id || item.related_source_id) === sourceId);
            const scoped = reframe.filter(item => {
                const metadata = item.metadata || {};
                return Number(metadata.segment_start_ms) <= Number(segment.source_in_ms)
                    && Number(segment.source_in_ms) < Number(metadata.segment_end_ms);
            });
            const transform = [...(scoped.length ? scoped : reframe)].reverse().find(item => (item.metadata || {}).manual_transform)?.metadata?.manual_transform || {scale: 1, x: 0, y: 0};
            const color = [...operations].reverse().find(item => item.type === 'color_adjustment' && item.enabled !== false && String(item.source_id) === sourceId)?.metadata?.adjustment || {};
            return {transform, color};
        };
        const applyVisualAdjustment = (timeline, segment) => {
            const {transform, color} = visualAdjustment(timeline, segment), scale = Number(transform.scale || 1), x = Number(transform.x || 0), y = Number(transform.y || 0);
            const shift = Math.max(0, (scale - 1) * 50), temperature = Math.max(-1, Math.min(1, Number(color.temperature || 0) / 100));
            const red = Math.max(-1, Math.min(1, (Number(color.red || 0) + Number(color.yellow || 0) + Number(color.magenta || 0)) / 300 + temperature));
            const green = Math.max(-1, Math.min(1, (Number(color.green || 0) + Number(color.yellow || 0) + Number(color.cyan || 0)) / 300));
            const blue = Math.max(-1, Math.min(1, (Number(color.blue || 0) + Number(color.cyan || 0) + Number(color.magenta || 0)) / 300 - temperature));
            matrix.setAttribute('values', `${1 + red * .28} 0 0 0 0 0 ${1 + green * .28} 0 0 0 0 0 ${1 + blue * .28} 0 0 0 0 0 1 0`);
            image.style.objectPosition = `${50 - x * 50}% ${50 - y * 50}%`;
            image.style.transform = `translate(${x * shift}%, ${y * shift}%) scale(${scale})`;
            image.style.filter = `url(#${filterId}) brightness(${1 + Number(color.brightness || 0) / 200}) contrast(${Number(color.contrast ?? 100) / 100}) saturate(${Number(color.saturation ?? 100) / 100})`;
        };
        async function submit(payload, button) {
            button.disabled = true;
            try { await save(payload); render(); }
            catch (error) { status.textContent = error.message; }
            finally { button.disabled = false; }
        }
        const status = element('p', '', panel); status.setAttribute('role', 'status');
        status.className = 'multicam-status';
        const body = element('div', '', panel);
        body.className = 'multicam-body';
        function editor(segment) {
            const timeline = getTimeline();
            const container = segment ? body : element('details', '', body);
            if (!segment) {
                container.className = 'multicam-new';
                const summary = element('summary', 'Adicionar alternância de câmera', container);
                summary.className = 'multicam-new__summary';
            }
            const form = element('form', '', container);
            form.className = `multicam-card${segment ? '' : ' multicam-card--new'}`;
            if (segment) form.dataset.cameraSelectionId = segment.id;
            const heading = element('div', '', form); heading.className = 'multicam-card__heading';
            const icon = element('i', '', heading); icon.className = `fas ${segment ? 'fa-camera' : 'fa-plus'}`;
            const headingCopy = element('div', '', heading);
            element('strong', segment ? 'Alternância de câmera' : 'Nova alternância', headingCopy);
            element('small', segment ? 'Defina quando a câmera extra deve aparecer.' : 'Escolha a câmera e o intervalo para inseri-la na timeline.', headingCopy);
            const fields = element('div', '', form); fields.className = 'multicam-fields';
            const available = (timeline.cameras || []).filter(camera => !(camera.is_primary ?? (camera.id === camera.reference_source_id)));
            const cameraField = element('label', 'Câmera', fields); cameraField.className = 'multicam-field';
            const choice = element('select', '', cameraField); choice.setAttribute('aria-label', 'Câmera');
            available.forEach(camera => { const option = element('option', camera.label, choice); option.value = camera.id; });
            if (segment) choice.value = segment.camera_source_id;
            const input = (label, value, step) => { const wrapper = element('label', label, fields), node = element('input', '', wrapper); wrapper.className = 'multicam-field'; node.type = 'number'; node.value = String(value); node.step = step; node.required = true; return node; };
            const start = input('Início (s)', ((segment?.start_ms ?? getTime()) / 1000).toFixed(3), '.001');
            const end = input('Fim (s)', ((segment?.end_ms ?? Math.min(getTime() + 6000, timeline.sequence.duration_ms)) / 1000).toFixed(3), '.001');
            const alignmentFor = () => (timeline.cameras || []).find(camera => camera.id === choice.value)?.alignment;
            const advanced = element('details', '', form); advanced.className = 'multicam-advanced';
            const advancedSummary = element('summary', 'Ajustar sincronização manualmente', advanced);
            const advancedFields = element('div', '', advanced); advancedFields.className = 'multicam-fields multicam-fields--advanced';
            const advancedInput = (label, value, step) => { const wrapper = element('label', label, advancedFields), node = element('input', '', wrapper); wrapper.className = 'multicam-field'; node.type = 'number'; node.value = String(value); node.step = step; node.required = true; return node; };
            const offset = advancedInput('Deslocamento (ms)', alignmentFor()?.offset_ms ?? 0, '1');
            const drift = advancedInput('Drift (ppm)', Math.round((alignmentFor()?.rate ?? 1) * 1000000 - 1000000), '1'); drift.min = '-1000'; drift.max = '1000';
            let manual = Boolean(segment && (timeline.decisions || []).find(item => item.id === segment.id)?.alignment?.status === 'MANUAL');
            const hint = element('small', '', form); hint.className = 'multicam-alignment';
            const describe = () => { const alignment = alignmentFor(), operation = (timeline.decisions || []).find(item => item.id === segment?.id); if (operation?.alignment?.status === 'MANUAL' && operation.camera_source_id === choice.value) { hint.textContent = 'Alinhamento manual aplicado.'; return; } hint.textContent = alignment?.status === 'ALIGNED' ? `Confiança acústica: ${Math.round(alignment.confidence * 100)}% · validada até ${(alignment.validated_until_primary_ms / 1000).toFixed(1)} s da gravação principal` : 'Sincronização requer ajuste manual.'; };
            describe();
            if (segment) {
                const operation = (timeline.decisions || []).find(item => item.id === segment.id);
                if (operation?.reason) element('small', `${operation.origin === 'AI' ? 'Sugestão da IA: ' : ''}${operation.reason}`, form);
                if (operation?.alignment) { offset.value = operation.alignment.offset_ms; drift.value = Math.round((operation.alignment.rate - 1) * 1000000); }
            }
            offset.oninput = drift.oninput = () => { manual = true; };
            choice.onchange = () => { manual = false; offset.value = alignmentFor()?.offset_ms ?? 0; drift.value = Math.round(((alignmentFor()?.rate ?? 1) - 1) * 1000000); describe(); };
            const actions = element('div', '', form); actions.className = 'multicam-actions';
            const button = element('button', segment ? 'Salvar alterações' : 'Adicionar alternância', actions);
            button.type = 'submit'; button.className = 'multicam-button multicam-button--primary';
            form.onsubmit = event => { event.preventDefault(); const payload = {camera_source_id: choice.value, start_ms: Math.round(Number(start.value) * 1000), end_ms: Math.round(Number(end.value) * 1000)}; if (segment) payload.id = segment.id; if (manual) { payload.offset_ms = Number(offset.value); payload.rate = 1 + Number(drift.value) / 1000000; } submit(payload, button); };
            if (segment) {
                const jump = element('button', 'Ver na timeline', actions); jump.type = 'button'; jump.className = 'multicam-button'; jump.onclick = () => seek(segment.start_ms);
                const remove = element('button', 'Remover alternância', actions); remove.type = 'button'; remove.className = 'multicam-button multicam-button--danger'; remove.onclick = () => submit({id: segment.id, delete: true}, remove);
            }
        }
        function render() {
            const timeline = getTimeline(); panel.hidden = !showEditor || !(timeline.cameras || []).length;
            if (!showEditor) return;
            const next = JSON.stringify([timeline.camera_segments, timeline.cameras, timeline.edit_decision_set]);
            if (next === signature) return;
            signature = next; body.replaceChildren(); status.textContent = '';
            const selections = new Map();
            (timeline.camera_segments || []).forEach(item => {
                const previous = selections.get(item.id);
                selections.set(item.id, previous ? {...previous, start_ms: Math.min(previous.start_ms, item.start_ms), end_ms: Math.max(previous.end_ms, item.end_ms)} : item);
            });
            selections.forEach(editor);
            editor(null);
        }
        function sync() {
            const timeline = getTimeline(), time = getTime();
            const baked = timeline.review_master_is_edited && (timeline.review_rendered_layers || []).includes('MULTICAM');
            const segment = baked ? null : at(timeline.camera_segments, time);
            if (!segment) {
                image.hidden = true;
                image.pause();
                currentId = null;
                hasFrame = false;
                correctingSeek = false;
                return;
            }
            const camera = timeline.cameras.find(item => item.id === segment.camera_source_id);
            if (!camera?.url) { image.hidden = true; return; }
            applyVisualAdjustment(timeline, segment);
            const id = `${segment.id}:${segment.start_ms}:${camera.url}`;
            if (currentId !== id) {
                image.hidden = true;
                hasFrame = false;
                correctingSeek = false;
                image.src = camera.url;
                currentId = id;
                image.load();
            }
            const target = sourceTime(segment, time);
            // Seeking a video element on every playback tick makes some browsers
            // briefly reveal the principal track. Correct only meaningful drift and
            // wait for the first decoded frame before displaying the camera.
            if (image.readyState >= 1 && !correctingSeek && Math.abs(image.currentTime - target) > .25) {
                correctingSeek = true;
                image.currentTime = target;
            }
            if (!hasFrame) return;
            image.hidden = false;
            image.playbackRate = mainVideo.playbackRate * segment.rate;
            if (mainVideo.paused || mainVideo.seeking) image.pause();
            else if (image.paused) image.play().catch(() => {});
        }
        function setMainVideo(video) {
            mainVideo = video;
            if (boundVideos.has(video)) return;
            boundVideos.add(video);
            video.addEventListener('pause', () => { if (video === mainVideo) image.pause(); });
        }
        setMainVideo(options.mainVideo);
        image.addEventListener('loadedmetadata', sync);
        image.addEventListener('loadeddata', sync);
        image.addEventListener('seeked', () => { correctingSeek = false; hasFrame = true; sync(); });
        image.addEventListener('error', () => { image.hidden = true; status.textContent = 'Câmera indisponível no preview. Aguarde o proxy ou tente novamente.'; });
        render();
        function focusSelection(selectionId) {
            render();
            panel.open = true;
            const form = body.querySelector(`[data-camera-selection-id="${CSS.escape(String(selectionId))}"]`);
            if (form) {
                form.scrollIntoView({block: 'center', behavior: 'smooth'});
                form.querySelector('input, select, button')?.focus({preventScroll: true});
            } else panel.scrollIntoView({block: 'start', behavior: 'smooth'});
        }
        return {render, sync, focusSelection, setMainVideo};
    }
    const api = {at, sourceTime, create};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.ConnectMulticam = api;
})(typeof window !== 'undefined' ? window : globalThis);
