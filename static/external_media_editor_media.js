(function (root) {
    'use strict';

    function bind({dialog, triggers, endpoint, uploadUrl, presignUrl, directUpload, csrf,
        canAddVisual, openVisual, insert}) {
        const list = dialog.querySelector('[data-media-items]');
        const status = dialog.querySelector('[data-media-status]');
        const upload = dialog.querySelector('[data-media-upload]');
        const visual = dialog.querySelector('[data-library-visual]');
        const rows = new Map();
        let timer;
        let generation = 0;

        async function request(url, options) {
            const response = await fetch(url, {credentials: 'same-origin', ...options});
            if (response.redirected || response.status === 401 || response.status === 403) {
                throw new Error('Sua sessão pode ter expirado. Atualize a página e entre novamente.');
            }
            let data;
            try {
                data = await response.json();
            } catch (_) {
                throw new Error('Não foi possível carregar a biblioteca de mídia. Atualize a página e tente novamente.');
            }
            if (!response.ok) throw new Error(data.detail || data.error || 'Não foi possível carregar a mídia.');
            return data;
        }

        function makeRow(item) {
            const row = document.createElement('form');
            row.className = 'editor-media-item';
            const name = document.createElement('strong');
            name.textContent = item.name;
            const message = document.createElement('small');
            const button = document.createElement('button');
            button.type = 'submit';
            button.className = 'media-btn media-primary';
            button.textContent = 'Adicionar à timeline';
            row.append(name, message, button);
            row.onsubmit = async event => {
                event.preventDefault();
                button.disabled = true;
                try {
                    await insert(item);
                    ++generation;
                    clearTimeout(timer);
                    row.remove();
                    rows.delete(item.id);
                    status.textContent = 'Vídeo adicionado à timeline.';
                    if (dialog.open) refresh();
                } catch (error) {
                    status.textContent = error.message;
                } finally {
                    button.disabled = false;
                }
            };
            list.append(row);
            return {row, message, button};
        }

        async function refresh() {
            const requestGeneration = ++generation;
            clearTimeout(timer);
            try {
                const {items} = await request(endpoint, {headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}});
                if (!dialog.open || requestGeneration !== generation) return;
                visual.disabled = !canAddVisual();
                for (const item of items) {
                    if (item.in_timeline) {
                        rows.get(item.id)?.row.remove();
                        rows.delete(item.id);
                        continue;
                    }
                    if (!rows.has(item.id)) rows.set(item.id, makeRow(item));
                    const {message, button} = rows.get(item.id);
                    const waitingCaptions = ['pending', 'running'].includes(item.captions);
                    button.disabled = item.status !== 'READY' || !item.duration_ms || waitingCaptions;
                    message.textContent = item.status === 'ERROR' ? item.error || 'Falha ao preparar o vídeo.'
                        : item.status !== 'READY' ? 'Preparando vídeo…'
                        : waitingCaptions ? 'Preparando legendas…'
                        : ['error', 'unavailable'].includes(item.captions) ? 'Legendas indisponíveis para este vídeo.'
                        : '';
                }
                if (items.some(item => !item.in_timeline && (item.status === 'PENDING' || ['pending', 'running'].includes(item.captions)))) {
                    timer = setTimeout(refresh, 2500);
                }
            } catch (error) {
                if (requestGeneration === generation && dialog.open) status.textContent = error.message;
            }
        }

        for (const trigger of triggers) trigger.addEventListener('click', () => {
            rows.clear();
            list.replaceChildren();
            status.textContent = '';
            visual.disabled = !canAddVisual();
            dialog.showModal();
            refresh();
        });
        dialog.addEventListener('close', () => { ++generation; clearTimeout(timer); });
        dialog.addEventListener('click', event => {
            if (event.target !== dialog) return;
            const box = dialog.getBoundingClientRect();
            if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) {
                dialog.close();
            }
        });
        visual.onclick = () => { dialog.close(); openVisual(); };
        upload.onchange = async () => {
            const files = [...upload.files];
            upload.disabled = true;
            const headers = {'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest'};
            const failures = [];
            for (const file of files) {
                status.textContent = `Enviando ${file.name}…`;
                try {
                    if (directUpload) {
                        const presign = await request(presignUrl, {
                            method: 'POST', headers: {...headers, 'Content-Type': 'application/json'},
                            body: JSON.stringify({filename: file.name, size: file.size, content_type: file.type}),
                        });
                        await root.ConnectS3Upload.upload(file, presign, {csrfToken: csrf,
                            onProgress: percent => { status.textContent = `Enviando ${file.name} (${percent}%)`; },
                        });
                        await root.ConnectS3Upload.complete(presign, csrf);
                    } else {
                        const body = new FormData();
                        body.append('file', file);
                        await request(uploadUrl, {method: 'POST', headers, body});
                    }
                } catch (error) {
                    failures.push(`${file.name} — ${error.message}`);
                }
                if (dialog.open) refresh();
            }
            status.textContent = failures.join(' · ') || 'Envio concluído.';
            upload.value = '';
            upload.disabled = false;
        };
    }

    root.ConnectEditorMedia = {bind};
})(window);
