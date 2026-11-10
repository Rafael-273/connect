document.addEventListener('DOMContentLoaded', () => {
    const app = document.getElementById('manual-app');
    const dialog = document.getElementById('manual-dialog');
    if (!app || !dialog) return;
    const form = document.getElementById('manual-form');
    const audioForm = document.getElementById('manual-audio-form');
    const choices = document.getElementById('manual-choices');
    const rich = document.getElementById('manual-rich');
    const source = form.elements.content;
    const csrf = form.elements.csrfmiddlewaretoken.value;
    const preview = document.getElementById('manual-preview');
    const error = form.querySelector('[data-form-error]');
    const editUrl = id => app.dataset.editUrl.replace('/0/', `/${id}/`);
    const statusUrl = id => app.dataset.statusUrl.replace('/0/', `/${id}/`);
    let dirty = false;
    source.hidden = true;
    source.required = false; // The server validates the synchronized rich-text value.
    function display(which) {
        choices.hidden = which !== 'choices';
        form.hidden = which !== 'form';
        audioForm.hidden = which !== 'audio';
    }
    function open() { if (!dialog.open) dialog.showModal(); }
    function reset() {
        form.reset(); rich.innerHTML = ''; preview.hidden = true; error.textContent = '';
        form.action = app.dataset.createUrl; dirty = false;
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
            form.elements.title.value = data.title; form.elements.summary.value = data.summary;
            form.elements.sub_team.value = data.sub_team || '';
            form.elements.is_active.checked = data.is_active;
            rich.innerHTML = html;
            document.getElementById('manual-dialog-title').textContent = 'Editar manual';
            display('form'); open();
        } catch (e) { window.alert(e.message); }
    }
    document.querySelector('[data-new-manual]').addEventListener('click', () => {
        reset(); document.getElementById('manual-dialog-title').textContent = 'Novo manual';
        display('choices'); open();
    });
    document.querySelectorAll('[data-edit-manual]').forEach(button => button.addEventListener('click', () => edit(button.dataset.editManual)));
    dialog.querySelector('[data-manual-choice]').addEventListener('click', () => { display('form'); rich.focus(); });
    dialog.querySelector('[data-ai-choice]').addEventListener('click', () => display('audio'));
    dialog.querySelector('[data-back-choice]').addEventListener('click', () => display('choices'));
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => {
        if (!dirty || window.confirm('Descartar alterações não salvas?')) { dirty = false; dialog.close(); }
    }));
    dialog.addEventListener('cancel', event => { if (dirty && !window.confirm('Descartar alterações não salvas?')) event.preventDefault(); else dirty = false; });
    form.addEventListener('input', () => { dirty = true; });
    rich.addEventListener('input', () => { dirty = true; });
    rich.addEventListener('paste', event => {
        event.preventDefault();
        document.execCommand('insertText', false, event.clipboardData.getData('text/plain'));
    });
    dialog.querySelectorAll('[data-cmd]').forEach(button => button.addEventListener('mousedown', event => event.preventDefault()));
    dialog.querySelectorAll('[data-cmd]').forEach(button => button.addEventListener('click', () => {
        rich.focus(); document.execCommand(button.dataset.cmd, false, button.dataset.value || null); dirty = true;
    }));
    dialog.querySelectorAll('[data-insert]').forEach(button => {
        button.addEventListener('mousedown', event => event.preventDefault());
        button.addEventListener('click', () => {
            rich.focus(); const type = button.dataset.insert;
            if (type === 'link' || type === 'image') {
                const url = window.prompt('Informe o endereço HTTPS ou HTTP:');
                if (!url || !/^https?:\/\//i.test(url)) return;
                document.execCommand(type === 'link' ? 'createLink' : 'insertImage', false, url);
            } else if (type === 'table') {
                document.execCommand('insertHTML', false, '<table><thead><tr><th>Coluna 1</th><th>Coluna 2</th></tr></thead><tbody><tr><td>Conteúdo</td><td>Conteúdo</td></tr></tbody></table><p><br></p>');
            } else document.execCommand('insertHTML', false, '<p>☐ Item do checklist</p>');
            dirty = true;
        });
    });
    dialog.querySelector('[data-preview]').addEventListener('click', async () => {
        try { preview.innerHTML = await renderContent(rich.innerHTML); preview.hidden = false; }
        catch (e) { error.textContent = e.message; }
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
    async function poll(id, status, retry, autoOpen) {
        try {
            const response = await fetch(statusUrl(id), {credentials: 'same-origin'});
            if (!response.ok) throw new Error();
            const data = await response.json(); status.textContent = data.error || labels[data.status] || 'Aguardando processamento…';
            if (retry && data.retryable) retry.hidden = false;
            if (data.status === 'completed') {
                if (dirty) { status.textContent = 'Manual pronto e salvo como rascunho. Atualize a lista após salvar suas alterações.'; return; }
                if (autoOpen && data.manual_id) await edit(data.manual_id); else window.location.reload();
                return;
            }
            if (data.status === 'error') { if (retry) retry.hidden = false; else status.textContent += ' Atualize a página para tentar novamente sem reenviar o arquivo.'; return; }
            window.setTimeout(() => poll(id, status, retry, autoOpen), 2500);
        } catch (_) { status.textContent = 'Falha de conexão. Atualize a página para acompanhar a geração.'; }
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
    audioForm.addEventListener('submit', async event => {
        event.preventDefault(); const status = audioForm.querySelector('[data-audio-status]'); const button = audioForm.querySelector('[type=submit]');
        status.textContent = 'Enviando áudio…'; button.disabled = true;
        try {
            const response = await fetch(app.dataset.generateUrl, {method: 'POST', body: new FormData(audioForm), credentials: 'same-origin'});
            const data = await response.json(); if (!response.ok || data.status === 'error') throw new Error((data.error || 'Falha ao iniciar.') + (data.id ? ' Atualize a página para tentar novamente sem reenviar o arquivo.' : ''));
            poll(data.id, status, null, true);
        } catch (e) { status.textContent = e.message || 'Falha de conexão. Tente novamente.'; button.disabled = false; }
    });
    const params = new URLSearchParams(window.location.search);
    if (params.get('create') === '1') document.querySelector('[data-new-manual]').click();
    if (/^\d+$/.test(params.get('edit') || '')) edit(params.get('edit'));
});
