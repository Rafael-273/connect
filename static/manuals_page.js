document.addEventListener('DOMContentLoaded', () => {
    const app = document.getElementById('manual-app');
    const dialog = document.getElementById('manual-dialog');
    const discardDialog = document.getElementById('manual-discard-dialog');
    const viewDialog = document.getElementById('manual-view-dialog');
    const filter = app?.querySelector('.manuals-filter');
    if (filter) {
        document.addEventListener('click', event => {
            if (filter.open && !filter.contains(event.target)) filter.open = false;
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && filter.open) {
                filter.open = false;
                filter.querySelector('summary').focus();
            }
        });
    }
    if (!app) return;
    let viewRequest = 0;
    if (viewDialog) {
        const title = viewDialog.querySelector('#manual-view-title');
        const team = viewDialog.querySelector('[data-view-team]');
        const status = viewDialog.querySelector('[data-view-status]');
        const summary = viewDialog.querySelector('[data-view-summary]');
        const content = viewDialog.querySelector('[data-view-content]');
        async function showManual(link) {
            const request = ++viewRequest;
            title.textContent = 'Visualizar manual'; team.textContent = '';
            summary.textContent = ''; summary.hidden = true; content.textContent = '';
            status.textContent = 'Carregando manual…';
            if (!viewDialog.open) viewDialog.showModal();
            try {
                const response = await fetch(link.href, {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'});
                if (!response.ok) throw new Error('Não foi possível carregar o manual.');
                const data = await response.json();
                if (request !== viewRequest || !viewDialog.open) return;
                title.textContent = data.title;
                team.textContent = `${data.team} · ${data.status}`;
                if (data.summary) { summary.textContent = data.summary; summary.hidden = false; }
                content.innerHTML = data.html; // HTML sanitizado no servidor.
                status.textContent = '';
            } catch (_) {
                if (request === viewRequest && viewDialog.open) status.textContent = 'Não foi possível carregar o manual. Tente novamente.';
            }
        }
        app.querySelectorAll('[data-view-manual]').forEach(link => link.addEventListener('click', event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); showManual(link);
        }));
        viewDialog.querySelectorAll('[data-close-view]').forEach(button => button.addEventListener('click', () => viewDialog.close()));
        if (/^\d+$/.test(app.dataset.selected || '')) {
            const selected = app.querySelector(`[data-view-manual="${app.dataset.selected}"]`);
            if (selected) showManual(selected);
        }
    }
    if (!dialog) return;
    const deleteDialog = document.getElementById('manual-delete-dialog');
    if (deleteDialog) {
        const deleteForm = deleteDialog.querySelector('[data-delete-form]');
        app.querySelectorAll('[data-delete-manual]').forEach(button => button.addEventListener('click', () => {
            const id = button.dataset.deleteManual;
            if (!/^\d+$/.test(id)) return;
            deleteDialog.querySelector('[data-delete-title]').textContent = button.dataset.manualTitle;
            deleteForm.action = app.dataset.deleteUrl.replace('/0/', `/${id}/`);
            deleteDialog.showModal();
        }));
        deleteDialog.querySelector('[data-cancel-delete]').addEventListener('click', () => deleteDialog.close());
    }
    const form = document.getElementById('manual-form');
    const audioForm = document.getElementById('manual-audio-form');
    const createToggle = document.querySelector('[data-new-manual]');
    const createMenu = document.getElementById('manual-create-menu');
    const rich = document.getElementById('manual-rich');
    const source = form.elements.content;
    const csrf = form.elements.csrfmiddlewaretoken.value;
    const formatStatus = form.querySelector('[data-format-status]');
    const error = form.querySelector('[data-form-error]');
    const editUrl = id => app.dataset.editUrl.replace('/0/', `/${id}/`);
    const statusUrl = id => app.dataset.statusUrl.replace('/0/', `/${id}/`);
    let dirty = false;
    let savedRange = null;
    let editorVersion = 0;
    let formatHistory = null;
    source.hidden = true;
    source.required = false; // The server validates the synchronized rich-text value.
    function display(which) {
        form.hidden = which !== 'form';
        audioForm.hidden = which !== 'audio';
    }
    function open() { if (!dialog.open) dialog.showModal(); }
    function reset() {
        form.reset(); rich.innerHTML = ''; formatStatus.textContent = ''; error.textContent = '';
        form.action = app.dataset.createUrl; dirty = false; savedRange = null; formatHistory = null; editorVersion += 1;
    }
    async function renderContent(value) {
        const body = new FormData(); body.append('content', value); body.append('csrfmiddlewaretoken', csrf);
        const response = await fetch(app.dataset.previewUrl, {method: 'POST', body, credentials: 'same-origin'});
        if (!response.ok) throw new Error('Não foi possível carregar o conteúdo.');
        return (await response.json()).html;
    }
    async function edit(id) {
        try {
            const response = await fetch(editUrl(id), {headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'});
            if (!response.ok) throw new Error('Não foi possível abrir o manual.');
            const data = await response.json();
            const html = await renderContent(data.content);
            reset(); form.action = editUrl(id);
            form.elements.title.value = data.title;
            form.elements.sub_team.value = data.sub_team || '';
            rich.innerHTML = html;
            document.getElementById('manual-dialog-title').textContent = 'Editar manual';
            display('form'); open();
        } catch (e) { window.alert(e.message); }
    }
    function setCreateMenu(visible) {
        createMenu.hidden = !visible;
        createToggle.setAttribute('aria-expanded', String(visible));
    }
    createToggle.addEventListener('click', () => setCreateMenu(createMenu.hidden));
    document.addEventListener('click', event => {
        if (!createMenu.hidden && !createMenu.contains(event.target) && !createToggle.contains(event.target)) setCreateMenu(false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !createMenu.hidden) {
            setCreateMenu(false);
            createToggle.focus();
        }
    });
    document.querySelector('[data-manual-choice]').addEventListener('click', () => {
        setCreateMenu(false); reset();
        document.getElementById('manual-dialog-title').textContent = 'Novo manual';
        display('form'); open(); rich.focus();
    });
    document.querySelector('[data-ai-choice]').addEventListener('click', () => {
        setCreateMenu(false); reset();
        document.getElementById('manual-dialog-title').textContent = 'Gerar manual com IA';
        display('audio'); open();
    });
    document.querySelectorAll('[data-edit-manual]').forEach(button => button.addEventListener('click', () => edit(button.dataset.editManual)));
    dialog.querySelector('[data-back-choice]').addEventListener('click', event => {
        event.stopPropagation();
        dialog.close(); setCreateMenu(true); createToggle.focus();
    });
    function requestClose() {
        if (!dirty) {
            dialog.close();
        } else if (!discardDialog.open) {
            discardDialog.showModal();
        }
    }
    discardDialog.querySelector('form').addEventListener('submit', event => {
        event.preventDefault();
        const discard = event.submitter && event.submitter.value === 'discard';
        discardDialog.close();
        if (discard) {
            dirty = false;
            dialog.close();
        }
    });
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', requestClose));
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        requestClose();
    });
    form.addEventListener('input', () => { dirty = true; });
    rich.addEventListener('input', () => { dirty = true; });
    rich.addEventListener('paste', event => {
        event.preventDefault();
        document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
    });
    document.addEventListener('selectionchange', () => {
        const selection = window.getSelection();
        if (selection.rangeCount && rich.contains(selection.anchorNode) && rich.contains(selection.focusNode)) {
            savedRange = selection.getRangeAt(0).cloneRange();
        }
    });
    function restoreSelection() {
        rich.focus();
        if (savedRange && rich.contains(savedRange.commonAncestorContainer)) {
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(savedRange);
        }
    }
    dialog.querySelector('[data-heading]').addEventListener('change', event => {
        if (!event.target.value) return;
        restoreSelection();
        document.execCommand('formatBlock', false, event.target.value);
        event.target.value = '';
        dirty = true;
    });
    dialog.querySelectorAll('[data-cmd]').forEach(button => button.addEventListener('mousedown', event => event.preventDefault()));
    function useFormatHistory(command) {
        if (!formatHistory) return false;
        if (command === 'undo' && formatHistory.applied && rich.innerHTML === formatHistory.after) {
            rich.innerHTML = formatHistory.before;
            formatHistory.applied = false;
        } else if (command === 'redo' && !formatHistory.applied && rich.innerHTML === formatHistory.before) {
            rich.innerHTML = formatHistory.after;
            formatHistory.applied = true;
        } else {
            return false;
        }
        savedRange = null;
        rich.focus();
        dirty = true;
        return true;
    }
    dialog.querySelectorAll('[data-cmd]').forEach(button => button.addEventListener('click', () => {
        if (useFormatHistory(button.dataset.cmd)) return;
        restoreSelection(); document.execCommand(button.dataset.cmd, false, button.dataset.value || null); dirty = true;
    }));
    rich.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        const key = event.key.toLowerCase();
        const command = key === 'z' ? (event.shiftKey ? 'redo' : 'undo') : (key === 'y' ? 'redo' : '');
        if (command && useFormatHistory(command)) event.preventDefault();
    });
    dialog.querySelectorAll('[data-insert]').forEach(button => {
        button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => {
            restoreSelection(); const type = button.dataset.insert;
            if (type === 'link' || type === 'image') {
                const url = window.prompt('Informe o endereço HTTPS ou HTTP:');
                if (!url || !/^https?:\/\//i.test(url)) return;
                restoreSelection();
                document.execCommand(type === 'link' ? 'createLink' : 'insertImage', false, url);
            } else if (type === 'table') {
                document.execCommand('insertHTML', false, '<table><thead><tr><th>Coluna 1</th><th>Coluna 2</th></tr></thead><tbody><tr><td>Conteúdo</td><td>Conteúdo</td></tr></tbody></table><p><br></p>');
            } else document.execCommand('insertHTML', false, '<p>☐ Item do checklist</p>');
            dirty = true;
        });
    });
    dialog.querySelector('[data-ai-format]').addEventListener('click', async event => {
        const button = event.currentTarget;
        const original = rich.innerHTML;
        const version = editorVersion;
        if (!rich.textContent.trim()) {
            formatStatus.textContent = 'Escreva algum texto antes de formatar com IA.';
            rich.focus();
            return;
        }
        button.disabled = true;
        const buttonText = button.innerHTML;
        button.textContent = 'Formatando…';
        formatStatus.textContent = 'Organizando a formatação com IA…';
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 90000);
        try {
            const body = new FormData();
            body.append('content', original);
            body.append('csrfmiddlewaretoken', csrf);
            const response = await fetch(app.dataset.formatUrl, {
                method: 'POST', body, credentials: 'same-origin', signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Não foi possível formatar com IA.');
            if (version !== editorVersion || !dialog.open || form.hidden) return;
            if (rich.innerHTML !== original) {
                formatStatus.textContent = 'O texto foi alterado durante a formatação. Suas alterações foram mantidas; tente novamente.';
                return;
            }
            if (data.changed === false) {
                formatStatus.textContent = 'A IA não identificou alterações de formatação. Você pode usar as ferramentas para ajustar o texto.';
                return;
            }
            // Replacing the whole document via insertHTML can nest paragraphs
            // inside the previous heading and make every paragraph look bold.
            // The server has already sanitized the returned HTML.
            rich.innerHTML = data.html;
            formatHistory = {before: original, after: rich.innerHTML, applied: true};
            rich.focus();
            savedRange = null;
            dirty = true;
            formatStatus.textContent = 'Formatação aplicada. Revise o conteúdo antes de salvar.';
        } catch (e) {
            formatStatus.textContent = e.name === 'AbortError'
                ? 'A formatação demorou demais. Seu conteúdo foi preservado; tente novamente.'
                : (e.message || 'Não foi possível formatar com IA. Seu conteúdo foi preservado.');
        } finally {
            window.clearTimeout(timer);
            button.disabled = false;
            button.innerHTML = buttonText;
        }
    });
    form.addEventListener('submit', async event => {
        event.preventDefault(); error.textContent = ''; source.value = rich.innerHTML;
        const body = new FormData(form); body.set('publication', event.submitter?.value || 'draft');
        const button = event.submitter; if (button) button.disabled = true;
        try {
            const response = await fetch(form.action, {method: 'POST', body, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
            const result = await response.json();
            if (!response.ok) throw new Error(Object.values(result.errors || {}).flat().map(item => item.message).join(' ') || 'Não foi possível salvar.');
            dirty = false; window.location.assign(result.url);
        } catch (e) { error.textContent = e.message || 'Falha de conexão. Seu conteúdo foi preservado.'; }
        finally { if (button) button.disabled = false; }
    });
    const labels = {waiting: 'Aguardando processamento…', transcribing: 'Transcrevendo treinamento…', generating: 'Organizando conteúdo…', completed: 'Manual pronto para revisão.', error: 'Não foi possível processar o áudio.'};
    async function poll(id, status, retry, autoOpen, progress = null) {
        try {
            const response = await fetch(statusUrl(id), {credentials: 'same-origin'});
            if (!response.ok) throw new Error();
            const data = await response.json(); status.textContent = data.error || labels[data.status] || 'Aguardando processamento…';
            if (retry && data.retryable) retry.hidden = false;
            if (data.status === 'completed') {
                if (progress) progress.complete();
                if (dirty) { status.textContent = 'Manual pronto e salvo como rascunho. Atualize a lista após salvar suas alterações.'; return; }
                if (autoOpen && data.manual_id) await edit(data.manual_id); else window.location.reload();
                return;
            }
            if (data.status === 'error') {
                if (progress) progress.fail(data.error || labels.error, 'job', id);
                else if (retry) retry.hidden = false;
                else status.textContent += ' Atualize a página para tentar novamente sem reenviar o arquivo.';
                return;
            }
            if (progress && data.retryable) {
                progress.fail('O processamento demorou mais que o esperado. Você pode tentar novamente sem reenviar o áudio.', 'job', id);
                return;
            }
            if (progress) progress.stage(data.status);
            window.setTimeout(() => poll(id, status, retry, autoOpen, progress), 2500);
        } catch (_) {
            status.textContent = 'Falha de conexão ao consultar a geração.';
            if (progress) progress.fail('Não foi possível consultar o processamento. Tente novamente para acompanhar o mesmo envio.', 'status', id);
        }
    }
    document.querySelectorAll('[data-job]').forEach(row => {
        const status = row.querySelector('[data-job-status]'), retry = row.querySelector('[data-job-retry]');
        retry.addEventListener('click', async () => {
            retry.disabled = true;
            try {
                const response = await fetch(statusUrl(row.dataset.job), {method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'});
                const data = await response.json(); if (!response.ok || data.status === 'error') throw new Error(data.error);
                retry.hidden = true; poll(row.dataset.job, status, retry, true);
            } catch (e) { status.textContent = e.message || 'Não foi possível iniciar.'; } finally { retry.disabled = false; }
        });
        poll(row.dataset.job, status, retry, false);
    });
    const audioInput = audioForm.elements.audio;
    const audioStatus = audioForm.querySelector('[data-audio-status]');
    const audioSubmit = audioForm.querySelector('[type=submit]');
    const progressDialog = document.getElementById('manual-progress-dialog');
    const progressTitle = progressDialog.querySelector('[data-progress-title]');
    const progressDescription = progressDialog.querySelector('[data-progress-description]');
    const progressActions = progressDialog.querySelector('[data-progress-actions]');
    const progressRetry = progressDialog.querySelector('[data-progress-retry]');
    const progressSteps = Array.from(progressDialog.querySelectorAll('[data-progress-step]'));
    let currentAudioJobId = null;
    let progressFailed = false;
    let progressRetryMode = null;
    let progressJobId = null;
    const stageDetails = {
        upload: ['Enviando áudio…', 'Aguarde enquanto o arquivo é enviado.'],
        waiting: ['Aguardando processamento…', 'O áudio foi enviado e está na fila.'],
        transcribing: ['Transcrevendo treinamento…', 'Estamos convertendo o áudio em texto. Esta etapa pode levar alguns minutos.'],
        generating: ['Organizando conteúdo…', 'A IA está preparando o manual para sua revisão.'],
        completed: ['Manual pronto para revisão', 'Abrindo o editor com o conteúdo gerado.'],
    };
    const progress = {
        stage(state) {
            const [title, description] = stageDetails[state] || stageDetails.waiting;
            progressTitle.textContent = title;
            progressDescription.textContent = description;
            const active = {upload: 0, waiting: 1, transcribing: 1, generating: 2, completed: 3}[state] ?? 1;
            progressSteps.forEach((step, index) => {
                step.classList.toggle('is-done', index < active);
                step.classList.toggle('is-current', index === active);
                if (index === active) step.setAttribute('aria-current', 'step');
                else step.removeAttribute('aria-current');
            });
        },
        begin(state) {
            progressFailed = false;
            progressDialog.classList.remove('is-error');
            progressActions.hidden = true;
            progressRetry.disabled = false;
            this.stage(state);
            if (!progressDialog.open) progressDialog.showModal();
        },
        fail(message, mode, id) {
            progressFailed = true;
            progressRetryMode = mode;
            progressJobId = id || null;
            if (id) currentAudioJobId = id;
            progressDialog.classList.add('is-error');
            progressTitle.textContent = 'Não foi possível concluir';
            progressDescription.textContent = message;
            progressRetry.hidden = !mode;
            progressRetry.disabled = false;
            progressActions.hidden = false;
        },
        complete() {
            this.stage('completed');
            currentAudioJobId = null;
            progressFailed = false;
            progressDialog.close();
        },
    };
    progressDialog.addEventListener('cancel', event => {
        event.preventDefault();
        if (progressFailed) progressDialog.close();
    });
    progressDialog.addEventListener('close', () => {
        audioSubmit.disabled = false;
        if (progressFailed) audioStatus.textContent = currentAudioJobId
            ? 'Você pode tentar novamente sem reenviar o áudio.' : progressDescription.textContent;
    });
    progressDialog.querySelector('[data-progress-dismiss]').addEventListener('click', () => progressDialog.close());
    progressRetry.addEventListener('click', async () => {
        const id = progressJobId;
        const mode = progressRetryMode;
        if (!id) return;
        progress.begin('waiting');
        if (mode === 'status') { poll(id, audioStatus, null, true, progress); return; }
        try {
            const response = await fetch(statusUrl(id), {method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'});
            const data = await response.json();
            if (response.status === 409) { poll(id, audioStatus, null, true, progress); return; }
            if (!response.ok || data.status === 'error') {
                progress.fail(data.error || 'Não foi possível reiniciar o processamento.', 'job', id);
                return;
            }
            poll(id, audioStatus, null, true, progress);
        } catch (_) {
            progress.fail('Não foi possível confirmar a retomada. Tente novamente para verificar o processamento.', 'status', id);
        }
    });
    function audioError() {
        const file = audioInput.files[0];
        const maxMB = Number(app.dataset.audioMaxMb);
        if (!file) return 'Selecione um arquivo de áudio.';
        if (!file.size) return 'O arquivo de áudio está vazio.';
        if (file.size > maxMB * 1024 * 1024) return `O arquivo tem ${(file.size / (1024 * 1024)).toFixed(1)} MB e ultrapassa o limite de ${maxMB} MB.`;
        const extension = '.' + file.name.split('.').pop().toLowerCase();
        if (!audioInput.accept.split(',').includes(extension)) return 'Formato não aceito. Use MP3, MP4, MPEG, MPGA, M4A, WAV ou WebM.';
        return '';
    }
    audioInput.addEventListener('change', () => { currentAudioJobId = null; audioStatus.textContent = audioError(); });
    audioForm.addEventListener('submit', async event => {
        event.preventDefault(); const status = audioForm.querySelector('[data-audio-status]'); const button = audioForm.querySelector('[type=submit]');
        const validationError = audioError();
        if (validationError) { status.textContent = validationError; return; }
        button.disabled = true;
        if (currentAudioJobId) {
            progress.begin('waiting');
            poll(currentAudioJobId, status, null, true, progress);
            return;
        }
        status.textContent = 'Enviando áudio…';
        progress.begin('upload');
        try {
            const response = await fetch(app.dataset.generateUrl, {method: 'POST', body: new FormData(audioForm), credentials: 'same-origin'});
            const data = await response.json();
            if (!response.ok) { progress.fail(data.error || 'Não foi possível enviar o áudio.', null, null); return; }
            currentAudioJobId = data.id;
            if (data.status === 'error') { progress.fail(data.error || 'Não foi possível iniciar o processamento.', 'job', data.id); return; }
            progress.stage('waiting');
            poll(data.id, status, null, true, progress);
        } catch (_) {
            progress.fail('Não foi possível confirmar o envio. Verifique a lista de gerações antes de reenviar o arquivo.', null, null);
        }
    });
    const params = new URLSearchParams(window.location.search);
    if (params.get('create') === '1') document.querySelector('[data-new-manual]').click();
    if (/^\d+$/.test(params.get('edit') || '')) edit(params.get('edit'));
});
