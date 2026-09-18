
(function () {
    document.querySelectorAll('[data-demand-team]').forEach(function (team) {
        const form = team.form;
        const help = form.querySelector('.team-assignment-help');
        function updateHelp() {
            if (!help) return;
            help.textContent = team.value
                ? 'Equipe responsável pela demanda. Os responsáveis podem ser de qualquer membro do ministério.'
                : '';
        }
        form.addEventListener('change', updateHelp);
        updateHelp();
    });
})();
