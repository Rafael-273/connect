(function () {
    document.addEventListener('click', async function (event) {
        var button = event.target.closest('[data-resource-reveal]');
        if (!button) return;
        var detail = button.closest('[data-resource-detail]');
        var area = detail.querySelector('[data-resource-secret]');
        var error = detail.querySelector('[data-resource-reveal-error]');
        if (button.getAttribute('aria-expanded') === 'true') {
            area.textContent = '';
            area.classList.add('hidden');
            button.textContent = 'Revelar senha';
            button.setAttribute('aria-expanded', 'false');
            return;
        }
        button.disabled = true;
        error.classList.add('hidden');
        try {
            var response = await fetch(button.dataset.resourceReveal, {
                method: 'POST', headers: { 'X-CSRFToken': detail.querySelector('[data-resource-csrf]').value },
            });
            if (!response.ok || response.redirected) throw new Error('Requisição recusada');
            var data = await response.json();
            area.textContent = 'Senha: ' + data.password + (data.notes ? '\nNotas: ' + data.notes : '');
            area.classList.remove('hidden');
            button.textContent = 'Ocultar senha';
            button.setAttribute('aria-expanded', 'true');
        } catch (err) {
            error.textContent = 'Não foi possível revelar a senha. Tente novamente.';
            error.classList.remove('hidden');
        } finally { button.disabled = false; }
    });
}());
