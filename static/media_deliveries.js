(() => {
    'use strict';
    const workerUrl = document.currentScript.dataset.hashWorker;
    const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    const message = (row, text) => { row.querySelector('[data-delivery-message]').textContent = text; };
    const progress = (row, value) => { const el = row.querySelector('progress'); el.classList.remove('hidden'); el.value = value; };
    async function request(url, data) {
        const response = await fetch(url, data ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify(data)} : {});
        if (response.redirected) throw new Error('Sua sessão expirou. Recarregue a página e tente novamente.');
        let result;
        try { result = await response.json(); }
        catch (_) { throw new Error('Não foi possível acessar este conteúdo. Tente novamente.'); }
        if (!response.ok) throw new Error(result.error || 'Não foi possível acessar este conteúdo.');
        return result;
    }
    function hashFile(file, row) {
        return new Promise((resolve, reject) => {
            const worker = new Worker(workerUrl);
            worker.onmessage = ({data}) => {
                if (data.error) { worker.terminate(); reject(new Error(data.error)); }
                else if (data.sha256) { worker.terminate(); resolve(data.sha256); }
                else { message(row, `Preparando arquivo: ${data.progress}%`); progress(row, data.progress); }
            };
            worker.onerror = () => { worker.terminate(); reject(new Error('Não foi possível preparar o arquivo. Tente novamente.')); };
            worker.postMessage(file);
        });
    }
    function sendFile(url, body, headers, row, method = 'PUT') {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open(method, url);
            Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
            xhr.upload.onprogress = event => {
                if (event.lengthComputable) {
                    const percent = Math.round(event.loaded / event.total * 100);
                    progress(row, percent); message(row, `Enviando: ${percent}%`);
                }
            };
            xhr.onerror = () => reject(new Error('O envio falhou. Verifique sua conexão e tente novamente.'));
            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(method === 'POST' ? JSON.parse(xhr.responseText) : {});
                } else {
                    let text = 'O envio falhou. Tente novamente.';
                    try { text = JSON.parse(xhr.responseText).error || text; } catch (_) { /* S3 returns XML. */ }
                    reject(new Error(text));
                }
            };
            xhr.send(body);
        });
    }
    function busy(row, value) {
        row.querySelectorAll('input,button,select').forEach(el => { el.disabled = value; });
        row.dataset.busy = value ? '1' : '';
    }
    let reloadWhenIdle = false;
    function finish(row) {
        busy(row, false);
        document.dispatchEvent(new CustomEvent('delivery:idle'));
        if (reloadWhenIdle && !document.querySelector('[data-delivery-row][data-busy="1"], [data-delivery-row][data-failed="1"]')) {
            reloadWhenIdle = false;
            window.location.reload();
        }
    }
    function success(row, result) {
        row.outerHTML = result.html;
        document.dispatchEvent(new CustomEvent('delivery:updated', {detail: {status: result.task_status, needsConfirmation: result.needs_confirmation, forceRefresh: result.force_refresh}}));
        if ((result.task_status === 'in_review' || result.needs_confirmation || result.force_refresh) && !document.getElementById('work-detail-dialog')) reloadWhenIdle = true;
    }
    async function remove(row, button) {
        if (row.dataset.busy) return;
        busy(row, true);
        row.dataset.failed = '';
        try {
            success(row, await request(row.dataset.endpoint, {action: 'withdraw', delivery_id: button.dataset.deliveryRemove}));
        } catch (error) {
            row.dataset.failed = '1';
            message(row, error.message);
        } finally { finish(row); }
    }
    async function upload(row) {
        if (row.dataset.busy) return;
        const file = row.querySelector('[data-delivery-upload]').files[0];
        if (!file) return;
        busy(row, true);
        row.dataset.failed = '';
        row.querySelector('[data-delivery-retry]').classList.add('hidden');
        try {
            if (!file.size || file.size > 2048 * 1024 * 1024) throw new Error('Selecione um arquivo de até 2048 MB.');
            const sha256 = await hashFile(file, row);
            if (row.deliveryHash !== sha256) { row.deliverySubmission = crypto.randomUUID(); row.deliveryHash = sha256; }
            const endpoint = row.dataset.endpoint;
            const allocation = await request(endpoint, {action: 'begin', name: file.name, size: file.size, mime: file.type, sha256});
            const complete = {action: 'complete', ticket: allocation.ticket, submission_id: row.deliverySubmission, name: file.name};
            let result;
            if (allocation.local && !allocation.existing) {
                const form = new FormData(); Object.entries(complete).forEach(([key, value]) => form.set(key, value)); form.set('file', file);
                result = await sendFile(endpoint, form, {'X-CSRFToken': csrf()}, row, 'POST');
            } else {
                if (!allocation.existing) await sendFile(allocation.upload_url, file, allocation.headers, row);
                message(row, allocation.existing ? 'Vinculando arquivo já salvo…' : 'Verificando e registrando entrega…');
                result = await request(endpoint, complete);
            }
            success(row, result);
        } catch (error) {
            row.dataset.failed = '1'; message(row, error.message); row.querySelector('[data-delivery-retry]').classList.remove('hidden');
        } finally { finish(row); }
    }
    function editor(row, opener) {
        const dialog = document.createElement('dialog');
        dialog.className = 'delivery-editor-dialog';
        dialog.setAttribute('aria-labelledby', 'delivery-editor-title');
        dialog.innerHTML = `
            <div class="delivery-editor-header">
                <div><p class="delivery-editor-kicker">Editor de vídeo</p><h2 id="delivery-editor-title">Selecionar projeto</h2></div>
                <button type="button" class="delivery-editor-close" data-editor-close aria-label="Fechar"><i class="fas fa-times" aria-hidden="true"></i></button>
            </div>
            <div class="delivery-editor-body">
                <div data-editor-projects>
                    <p class="delivery-editor-description">Encontre o projeto que contém o vídeo que deseja entregar.</p>
                    <label class="delivery-editor-search"><i class="fas fa-search" aria-hidden="true"></i><span class="sr-only">Buscar projeto ou responsável</span><input type="search" placeholder="Buscar projeto ou responsável" autocomplete="off" data-editor-search></label>
                    <p class="delivery-editor-section-label">Projetos</p>
                    <div class="delivery-editor-list" data-editor-project-list></div>
                    <button type="button" class="delivery-editor-more" data-editor-more hidden>Carregar mais projetos</button>
                </div>
                <div data-editor-selection hidden>
                    <div class="delivery-editor-selected"><div><span>Projeto selecionado</span><strong data-editor-project-name></strong></div><button type="button" data-editor-change>Trocar</button></div>
                    <p class="delivery-editor-section-label">Escolha uma exportação</p>
                    <div class="delivery-editor-list" data-editor-exports></div>
                </div>
                <p class="delivery-editor-message" data-editor-message role="status" aria-live="polite"></p>
            </div>
            <div class="delivery-editor-footer">
                <p><i class="fas fa-check-circle" aria-hidden="true"></i> O arquivo já está salvo e não será enviado novamente.</p>
                <div><button type="button" class="org-btn-secondary" data-editor-close>Cancelar</button><button type="button" class="org-btn-primary" data-editor-submit disabled>Usar exportação</button></div>
            </div>`;
        document.body.append(dialog);
        const search = dialog.querySelector('[data-editor-search]');
        const projectsPanel = dialog.querySelector('[data-editor-projects]');
        const selectedPanel = dialog.querySelector('[data-editor-selection]');
        const projectList = dialog.querySelector('[data-editor-project-list]');
        const exportsList = dialog.querySelector('[data-editor-exports]');
        const more = dialog.querySelector('[data-editor-more]');
        const notice = dialog.querySelector('[data-editor-message]');
        const submit = dialog.querySelector('[data-editor-submit]');
        let queryGeneration = 0, page = 0, searchTimer, selectedProject = null, selectedExport = null;
        let submitting = false, submission = null;
        const date = value => {
            const parsed = new Date(value);
            return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString('pt-BR');
        };
        const setNotice = (text, error = false) => { notice.textContent = text; notice.classList.toggle('is-error', error); };
        const setSubmitting = value => {
            submitting = value;
            dialog.setAttribute('aria-busy', String(value));
            dialog.querySelectorAll('input, button:not([data-editor-close])').forEach(element => { element.disabled = value; });
            if (!value) submit.disabled = !selectedExport;
            submit.textContent = value ? 'Preparando arquivo…' : 'Usar exportação';
        };
        const addProject = project => {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'delivery-editor-project';
            const icon = document.createElement('span'); icon.className = 'delivery-editor-project-icon';
            icon.innerHTML = '<i class="fas fa-film" aria-hidden="true"></i>';
            const text = document.createElement('span'); text.className = 'delivery-editor-project-text';
            const name = document.createElement('strong'); name.textContent = project.name;
            const meta = document.createElement('small'); meta.textContent = `${project.is_owner ? 'Seu projeto' : project.owner}${date(project.created_at) ? ' · ' + date(project.created_at) : ''}`;
            const arrow = document.createElement('i'); arrow.className = 'fas fa-chevron-right delivery-editor-arrow'; arrow.setAttribute('aria-hidden', 'true');
            text.append(name, meta); button.append(icon, text, arrow);
            button.addEventListener('click', () => chooseProject(project));
            projectList.append(button);
        };
        async function loadProjects(append = false) {
            const generation = ++queryGeneration, term = search.value.trim(), requested = append ? page + 1 : 1;
            if (!append) projectList.replaceChildren();
            more.hidden = true;
            setNotice('Buscando projetos…');
            try {
                const result = await request(`${row.dataset.projects}?q=${encodeURIComponent(term)}&page=${requested}`);
                if (!dialog.open || generation !== queryGeneration || term !== search.value.trim()) return;
                page = result.page;
                result.projects.forEach(addProject);
                more.hidden = !result.has_more;
                setNotice(projectList.children.length ? '' : 'Nenhum projeto encontrado. Tente outro nome ou responsável.');
            } catch (error) {
                if (dialog.open && generation === queryGeneration) setNotice(error.message, true);
            }
        }
        async function chooseProject(project) {
            clearTimeout(searchTimer);
            selectedProject = project; selectedExport = null; submission = null;
            queryGeneration++;
            projectsPanel.hidden = true; selectedPanel.hidden = false;
            dialog.querySelector('[data-editor-project-name]').textContent = project.name;
            exportsList.replaceChildren(); submit.disabled = true;
            setNotice('Buscando exportações…');
            try {
                const result = await request(`${row.dataset.projects}?project=${encodeURIComponent(project.id)}`);
                if (!dialog.open || selectedProject !== project) return;
                if (!result.exports.length) { setNotice('Ainda não há uma exportação disponível para este projeto.'); return; }
                setNotice('');
                result.exports.forEach(item => {
                    const button = document.createElement('button');
                    button.type = 'button'; button.className = 'delivery-editor-export'; button.setAttribute('aria-pressed', 'false');
                    const icon = document.createElement('span'); icon.className = 'delivery-editor-export-icon';
                    icon.innerHTML = '<i class="fas fa-play" aria-hidden="true"></i>';
                    const text = document.createElement('span'); text.className = 'delivery-editor-project-text';
                    const name = document.createElement('strong'); name.textContent = item.format || 'Vídeo exportado';
                    const metadata = [item.width && item.height ? `${item.width} × ${item.height}` : '', item.language, date(item.created_at)].filter(Boolean);
                    const details = document.createElement('small'); details.textContent = metadata.join(' · ');
                    const filename = document.createElement('small'); filename.textContent = item.name;
                    text.append(name, details, filename); button.append(icon, text);
                    button.addEventListener('click', () => {
                        selectedExport = item; submission = crypto.randomUUID();
                        exportsList.querySelectorAll('button').forEach(element => element.setAttribute('aria-pressed', String(element === button)));
                        submit.disabled = false;
                        const vertical = ['story', 'reels'].includes(row.dataset.format);
                        const mismatch = item.width && item.height && ((vertical && item.width > item.height) || (row.dataset.format === 'horizontal' && item.width < item.height));
                        setNotice(mismatch ? 'Confira a orientação: esta exportação parece ter outro formato.' : '');
                    });
                    exportsList.append(button);
                });
            } catch (error) { if (dialog.open && selectedProject === project) setNotice(error.message, true); }
        }
        search.addEventListener('input', () => {
            queryGeneration++;
            clearTimeout(searchTimer);
            projectList.replaceChildren();
            more.hidden = true;
            setNotice('Buscando projetos…');
            searchTimer = setTimeout(() => loadProjects(), 250);
        });
        more.addEventListener('click', () => loadProjects(true));
        dialog.querySelector('[data-editor-change]').addEventListener('click', () => {
            selectedProject = null; selectedExport = null; queryGeneration++;
            selectedPanel.hidden = true; projectsPanel.hidden = false; submit.disabled = true;
            loadProjects(); search.focus();
        });
        dialog.querySelectorAll('[data-editor-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
        dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
        dialog.addEventListener('close', () => {
            clearTimeout(searchTimer); queryGeneration++;
            dialog.remove();
            if (!submitting) {
                row.dataset.failed = '';
                document.dispatchEvent(new CustomEvent('delivery:idle'));
                if (opener?.isConnected) opener.focus();
            }
        });
        submit.addEventListener('click', async () => {
            if (!selectedExport || submitting) return;
            busy(row, true); row.dataset.failed = ''; setSubmitting(true);
            try {
                const data = {action:'editor', source_id:selectedExport.id, submission_id:submission, retry_index:true};
                let result = await request(row.dataset.endpoint, data); data.retry_index = false;
                while (result.pending && dialog.open && row.isConnected) {
                    setNotice(result.message);
                    await new Promise(resolve => setTimeout(resolve, 2500));
                    if (!dialog.open || !row.isConnected) break;
                    result = await request(row.dataset.endpoint, data);
                }
                if (!result.pending && row.isConnected) {
                    if (dialog.open) dialog.close();
                    success(row, result);
                }
            } catch (error) {
                row.dataset.failed = '1';
                if (dialog.open) setNotice(error.message, true); else if (row.isConnected) message(row, error.message);
            } finally {
                if (dialog.open) setSubmitting(false);
                finish(row);
                if (!dialog.open && opener?.isConnected) opener.focus();
            }
        });
        row.dataset.failed = '';
        dialog.showModal();
        loadProjects(); search.focus();
    }
    function initConfig(scope = document) {
        scope.querySelectorAll('[data-delivery-config]').forEach(config => {
            if (config.dataset.initialized) return;
            config.dataset.initialized = '1';
            const field = config.querySelector('[data-delivery-formats]');
            const choices = Array.from(config.querySelectorAll('[data-delivery-choice]'));
            const other = config.querySelector('[data-delivery-other]');
            const row = config.closest('.demand-assignment-row-wrap');
            const role = row.querySelector('.assignment-role-select');
            const custom = row.querySelector('.assignment-role-custom');
            const type = config.closest('form')?.querySelector('[name$="content_type"]');
            let values = []; try { values = JSON.parse(field.value); } catch (_) { /* Server validates malformed data. */ }
            choices.forEach(choice => { choice.checked = values.includes(choice.value); });
            const update = () => {
                field.value = JSON.stringify(choices.filter(choice => choice.checked).map(choice => choice.value));
                other.hidden = !choices.some(choice => choice.value === 'other' && choice.checked);
            };
            const filter = () => {
                const video = ['video', 'reel', 'youtube', 'livestream'].includes(type?.value);
                const name = (role?.value === '__custom__' ? custom?.value : role?.value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
                const visual = /design|arte|criacao|ilustra/.test(name) || (video && /editor|edicao|filmmaker/.test(name));
                config.hidden = !visual;
                const existing = row.querySelector('[name$="-assignment_id"]')?.value;
                if (video && field.dataset.deliveryDefaults === '1') {
                    choices.forEach(choice => { choice.checked = false; });
                    delete field.dataset.deliveryDefaults;
                    update();
                }
                if (visual && type?.value && !existing && !field.dataset.deliveryConfigured) {
                    if (!video) {
                        choices.forEach(choice => { choice.checked = ['feed', 'story', 'telao'].includes(choice.value); });
                        field.dataset.deliveryDefaults = '1';
                    }
                    field.dataset.deliveryConfigured = '1';
                    update();
                }
                config.querySelectorAll('[data-format-group]').forEach(option => {
                    const group = option.dataset.formatGroup;
                    option.hidden = group !== 'shared' && group !== (video ? 'video' : 'design') && !option.querySelector('input').checked;
                });
            };
            choices.forEach(choice => choice.addEventListener('change', () => { field.dataset.deliveryConfigured = '1'; delete field.dataset.deliveryDefaults; update(); filter(); }));
            role?.addEventListener('change', filter);
            custom?.addEventListener('input', filter);
            type?.addEventListener('change', filter);
            update(); filter();
        });
    }
    document.addEventListener('change', event => { if (event.target.matches('[data-delivery-upload]')) upload(event.target.closest('[data-delivery-row]')); });
    document.addEventListener('click', event => {
        const row = event.target.closest('[data-delivery-row]'); if (!row) return;
        const removeButton = event.target.closest('[data-delivery-remove]');
        if (removeButton) remove(row, removeButton);
        const editorButton = event.target.closest('[data-delivery-editor]');
        if (editorButton) editor(row, editorButton);
        if (event.target.closest('[data-delivery-retry]')) upload(row);
    });
    initConfig();
    new MutationObserver(() => initConfig()).observe(document.body, {childList: true, subtree: true});
})();
