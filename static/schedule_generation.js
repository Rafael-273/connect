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
        let editing = null, dirty = false, busy = false, blocked = false, filter = '';
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
            if (success) { dirty = false; editing = null; onDirty(); $('sg-status').textContent = 'Composição salva. Os bloqueios serão preservados ao regenerar.'; }
            else $('sg-status').textContent = 'Composição não salva. Corrija as pendências indicadas acima.';
            render(getState()); return success;
        }
        function setControls() {
            host.querySelectorAll('button, input, select').forEach(n => { n.disabled = busy || blocked; });
        }
        function editForm(row) {
            const form = node('form', 'sg-edit'); form.id = 'sg-edit-form';
            form.append(node('p', '', 'Selecione os integrantes e confirme os bloqueios. Novas escolhas ficam bloqueadas por padrão. Desbloqueie somente o que pode ser recalculado.'));
            const all = new Map(row.candidates.map(c => [c.id, c]));
            row.people.forEach(p => { if (!all.has(p.member_id)) all.set(p.member_id, {id: p.member_id, name: p.name + ' · precisa de revisão', experience: p.experience}); });
            all.forEach(person => {
                const existing = row.people.find(p => p.member_id === person.id), line = node('div', 'sg-choice');
                const label = node('label'), selected = node('input'); selected.type = 'checkbox'; selected.value = person.id; selected.dataset.member = ''; selected.checked = !!existing;
                label.append(selected, node('span', '', `${person.name} · ${labels[person.experience] || person.experience}`));
                const lockLabel = node('label'), lock = node('input'); lock.type = 'checkbox'; lock.value = person.id; lock.dataset.lock = ''; lock.checked = existing ? existing.locked : true;
                lockLabel.append(lock, node('span', '', 'Bloquear')); line.append(label, lockLabel); form.append(line);
            });
            form.addEventListener('change', () => { dirty = true; onDirty(); $('sg-status').textContent = 'Composição alterada; confirme para validar e salvar.'; });
            form.append(button('Confirmar composição', save, 'org-btn-primary'), button('Cancelar edição', () => { if (dirty && !window.confirm('Descartar as alterações desta composição ainda não salvas?')) return; dirty = false; editing = null; onDirty(); render(getState()); }));
            form.addEventListener('submit', event => { event.preventDefault(); save(); });
            return form;
        }
        function render(state) {
            if (busy) return;
            if (editing && !dirty) editing = null;
            const data = state.generation, summary = data.summary;
            $('sg-stale').hidden = !data.stale;
            $('sg-generate').textContent = data.generated ? 'Regenerar sugestões' : 'Gerar sugestões';
            const metrics = [['Vagas preenchidas', summary.filled], ['Vagas pendentes', summary.pending], ['Designações bloqueadas', summary.locked]];
            $('sg-summary').replaceChildren(...metrics.map(([label, value]) => { const box = node('div', 'sg-stat'); box.append(node('strong', '', value), node('span', '', label)); return box; }));
            const teamSelect = $('sg-team'), previous = filter;
            teamSelect.replaceChildren(); const option = node('option', '', 'Todas as equipes · visão mensal'); option.value = ''; teamSelect.append(option);
            new Map(data.rows.map(row => [String(row.team_id), row.team])).forEach((name, id) => { const o = node('option', '', name); o.value = id; teamSelect.append(o); });
            filter = Array.from(teamSelect.options).some(o => o.value === previous) ? previous : ''; teamSelect.value = filter;
            const obsolete = $('sg-obsolete'); obsolete.hidden = !data.obsolete.length;
            obsolete.replaceChildren(node('strong', '', 'Designações que precisam ser retiradas'));
            data.obsolete.forEach(item => { const line = node('p'); line.append(node('span', '', `${item.name}${item.locked ? ' · bloqueado' : ''}: ${item.reason} `), button('Remover', async () => { if (window.confirm('Remover esta designação do rascunho?')) await onRemoveObsolete(item.id); })); obsolete.append(line); });
            const results = $('sg-results');
            // Keep an unsaved composition in place while unrelated queued requests finish.
            if (!editing) {
                results.replaceChildren();
                data.rows.filter(row => !filter || String(row.team_id) === filter).forEach(row => {
                    const card = node('article', 'sg-card'); card.style.setProperty('--team-color', row.color); card.dataset.planId = row.plan_id;
                    const heading = node('header'); heading.append(node('p', 'sg-date', `${dateLabel(row.date)} · ${row.time}`), node('h3', '', row.name), node('strong', 'sg-team-label', row.team)); card.append(heading);
                    card.append(node('p', 'sg-activities', 'Atividades: ' + (row.activities.map(a => a.name).join(' · ') || 'A definir')), node('p', '', `${row.filled}/${row.required} vagas · acompanhamento mínimo: ${row.minimum_mentors || 'configurar na etapa 1'}`));
                    if (row.notes) card.append(node('p', '', row.notes));
                    const people = node('ul', 'sg-assignees');
                    row.people.forEach(person => { const line = node('li'); line.append(node('strong', '', person.name), node('span', '', person.experience_label), node('span', person.locked ? 'sg-lock' : '', person.locked ? 'Bloqueado' : 'Pode ser recalculado')); people.append(line); }); card.append(people);
                    if (!row.people.length) card.append(node('p', 'sg-empty', data.generated ? 'Sem composição válida sugerida.' : 'Aguardando geração ou seleção manual.'));
                    if (row.reasons.length) { const warnings = node('ul', 'sg-warnings'); row.reasons.forEach(reason => warnings.append(node('li', '', reason))); card.append(warnings); }
                    card.append(button('Editar integrantes e bloqueios', () => { if (editing) return; editing = row.plan_id; card.append(editForm(row)); setControls(); card.querySelector('form input, form button')?.focus(); })); results.append(card);
                });
                if (!data.rows.length) results.append(node('p', 'sa-empty', 'Nenhuma equipe planejada. Volte à etapa 1 para configurar os cultos.'));
            }
            $('sg-people').replaceChildren(...summary.people.map(p => { const tr = node('tr'); [p.name, p.services, p.team_assignments, p.mentor_assignments].forEach(value => tr.append(node('td', '', value))); return tr; }));
            $('sg-consecutive').replaceChildren(...(summary.consecutive.length ? summary.consecutive.map(c => node('p', '', `${c.member} · ${c.team}: ${dateLabel(c.previous_date)} e ${dateLabel(c.date)}.`)) : [node('p', '', 'Nenhuma atuação consecutiva da mesma equipe identificada no rascunho.')]));
            $('sg-solver').replaceChildren();
            if (data.generated) $('sg-solver').append(node('p', '', data.report.fully_optimized ? 'Todos os critérios foram otimizados na ordem informada.' : 'Foi preservada uma solução válida dentro do limite de resolução. Os critérios restantes podem admitir melhorias.'));
            setControls();
        }
        $('sg-generate').addEventListener('click', async () => { if (busy || blocked) return; if (!await save()) return; editing = null; busy = true; setControls(); $('sg-status').textContent = 'Avaliando todas as ocorrências do mês…'; const success = await onGenerate(); busy = false; $('sg-status').textContent = success ? 'Sugestões geradas e salvas em rascunho. Revise as composições e pendências.' : 'A geração não foi concluída. O rascunho anterior foi preservado.'; render(getState()); });
        $('sg-team').addEventListener('change', async () => { const chosen = $('sg-team').value; if (dirty && !await save()) { $('sg-team').value = filter; return; } editing = null; filter = chosen; render(getState()); });
        return {render, save, isDirty: () => dirty, setBlocked(value) { blocked = value; setControls(); }};
    }};
})();
