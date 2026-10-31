(function () {
    'use strict';
    const filterMenu = document.getElementById('schedule-filter-menu');
    const filterToggle = document.getElementById('schedule-filter-toggle');
    const filterPanel = document.getElementById('schedule-filter-panel');
    if (filterMenu && filterToggle && filterPanel) {
        function closeFilters() {
            filterPanel.hidden = true;
            filterToggle.setAttribute('aria-expanded', 'false');
        }
        filterToggle.addEventListener('click', () => {
            const open = filterPanel.hidden;
            filterPanel.hidden = !open;
            filterToggle.setAttribute('aria-expanded', String(open));
        });
        document.addEventListener('click', event => {
            if (!filterMenu.contains(event.target)) closeFilters();
        });
        filterMenu.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !filterPanel.hidden) {
                closeFilters();
                filterToggle.focus();
            }
        });
    }
    const dialog = document.getElementById('schedule-create-dialog');
    if (!dialog) return;
    document.querySelectorAll('[data-open-schedule]').forEach(button => {
        button.addEventListener('click', () => dialog.showModal());
    });
    document.getElementById('schedule-create-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
        if (event.target !== dialog) return;
        const box = dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
    });
})();
