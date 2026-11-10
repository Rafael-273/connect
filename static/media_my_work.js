(() => {
    const dialog = document.getElementById('work-detail-dialog');
    if (!dialog || !dialog.showModal) return;
    const body = document.getElementById('work-detail-body');
    let controller;
    let opener;
    let previousOverflow;
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
        document.body.style.overflow = previousOverflow;
        opener?.focus();
    });
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
            if (!response.ok || !response.redirected || new URL(response.url).pathname !== window.location.pathname) throw new Error('update');
            window.location.reload();
        } catch (_) {
            buttons.forEach(button => { button.disabled = false; });
            let error = form.querySelector('[role="alert"]');
            if (!error) { error = document.createElement('p'); error.setAttribute('role', 'alert'); form.append(error); }
            error.textContent = 'Não foi possível confirmar a atualização. Tente novamente.';
        }
    });
})();
