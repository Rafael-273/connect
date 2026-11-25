(() => {
    const dialog = document.getElementById('work-detail-dialog');
    if (!dialog || !dialog.showModal) return;
    const body = document.getElementById('work-detail-body');
    let controller;
    let opener;
    let previousOverflow;
    let refreshPending = false;
    function message(text) {
        body.replaceChildren();
        const paragraph = document.createElement('p');
        paragraph.textContent = text;
        paragraph.setAttribute('role', 'status');
        body.append(paragraph);
    }
    function updateHeader() {
        const activity = body.querySelector('[data-work-header-title]');
        if (!activity) return;
        document.getElementById('work-detail-stage').textContent = activity.dataset.workStageTitle;
        document.getElementById('work-detail-title').textContent = activity.dataset.workHeaderTitle;
    }
    document.addEventListener('click', async event => {
        const link = event.target.closest('[data-work-detail]');
        if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        opener = link;
        controller?.abort();
        controller = new AbortController();
        previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        message('Carregando atividade…');
        dialog.showModal();
        const url = new URL(link.href);
        url.searchParams.set('fragment', 'activity');
        try {
            const response = await fetch(url, {signal: controller.signal});
            if (!response.ok || response.redirected) throw new Error('unavailable');
            const html = await response.text();
            if (dialog.open) {
                body.innerHTML = html;
                updateHeader();
            }
        } catch (error) {
            if (error.name !== 'AbortError') message('Não foi possível carregar a atividade. Feche o card e tente novamente.');
        }
    });
    dialog.querySelector('.work-detail-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
        if (event.target !== dialog) return;
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    dialog.addEventListener('close', () => {
        controller?.abort();
        refreshPending = false;
        document.body.style.overflow = previousOverflow;
        opener?.focus();
    });
    async function refreshWhenIdle() {
        if (!refreshPending || !opener || !dialog.open || body.querySelector('[data-delivery-row][data-busy="1"], [data-delivery-row][data-failed="1"]')) return;
        refreshPending = false;
        const url = new URL(opener.href);
        url.searchParams.set('fragment', 'activity');
        try {
            const response = await fetch(url);
            if (!response.ok || response.redirected) throw new Error('unavailable');
            if (dialog.open) body.innerHTML = await response.text();
        } catch (_) { window.location.reload(); }
    }
    document.addEventListener('delivery:updated', event => {
        dialog.querySelector('.work-detail-status-form [role="alert"]')?.remove();
        if (event.detail?.status === 'in_review' || event.detail?.needsConfirmation || event.detail?.forceRefresh) refreshPending = true;
        refreshWhenIdle();
    });
    document.addEventListener('delivery:idle', refreshWhenIdle);
    dialog.addEventListener('submit', async event => {
        const form = event.target.closest('.work-detail-status-form');
        if (!form) return;
        event.preventDefault();
        const data = new FormData(form);
        if (event.submitter?.name) data.set(event.submitter.name, event.submitter.value);
        data.set('return_to', 'list');
        const buttons = form.querySelectorAll('button');
        buttons.forEach(button => { button.disabled = true; });
        try {
            const response = await fetch(form.action, {method: 'POST', body: data});
            if (!response.ok) throw new Error(await response.text());
            if (!response.redirected || new URL(response.url).pathname !== window.location.pathname) throw new Error('update');
            window.location.reload();
        } catch (failure) {
            buttons.forEach(button => { button.disabled = false; });
            let error = form.querySelector('[role="alert"]');
            if (!error) { error = document.createElement('p'); error.setAttribute('role', 'alert'); form.append(error); }
            error.textContent = failure.message === 'update' ? 'Não foi possível confirmar a atualização. Tente novamente.' : failure.message;
        }
    });
})();
