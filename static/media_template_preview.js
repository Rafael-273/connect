(() => {
    const source = document.getElementById('media-template-previews');
    const output = document.getElementById('media-template-preview');
    const select = document.getElementById('id_event-event_type');
    const apply = document.getElementById('id_event-apply_template');
    const date = document.getElementById('id_event-event_date');
    if (!source || !output || !select) return;
    const templates = JSON.parse(source.textContent);
    function render() {
        output.replaceChildren();
        const items = apply && !apply.checked ? [] : (templates[select.value] || []);
        const title = document.createElement('p');
        title.className = 'font-semibold';
        title.textContent = items.length ? `${items.length} demanda(s) serão criadas:` : 'Nenhuma demanda automática será criada.';
        output.append(title);
        const list = document.createElement('ul');
        list.className = 'mt-2 space-y-2';
        items.forEach(item => {
            const row = document.createElement('li');
            let deadline = item.due;
            if (date && date.value && item.offset !== null) {
                const value = new Date(`${date.value}T12:00:00`);
                value.setDate(value.getDate() + item.offset);
                if (!Number.isNaN(value.getTime())) deadline += ` (${value.toLocaleDateString('pt-BR')})`;
            }
            row.textContent = `${item.title} · ${deadline}${item.team ? ` · ${item.team}` : ''}`;
            list.append(row);
        });
        output.append(list);
    }
    [select, apply, date].filter(Boolean).forEach(field => field.addEventListener('change', render));
    render();
})();
