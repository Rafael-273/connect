(function () {
    'use strict';
    const labels = {mentor: 'Apto a acompanhar', trainee: 'Em treinamento', pending: 'Classificação pendente'};
    function assignmentPayload(planId, members, locked) {
        return {plan_id: Number(planId), member_ids: members.map(Number), locked_member_ids: locked.map(Number).filter(id => members.map(Number).includes(id))};
    }
    if (typeof module !== 'undefined') module.exports = {assignmentPayload, labels};
    if (typeof document === 'undefined') return;
    window.ScheduleGenerationUI = {create({getState, onSave, onGenerate, onRemoveObsolete, onDirty}) {
        const $ = id => document.getElementById(id), host = $('sa-generation');
        let editing = null, dirty = false, busy = false, blocked = false, filter = '', onlyPending = false, view = 'list', selectedDate = null;
        const expandedOccurrences = new Set();
        const node = (tag, cls, value) => { const n = document.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = value; return n; };
        const button = (label, fn, cls = 'org-btn-secondary') => { const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', fn); b.disabled = blocked || busy; return b; };
        const dateLabel = value => new Intl.DateTimeFormat('pt-BR', {weekday: 'short', day: '2-digit', month: 'long'}).format(new Date(value + 'T12:00:00'));
        function payload() {
            const form = $('sg-edit-form');
            return assignmentPayload(editing, Array.from(form.querySelectorAll('[data-member]:checked'), n => n.value), Array.from(form.querySelectorAll('[data-lock]:checked'), n => n.value));
        }
        async function save() {
            if (!dirty) return true;
            if (blocked || busy) return false;
            busy = true; $('sg-status').textContent = 'Validando e salvando a composição…'; setControls();
            const success = await onSave(payload());
            busy = false;
            if (success) { dirty = false; editing = null; if ($('sg-edit-dialog').open) $('sg-edit-dialog').close(); onDirty(); $('sg-status').textContent = 'Composição salva. Os bloqueios serão preservados ao regenerar.'; }
            else $('sg-status').textContent = 'Composição não salva. Corrija as pendências indicadas acima.';
            render(getState()); return success;
        }
        function setControls() {
            host.querySelectorAll('button, input, select').forEach(n => { n.disabled = busy || blocked || n.dataset.sgEmpty === 'true'; });
        }
        function editForm(row) {
            const form = node('form', 'sg-edit'); form.id = 'sg-edit-form';
            form.append(node('p', 'sg-edit-help', 'Selecione entre os integrantes disponíveis para este culto. Para trocar, desmarque um e selecione outro.'));
            form.append(node('p', 'sg-edit-hint', 'Fixar mantém a pessoa nesta equipe ao regenerar a escala.'));
            const all = new Map(row.candidates.map(c => [c.id, c]));
            row.people.forEach(p => { if (!all.has(p.member_id)) all.set(p.member_id, {id: p.member_id, name: p.name + ' · precisa de revisão', experience: p.experience}); });
            if (!all.size) form.append(node('p', 'sg-edit-empty', 'Nenhum integrante disponível para este culto. Revise as disponibilidades na etapa anterior.'));
            all.forEach(person => {
                const existing = row.people.find(p => p.member_id === person.id), line = node('div', 'sg-choice');
                const label = node('label'), selected = node('input'); selected.type = 'checkbox'; selected.value = person.id; selected.dataset.member = ''; selected.checked = !!existing;
                const info = node('span', 'sg-choice-info'); info.append(node('strong', '', person.name), node('small', '', labels[person.experience] || person.experience));
                label.append(selected, info);
                const lockLabel = node('label'), lock = node('input'); lock.type = 'checkbox'; lock.value = person.id; lock.dataset.lock = ''; lock.checked = existing ? existing.locked : true;
                lockLabel.append(lock, node('span', '', 'Fixar')); lockLabel.title = 'Manter nesta equipe ao regenerar a escala'; line.append(label, lockLabel); form.append(line);
            });
            form.addEventListener('change', () => { dirty = true; onDirty(); $('sg-status').textContent = 'Composição alterada; confirme para validar e salvar.'; });
            const actions = node('div', 'sg-dialog-actions'); actions.append(button('Cancelar', cancelEdit), button('Salvar composição', async () => { if (!dirty) { editing = null; $('sg-edit-dialog').close(); render(getState()); return; } await save(); }, 'org-btn-primary')); form.append(actions);
            form.addEventListener('submit', event => { event.preventDefault(); save(); });
            return form;
        }
        async function cancelEdit() {
            if (dirty && !await window.ScheduleConfirm({title: 'Descartar alterações?', message: 'As alterações desta composição ainda não foram salvas.', action: 'Descartar alterações'})) return;
            dirty = false; editing = null; onDirty(); if ($('sg-edit-dialog').open) $('sg-edit-dialog').close(); render(getState());
        }
        $('sg-edit-close').addEventListener('click', cancelEdit);
        $('sg-edit-dialog').addEventListener('cancel', event => { event.preventDefault(); cancelEdit(); });
        function render(state) {
            if (busy) return;
            if (editing && !dirty) editing = null;
            const data = state.generation, summary = data.summary;
            $('sg-stale').hidden = !data.stale;
            $('sg-generate').textContent = data.generated ? 'Regenerar sugestões' : 'Gerar sugestões';
            const teamSelect = $('sg-team'), previous = filter;
            teamSelect.replaceChildren(); const option = node('option', '', 'Todas as equipes · visão mensal'); option.value = ''; teamSelect.append(option);
            new Map(data.rows.map(row => [String(row.team_id), row.team])).forEach((name, id) => { const o = node('option', '', name); o.value = id; teamSelect.append(o); });
            filter = Array.from(teamSelect.options).some(o => o.value === previous) ? previous : ''; teamSelect.value = filter;
            const filterIndicator = $('sg-filter-indicator');
            filterIndicator.hidden = !filter && !onlyPending;
            const obsolete = $('sg-obsolete'); obsolete.hidden = !data.obsolete.length;
            obsolete.replaceChildren(node('strong', '', 'Designações que precisam ser retiradas'));
            data.obsolete.forEach(item => { const line = node('p'); line.append(node('span', '', `${item.name}${item.locked ? ' · bloqueado' : ''}: ${item.reason} `), button('Remover', async () => { if (await window.ScheduleConfirm({title: 'Remover designação?', message: 'Esta designação será removida do rascunho.', action: 'Remover'})) await onRemoveObsolete(item.id); })); obsolete.append(line); });
            const results = $('sg-results');
            // Keep an unsaved composition in place while unrelated queued requests finish.
            if (!editing) {
                results.replaceChildren();
                const groups = new Map();
                const matchingRows = data.rows.filter(row => (!filter || String(row.team_id) === filter) && (!onlyPending || row.pending || row.reasons.length));
                const calendar = $('sg-calendar'); calendar.hidden = view !== 'calendar'; calendar.replaceChildren();
                if (view === 'calendar') {
                    const weekdays = node('div', 'sg-calendar-weekdays'); ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'].forEach(day => weekdays.append(node('span', '', day))); calendar.append(weekdays);
                    const grid = node('div', 'sg-calendar-grid');
                    const first = new Date(state.year, state.month - 1, 1), count = new Date(state.year, state.month, 0).getDate();
                    for (let i = 0; i < first.getDay(); i++) grid.append(node('span', 'sg-calendar-blank'));
                    for (let day = 1; day <= count; day++) {
                        const date = `${state.year}-${String(state.month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                        const rows = matchingRows.filter(row => row.date === date);
                        const cell = button('', () => { selectedDate = selectedDate === date ? null : date; render(getState()); }, 'sg-calendar-day' + (selectedDate === date ? ' is-selected' : ''));
                        cell.dataset.sgEmpty = String(!rows.length); cell.disabled = !rows.length || blocked || busy; cell.setAttribute('aria-pressed', String(selectedDate === date));
                        cell.append(node('strong', '', day));
                        if (rows.length) { const occurrences = new Set(rows.map(row => row.occurrence_id)).size; const pending = rows.filter(row => row.pending || row.reasons.length).length; cell.append(node('span', '', `${occurrences} ${occurrences === 1 ? 'culto' : 'cultos'}`)); if (pending) cell.append(node('span', 'sg-calendar-pending', `${pending} ${pending === 1 ? 'pendência' : 'pendências'}`)); }
                        grid.append(cell);
                    }
                    calendar.append(grid);
                    calendar.append(node('p', 'sg-calendar-help', selectedDate ? 'Selecione novamente o dia para voltar à visão do mês.' : 'Selecione um dia para ver e editar as equipes.'));
                }
                const visibleRows = view === 'calendar' ? matchingRows.filter(row => selectedDate && row.date === selectedDate) : matchingRows;
                visibleRows.forEach(row => {
                    if (!groups.has(row.occurrence_id)) groups.set(row.occurrence_id, []);
                    groups.get(row.occurrence_id).push(row);
                });
                groups.forEach(rows => {
                    const occurrence = rows[0], group = node('details', 'sg-occurrence');
                    group.open = expandedOccurrences.has(occurrence.occurrence_id);
                    group.addEventListener('toggle', () => { if (group.open) expandedOccurrences.add(occurrence.occurrence_id); else expandedOccurrences.delete(occurrence.occurrence_id); });
                    const heading = node('summary', 'sg-occurrence-heading');
                    const title = node('div'); title.append(node('p', 'sg-date', `${dateLabel(occurrence.date)} · ${occurrence.time}`), node('h3', '', occurrence.name));
                    const badges = node('div', 'sg-occurrence-badges');
                    const pendingTeams = rows.filter(row => row.pending || row.reasons.length).length;
                    if (pendingTeams) badges.append(node('span', 'sg-occurrence-alert', `${pendingTeams} ${pendingTeams === 1 ? 'equipe com pendência' : 'equipes com pendências'}`));
                    badges.append(node('span', 'sg-occurrence-count', `${rows.length} equipe${rows.length === 1 ? '' : 's'}`));
                    const arrow = node('span', 'sg-occurrence-chevron'); arrow.setAttribute('aria-hidden', 'true'); badges.append(arrow);
                    heading.append(title, badges);
                    const cards = node('div', 'sg-occurrence-cards'); group.append(heading, cards); results.append(group);
                    rows.forEach(row => {
                        const card = node('article', 'sg-card'); card.style.setProperty('--team-color', row.color); card.dataset.planId = row.plan_id;
                        const cardHeading = node('header', 'sg-card-heading');
                        const teamName = node('div', 'sg-team-name'); teamName.append(node('span', 'sg-team-dot'), node('strong', 'sg-team-label', row.team));
                        const actions = node('div', 'sg-card-actions');
                        const edit = button('', () => { if (editing) return; editing = row.plan_id; $('sg-edit-context').textContent = `${row.team} · ${dateLabel(row.date)} às ${row.time}`; $('sg-edit-content').replaceChildren(editForm(row)); $('sg-edit-dialog').showModal(); setControls(); }, 'sg-edit-icon');
                        edit.setAttribute('aria-label', `Editar integrantes de ${row.team}`); edit.title = 'Editar integrantes';
                        const icon = node('i', 'fas fa-pen'); icon.setAttribute('aria-hidden', 'true'); edit.append(icon);
                        actions.append(node('span', 'sg-capacity' + (row.pending ? ' is-pending' : ''), `${row.filled}/${row.required} vagas`), edit);
                        cardHeading.append(teamName, actions); card.append(cardHeading);
                        const people = node('ul', 'sg-assignees');
                        row.people.forEach(person => {
                            const line = node('li'), initials = person.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toLocaleUpperCase('pt-BR');
                            const avatar = node('span', 'sg-avatar', initials); avatar.setAttribute('aria-hidden', 'true');
                            const info = node('div', 'sg-person-info'); info.append(node('strong', '', person.name), node('span', '', person.experience_label));
                            const activities = node('div', 'sg-person-tags');
                            const names = row.activities.map(a => a.name);
                            if (names.length) names.forEach(name => activities.append(node('span', 'sg-activity-tag', name)));
                            else activities.append(node('span', 'sg-activity-tag', 'Atividades a definir'));
                            activities.setAttribute('aria-label', 'Atividades compartilhadas da equipe');
                            info.append(activities);
                            line.append(avatar, info);
                            if (person.locked) line.append(node('span', 'sg-lock', 'Bloqueado'));
                            people.append(line);
                        });
                        if (row.people.length) card.append(people);
                        else card.append(node('p', 'sg-empty', data.generated ? 'Sem integrantes sugeridos.' : 'Aguardando geração ou seleção manual.'));
                        const details = node('details', 'sg-card-details'); details.append(node('summary', '', 'Atividades e detalhes'));
                        details.append(node('p', 'sg-activities', 'Atividades: ' + (row.activities.map(a => a.name).join(' · ') || 'A definir')), node('p', '', `Acompanhamento mínimo: ${row.minimum_mentors || 'configurar na etapa 1'}`));
                        if (row.notes) details.append(node('p', '', row.notes));
                        if (!row.people.length || row.notes) card.append(details);
                        if (data.generated && row.reasons.length) { const warnings = node('details', 'sg-card-warning'); const warningHeading = node('summary'); const warningIcon = node('span', 'sg-warning-icon', '!'); warningIcon.setAttribute('aria-hidden', 'true'); const chevron = node('span', 'sg-warning-chevron'); chevron.setAttribute('aria-hidden', 'true'); warningHeading.append(warningIcon, node('span', '', `${row.reasons.length} ${row.reasons.length === 1 ? 'pendência para revisar' : 'pendências para revisar'}`), chevron); warnings.append(warningHeading); const list = node('ul', 'sg-warnings'); row.reasons.forEach(reason => list.append(node('li', '', reason))); warnings.append(list); card.append(warnings); }
                        cards.append(card);
                    });
                });
                if (!data.rows.length) results.append(node('p', 'sa-empty', 'Nenhuma equipe planejada. Volte à etapa 1 para configurar os cultos.'));
                else if (!matchingRows.length) results.append(node('p', 'sa-empty', onlyPending ? 'Nenhuma pendência encontrada para este filtro.' : 'Nenhuma equipe encontrada para este filtro.'));
            }
            $('sg-people').replaceChildren(...summary.people.map(p => { const tr = node('tr'); const name = node('th', 'sg-table-name', p.name); name.scope = 'row'; tr.append(name); [p.services, p.team_assignments, p.mentor_assignments].forEach(value => { const cell = node('td'); cell.append(node('span', 'sg-table-count', value)); tr.append(cell); }); return tr; }));
            $('sg-consecutive').replaceChildren(...(summary.consecutive.length ? summary.consecutive.map(c => { const item = node('div', 'sg-consecutive-item'); const person = node('div'); person.append(node('strong', '', c.member), node('span', '', c.team)); item.append(person, node('span', 'sg-consecutive-dates', `${dateLabel(c.previous_date)} · ${dateLabel(c.date)}`)); return item; }) : [node('p', 'sg-consecutive-empty', 'Nenhuma atuação consecutiva identificada.')])) ;
            $('sg-solver').replaceChildren();
            if (data.generated) $('sg-solver').append(node('p', '', data.report.fully_optimized ? 'Todos os critérios foram otimizados na ordem informada.' : 'Foi preservada uma solução válida dentro do limite de resolução. Os critérios restantes podem admitir melhorias.'));
            setControls();
        }
        $('sg-generate').addEventListener('click', async () => { if (busy || blocked) return; if (!await save()) return; editing = null; busy = true; setControls(); $('sg-status').textContent = 'Avaliando todas as ocorrências do mês…'; const success = await onGenerate(); busy = false; $('sg-status').textContent = success ? 'Sugestões geradas e salvas em rascunho. Revise as composições e pendências.' : 'A geração não foi concluída. O rascunho anterior foi preservado.'; render(getState()); });
        document.addEventListener('click', event => { const menu = host.querySelector('.sg-filter-menu'); if (menu.open && !menu.contains(event.target)) menu.open = false; });
        host.querySelectorAll('[data-sg-filter], [data-sg-view]').forEach(control => control.addEventListener('click', async () => {
            if (dirty && !await save()) return;
            if (control.dataset.sgFilter) onlyPending = control.dataset.sgFilter === 'pending';
            if (control.dataset.sgView) { view = control.dataset.sgView; selectedDate = null; }
            host.querySelectorAll('[data-sg-filter]').forEach(item => item.setAttribute('aria-pressed', String((item.dataset.sgFilter === 'pending') === onlyPending)));
            host.querySelectorAll('[data-sg-view]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.sgView === view)));
            render(getState());
        }));
        $('sg-team').addEventListener('change', async () => { const chosen = $('sg-team').value; if (dirty && !await save()) { $('sg-team').value = filter; return; } editing = null; filter = chosen; render(getState()); });
        return {render, save, isDirty: () => dirty, setBlocked(value) { blocked = value; setControls(); }};
    }};
})();
