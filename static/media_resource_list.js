(function () {
    var filters = document.getElementById('resource-filters');
    if (filters) {
        document.addEventListener('click', function (event) {
            if (filters.open && !filters.contains(event.target)) filters.open = false;
        });
        filters.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && filters.open) {
                event.preventDefault();
                event.stopPropagation();
                filters.open = false;
                filters.querySelector('summary').focus();
            }
        });
    }
    var modal = document.getElementById('resource-detail-modal');
    if (!modal) return;
    var content = document.getElementById('resource-detail-content');
    var title = document.getElementById('resource-detail-title');
    var kicker = modal.querySelector('[data-resource-detail-kicker]');
    var closeButton = modal.querySelector('button[data-close-resource-detail]');
    var actions = document.getElementById('resource-detail-actions');
    var editLink = document.getElementById('resource-detail-edit');
    var origin = null;
    var currentUrl = '';
    var previousOverflow = '';
    var loadId = 0;

    function updateEditTeams() {
        var access = content.querySelector('[data-edit-resource-access]');
        if (!access) return;
        var count = access.querySelectorAll('input[type="checkbox"]:checked').length;
        access.querySelector('[data-access-summary]').textContent = count ? count + (count === 1 ? ' equipe selecionada' : ' equipes selecionadas') : 'Selecionar equipes';
    }

    function displayEdit(html) {
        if (actions) { actions.hidden = true; actions.open = false; }
        kicker.textContent = 'Editar recurso';
        title.textContent = origin.dataset.resourceName;
        content.innerHTML = html;
        content.scrollTop = 0;
        updateEditTeams();
        var first = content.querySelector('[data-field-error] input, [data-field-error] select, [data-field-error] textarea') || content.querySelector('input:not([type="hidden"])');
        if (first) first.focus();
    }

    async function loadEdit(url) {
        var requestId = ++loadId;
        if (actions) { actions.hidden = true; actions.open = false; }
        kicker.textContent = 'Editar recurso';
        content.textContent = 'Carregando edição...';
        try {
            var editUrl = new URL(url, window.location.href);
            editUrl.searchParams.set('modal', '1');
            var response = await fetch(editUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok || response.redirected) throw new Error('Requisição recusada');
            var html = await response.text();
            if (requestId === loadId) displayEdit(html);
        } catch (error) {
            if (requestId === loadId) content.textContent = 'Não foi possível carregar a edição. Feche e tente novamente.';
        }
    }

    async function loadDetail(url, message) {
        var requestId = ++loadId;
        kicker.textContent = 'Recurso';
        if (origin) title.textContent = origin.dataset.resourceName;
        if (actions) { actions.hidden = true; actions.open = false; }
        content.textContent = 'Carregando recurso...';
        try {
            var detailUrl = new URL(url, window.location.href);
            detailUrl.searchParams.set('modal', '1');
            var response = await fetch(detailUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok || response.redirected) throw new Error('Requisição recusada');
            var html = await response.text();
            if (requestId !== loadId) return;
            content.innerHTML = html;
            var detail = content.querySelector('[data-resource-detail]');
            if (actions && detail && detail.dataset.editUrl) {
                editLink.href = detail.dataset.editUrl;
                actions.hidden = false;
            }
            if (message) {
                var notice = document.createElement('p');
                notice.className = 'text-sm text-emerald-700 mb-3';
                notice.setAttribute('role', 'status');
                notice.textContent = message;
                content.prepend(notice);
            }
        } catch (error) {
            if (requestId === loadId) content.textContent = 'Não foi possível carregar o recurso. Feche e tente novamente.';
        }
    }

    function closeDetail() {
        ++loadId;
        modal.classList.remove('active');
        modal.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = previousOverflow;
        content.replaceChildren();
        if (actions) { actions.open = false; actions.hidden = true; }
        if (origin) origin.focus();
    }

    document.addEventListener('click', function (event) {
        if (actions && actions.open && !actions.contains(event.target)) actions.open = false;
        var edit = event.target.closest('#resource-detail-edit');
        if (edit && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
            event.preventDefault();
            loadEdit(edit.href);
            return;
        }
        if (event.target.closest('[data-cancel-resource-edit]')) {
            loadDetail(currentUrl);
            return;
        }
        var link = event.target.closest('[data-open-resource-detail]');
        if (link && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
            event.preventDefault();
            origin = link;
            currentUrl = link.href;
            previousOverflow = document.body.style.overflow;
            title.textContent = link.dataset.resourceName;
            modal.classList.add('active');
            modal.setAttribute('aria-hidden', 'false');
            document.body.style.overflow = 'hidden';
            closeButton.focus();
            loadDetail(currentUrl);
        } else if (event.target.closest('[data-close-resource-detail]')) {
            closeDetail();
        }
    });

    modal.addEventListener('change', function (event) {
        if (event.target.closest('[data-edit-resource-access]')) updateEditTeams();
    });

    modal.addEventListener('submit', async function (event) {
        var form = event.target.closest('[data-resource-edit-form]');
        if (!form) return;
        event.preventDefault();
        var button = form.querySelector('button[type="submit"]');
        var error = form.querySelector('[data-resource-edit-error]');
        var savedUrl = currentUrl;
        var savedOrigin = origin;
        button.disabled = true;
        error.classList.add('hidden');
        try {
            var response = await fetch(form.action, {
                method: 'POST', body: new FormData(form), headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (response.status === 422) {
                var html = await response.text();
                if (modal.classList.contains('active') && currentUrl === savedUrl) displayEdit(html);
                return;
            }
            if (!response.ok || response.redirected) throw new Error('Requisição recusada');
            var result = await response.json();
            savedOrigin.dataset.resourceName = result.name;
            savedOrigin.querySelector('h2').textContent = result.name;
            savedOrigin.querySelector('[data-resource-category]').textContent = result.category;
            var label = savedOrigin.querySelector('[data-resource-credential-label]');
            if (label && result.has_password) label.textContent = 'Com credenciais';
            if (modal.classList.contains('active') && currentUrl === savedUrl) loadDetail(currentUrl, 'Recurso atualizado com sucesso.');
        } catch (err) {
            error.textContent = 'Não foi possível salvar as alterações. Tente novamente.';
            error.classList.remove('hidden');
        } finally { button.disabled = false; }
    });

    document.addEventListener('keydown', function (event) {
        if (!modal.classList.contains('active')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (actions && actions.open) { actions.open = false; actions.querySelector('summary').focus(); }
            else closeDetail();
            return;
        }
        if (event.key !== 'Tab') return;
        var focusable = Array.from(modal.querySelectorAll('a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')).filter(function (element) { return element.getClientRects().length; });
        if (!focusable.length) return;
        var first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });

    var initial = document.getElementById('resource-edit-initial');
    var params = new URLSearchParams(window.location.search);
    var initialId = initial ? initial.dataset.resourceId : (params.get('edit') || params.get('resource'));
    if (initialId && /^\d+$/.test(initialId)) {
        var initialLink = document.querySelector('[data-open-resource-detail][data-resource-id="' + initialId + '"]');
        if (initialLink) {
            initialLink.click();
            if (initial) { ++loadId; displayEdit(initial.innerHTML); }
            else if (params.has('edit') && actions) {
                loadEdit(initialLink.href.replace(/\/$/, '') + '/editar/');
            }
        }
    }
}());
