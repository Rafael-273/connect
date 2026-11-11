(() => {
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-toggle-review-reject]');
        if (!button) return;
        const panel = document.getElementById(button.getAttribute('aria-controls'));
        if (!panel) return;
        const open = button.getAttribute('aria-expanded') !== 'true';
        button.setAttribute('aria-expanded', String(open));
        panel.hidden = !open;
        if (open) panel.querySelector('textarea')?.focus();
    });
    const dialog = document.getElementById('delivery-review-dialog');
    if (!dialog || !dialog.showModal) return;
    let opener;
    let changed = false;
    async function loadReview() {
        const url = new URL(opener.href); url.searchParams.set('fragment', '1');
        const response = await fetch(url);
        if (!response.ok || response.redirected) throw new Error('unavailable');
        const html = await response.text();
        if (dialog.open) dialog.innerHTML = html;
    }
    document.addEventListener('click', async event => {
        const link = event.target.closest('[data-open-delivery-review]');
        if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        opener = link; changed = false;
        dialog.replaceChildren();
        dialog.showModal();
        try { await loadReview(); }
        catch (_) { dialog.close(); window.location.assign(link.href); }
    });
    dialog.addEventListener('submit', async event => {
        const form = event.target.closest('[data-review-delivery-form]');
        if (!form || !event.submitter) return;
        event.preventDefault();
        const data = new FormData(form);
        data.set(event.submitter.name, event.submitter.value);
        const buttons = form.querySelectorAll('button');
        buttons.forEach(button => { button.disabled = true; });
        const error = form.querySelector('[role="alert"]'); error.hidden = true;
        try {
            const response = await fetch(form.action, {method:'POST', body:data, headers:{'X-Requested-With':'XMLHttpRequest'}});
            if (response.redirected) throw new Error('Sua sessão expirou. Recarregue a página.');
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Não foi possível registrar a avaliação.');
            changed = true;
            await loadReview();
        } catch (failure) {
            error.textContent = failure.message || 'Não foi possível registrar a avaliação.';
            error.hidden = false;
            buttons.forEach(button => { button.disabled = false; });
        }
    });
    dialog.addEventListener('click', event => {
        if (event.target.closest('[data-close-delivery-review]') || event.target === dialog) dialog.close();
    });
    dialog.addEventListener('close', () => {
        if (changed) window.location.reload();
        else opener?.focus();
    });
})();
