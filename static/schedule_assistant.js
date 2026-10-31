(function () {
    'use strict';
    function calendarDays(year, month) {
        const first = new Date(year, month - 1, 1);
        return Array.from({length: 42}, (_, i) => {
            const d = new Date(year, month - 1, i - first.getDay() + 1);
            return {date: [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-'), number: d.getDate(), inMonth: d.getMonth() === month - 1};
        });
    }
    function serializeTeamForms(form) {
        return Array.from(form.querySelectorAll('[data-team-id]')).map(card => ({team_id: Number(card.dataset.teamId), member_count: Number(card.querySelector('[data-count]').value), minimum_mentors: card.querySelector('[data-minimum-mentors]')?.value || null, activity_ids: Array.from(card.querySelectorAll('[data-activity]:checked')).map(input => Number(input.value)), notes: card.querySelector('[data-team-notes]').value}));
    }
    if (typeof module !== 'undefined') module.exports = {calendarDays, serializeTeamForms};
    if (typeof document === 'undefined') return;
    const root = document.getElementById('schedule-assistant');
    if (!root) return;
    let state = JSON.parse(document.getElementById('schedule-assistant-data').textContent);
    let selected = null, view = 'calendar', queue = Promise.resolve(), timer = null;
    let availabilityUI = null, generationUI = null;
    let dirty = false, pending = 0, blocked = false, failed = false, lastFailed = null, leaving = false;
    const $ = id => document.getElementById(id), status = $('sa-save-status');
    const csrf = $('assistant-csrf').querySelector('input').value;
    const dom = (tag, className, content) => { const node = document.createElement(tag); if (className) node.className = className; if (content !== undefined) node.textContent = content; return node; };
    const button = (label, cls, fn) => { const node = dom('button', cls, label); node.type = 'button'; if (fn) node.addEventListener('click', fn); return node; };
    const fmtDate = date => new Intl.DateTimeFormat('pt-BR', {day: '2-digit', month: 'short', timeZone: 'America/Sao_Paulo'}).format(new Date(date + 'T12:00:00-03:00'));
    const current = () => state.occurrences.find(item => item.id === selected);
    function showError(message, conflict = false, retry = false) {
        root.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
        $('sa-error').hidden = false; $('sa-error').querySelector('p').textContent = message;
        $('sa-reload').hidden = !conflict; $('sa-retry').hidden = !retry;
        status.textContent = conflict ? 'Conflito de edição' : 'Alterações não salvas';
        if (conflict) {
            blocked = true;
            availabilityUI?.setBlocked(true); generationUI?.setBlocked(true);
            root.querySelectorAll('.sa-editor-form input, .sa-editor-form textarea, .sa-editor-form button, .sa-editor-form select').forEach(node => { node.disabled = true; });
        }
        $('sa-error').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function updateStatus() { if (!blocked && !failed && !lastFailed) status.textContent = dirty || pending ? 'Salvando alterações…' : availabilityUI?.isDirty() ? 'Disponibilidade ainda não salva' : generationUI?.isDirty() ? 'Composição ainda não salva' : 'Todas as alterações salvas'; }
    function send(action, payload = {}) {
        pending++; updateStatus();
        const work = queue.then(async () => {
            if (blocked || failed) return false;
            try {
                const response = await fetch(root.dataset.apiUrl, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({...payload, action, version: state.version})});
                if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Sua sessão expirou. Entre novamente antes de continuar.');
                const data = await response.json();
                if (!response.ok) {
                    lastFailed = {action, payload}; failed = response.status >= 500;
                    showError(data.error || 'Não foi possível salvar.', response.status === 409, response.status !== 409);
                    return false;
                }
                if (data.discarded) { leaving = true; dirty = false; window.location.assign(data.redirect_url); return true; }
                state = data; lastFailed = null; $('sa-error').hidden = true; availabilityUI?.render(state); generationUI?.render(state); renderCalendar(); renderStep(); renderReview();
                if ($('sa-editor-heading') && current()) $('sa-editor-heading').textContent = current().name;
                return true;
            } catch (error) {
                failed = true; lastFailed = {action, payload}; showError(error.message || 'Falha de conexão. Mantenha a página aberta e tente salvar novamente.', false, true); return false;
            }
        });
        queue = work.catch(() => false).finally(() => { pending--; updateStatus(); });
        return work;
    }
    function editorPayload() {
        const form = $('sa-editor-form'); if (!form || !form.reportValidity()) return null;
        return {occurrence_id: Number(form.dataset.occurrenceId), name: form.elements.name.value, date: form.elements.date.value, time: form.elements.time.value, notes: form.elements.notes.value, teams: serializeTeamForms(form), apply_to: Array.from(form.querySelectorAll('[data-batch]:checked')).map(input => Number(input.value))};
    }
    async function flush() {
        clearTimeout(timer); timer = null;
        if (blocked || failed) return false;
        if (dirty) {
            const payload = editorPayload();
            if (!payload) { showError('Revise os campos destacados antes de continuar.'); return false; }
            dirty = false;
            if (!await send('save_occurrence', payload)) { dirty = true; return false; }
        }
        if (availabilityUI?.isDirty() && !await availabilityUI.save()) return false;
        if (generationUI?.isDirty() && !await generationUI.save()) return false;
        await queue; return !blocked && !failed;
    }
    function scheduleSave() { dirty = true; updateStatus(); clearTimeout(timer); timer = setTimeout(flush, 700); }
    function renderReview() {
        $('sa-review').hidden = !state.availability_review.length;
        $('sa-review').replaceChildren();
        if (state.availability_review.length) {
            $('sa-review').append(dom('p', '', 'O planejamento mudou após o registro das disponibilidades. Os dados válidos foram preservados. Revise:'));
            const list = dom('ul'); state.availability_review.forEach(item => list.append(dom('li', '', `${item.name}: ${item.reason}`))); $('sa-review').append(list);
        }
    }
    function teamTag(team) {
        const tag = dom('span', 'sa-team-tag'), dot = dom('span', 'sa-dot'); dot.style.backgroundColor = team.color;
        tag.append(dot, dom('span', 'sa-team-label', `${team.name} · ${team.member_count}`));
        tag.title = `${team.name}: ${team.member_count} integrantes${team.activities.length ? ' · ' + team.activities.map(item => item.name).join(', ') : ''}`; return tag;
    }
    function occurrenceButton(item, list = false) {
        const node = button('', (list ? 'sa-list-occurrence' : 'sa-occurrence') + (selected === item.id ? ' is-selected' : ''), () => selectOccurrence(item.id));
        node.setAttribute('aria-label', `${item.name}, ${fmtDate(item.date)}, ${item.time}. ${item.teams.map(team => `${team.name}, ${team.member_count} integrantes, atividades: ${team.activities.map(activity => activity.name).join(', ') || 'a definir'}`).join('. ')}`);
        node.setAttribute('aria-pressed', String(selected === item.id));
        const time = dom('time', 'sa-occurrence-time', item.time); time.dateTime = item.date + 'T' + item.time;
        const content = dom('span'); content.append(dom('strong', 'sa-occurrence-name', item.name));
        const teams = dom('span', 'sa-occurrence-teams'); item.teams.forEach(team => teams.append(teamTag(team)));
        if (!item.teams.length) teams.append(dom('span', 'sa-team-empty', 'Sem equipes'));
        content.append(teams);
        const activities = [...new Set(item.teams.flatMap(team => team.activities.map(activity => activity.name)))];
        if (activities.length) content.append(dom('span', 'sa-occurrence-activities', activities.join(' · ')));
        if (item.availability_review_required) content.append(dom('span', 'sa-review-tag', 'Revisar'));
        node.append(time, content); return node;
    }
    function renderCalendar() {
        const grid = $('sa-calendar'); grid.replaceChildren();
        const ordered = [...state.occurrences].sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Sao_Paulo'}).format(new Date());
        const allDays = calendarDays(state.year, state.month);
        // Keep the calendar visually compact: render only the complete weeks
        // needed to include the last day of the selected month.
        const lastMonthDay = allDays.reduce((last, day, index) => day.inMonth ? index : last, 0);
        allDays.slice(0, Math.ceil((lastMonthDay + 1) / 7) * 7).forEach(day => {
            const cell = dom('div', 'sa-day' + (!day.inMonth ? ' is-outside' : '') + (day.date === today ? ' is-today' : ''));
            cell.append(dom('span', 'sa-day-number', String(day.number)));
            if (day.inMonth) ordered.filter(item => item.date === day.date).forEach(item => cell.append(occurrenceButton(item)));
            grid.append(cell);
        });
        const agenda = $('sa-agenda-list'); agenda.replaceChildren(); let lastDate;
        ordered.forEach(item => { if (lastDate !== item.date) { agenda.append(dom('h3', 'sa-list-date', fmtDate(item.date))); lastDate = item.date; } agenda.append(occurrenceButton(item, true)); });
        if (!ordered.length) agenda.append(dom('p', 'sa-empty', 'Nenhuma ocorrência neste planejamento. Adicione um culto ou evento.'));
        $('sa-summary').textContent = `${ordered.length} ocorrência${ordered.length === 1 ? '' : 's'} · ${new Set(ordered.flatMap(item => item.teams.map(team => team.team_id))).size} equipes`;
        const legend = $('sa-legend'); legend.replaceChildren(); const unique = new Map();
        ordered.forEach(item => item.teams.forEach(team => unique.set(team.team_id, team)));
        unique.forEach(team => { const node = dom('span', 'sa-legend-item'), dot = dom('span', 'sa-dot'); dot.style.backgroundColor = team.color; node.append(dot, dom('span', '', team.name)); legend.append(node); });
        if (!unique.size) legend.append(dom('span', '', 'Selecione um culto para incluir suas equipes.'));
        const warnings = $('sa-warnings');
        if (warnings) {
            warnings.hidden = !state.warnings.length;
            warnings.querySelector('ul').replaceChildren(...state.warnings.map(item => dom('li', '', item)));
        }
        $('sa-weekdays').hidden = view !== 'calendar'; grid.hidden = view !== 'calendar'; agenda.hidden = view !== 'list';
    }
    function dateBounds(input) { input.min = `${state.year}-${String(state.month).padStart(2, '0')}-01`; input.max = `${state.year}-${String(state.month).padStart(2, '0')}-${new Date(state.year, state.month, 0).getDate()}`; }
    function field(labelText, name, value, type = 'text') {
        const box = dom('div', 'sa-stack'), label = dom('label', '', labelText), input = dom(type === 'textarea' ? 'textarea' : 'input');
        input.name = name; input.id = 'sa-edit-' + name; input.value = value || ''; label.htmlFor = input.id;
        if (type !== 'textarea') input.type = type;
        if (['name', 'date', 'time'].includes(name)) input.required = true;
        if (name === 'name') input.maxLength = 255;
        if (type === 'textarea') { input.maxLength = 5000; input.rows = 2; }
        if (type === 'date') dateBounds(input);
        box.append(label, input); return box;
    }
    function teamCard(team) {
        const card = dom('section', 'sa-team-card'); card.dataset.teamId = team.team_id; card.style.setProperty('--team-color', team.color);
        const heading = dom('div', 'sa-team-card-head'); heading.append(dom('strong', '', team.name), button('Retirar', '', () => { card.remove(); refreshAddTeams(); scheduleSave(); })); card.append(heading);
        const count = dom('label', 'sa-stack', 'Integrantes necessários'), input = dom('input'); input.type = 'number'; input.min = '1'; input.max = '32767'; input.required = true; input.value = team.member_count; input.dataset.count = ''; count.append(input); card.append(count);
        const mentorLabel = dom('label', 'sa-stack', 'Mínimo de integrantes aptos a acompanhar'), mentorInput = dom('input');
        mentorInput.type = 'number'; mentorInput.min = '1'; mentorInput.max = input.value; mentorInput.dataset.minimumMentors = '';
        mentorInput.value = team.minimum_mentors || ''; mentorInput.placeholder = 'Defina para quantidades diferentes de dois';
        const syncPolicy = () => { mentorInput.max = input.value; mentorInput.disabled = Number(input.value) === 2; mentorLabel.hidden = Number(input.value) === 2; };
        input.addEventListener('input', syncPolicy); syncPolicy(); mentorLabel.append(mentorInput); card.append(mentorLabel);
        const activities = new Map((state.teams.find(item => item.id === team.team_id)?.activities || []).map(item => [item.id, item])); team.activities.forEach(item => activities.set(item.id, item));
        const fieldset = dom('fieldset'); fieldset.append(dom('legend', '', 'Atividades compartilhadas'));
        const checks = dom('div', 'sa-team-activities');
        activities.forEach(activity => { const label = dom('label', 'sa-check-label'), check = dom('input'); check.type = 'checkbox'; check.value = activity.id; check.dataset.activity = ''; check.checked = team.activities.some(item => item.id === activity.id); label.append(check, dom('span', '', activity.name)); checks.append(label); });
        if (!activities.size) checks.append(dom('span', '', 'Nenhuma atividade cadastrada para esta equipe.'));
        fieldset.append(checks); card.append(fieldset);
        const note = dom('label', 'sa-stack', 'Observações da equipe'), area = dom('textarea'); area.rows = 2; area.maxLength = 5000; area.dataset.teamNotes = ''; area.value = team.notes; note.append(area); card.append(note); return card;
    }
    function refreshAddTeams() {
        const select = $('sa-add-team-select'); if (!select) return;
        const included = Array.from($('sa-team-cards').children).map(node => Number(node.dataset.teamId)); select.replaceChildren();
        const empty = dom('option', '', 'Adicionar equipe…'); empty.value = ''; select.append(empty);
        state.teams.filter(item => !included.includes(item.id)).forEach(item => { const option = dom('option', '', item.name); option.value = item.id; select.append(option); });
    }
    async function selectOccurrence(id) {
        if (!await flush()) return; selected = id; renderCalendar(); renderEditor();
        const dialog = $('sa-editor-dialog');
        if (!dialog.open) dialog.showModal();
        $('sa-editor-heading').focus({preventScroll: true});
    }
    async function closeEditor() {
        if (!await flush()) return;
        $('sa-editor-dialog').close();
    }
    function renderEditor() {
        const occurrence = current(), editor = $('sa-editor'); if (!occurrence) return; editor.replaceChildren();
        const heading = dom('header', 'sa-editor-head'), title = dom('h3', '', occurrence.name); title.id = 'sa-editor-heading'; title.tabIndex = -1;
        const headingCopy = dom('div'); headingCopy.append(title, dom('p', '', occurrence.source_label ? 'Agenda: ' + occurrence.source_label : 'Ocorrência adicional · somente neste planejamento'));
        const close = button('×', 'sa-editor-close', closeEditor); close.setAttribute('aria-label', 'Fechar detalhes da ocorrência');
        heading.append(headingCopy, close); editor.append(heading);
        const form = dom('form', 'sa-editor-form'); form.id = 'sa-editor-form'; form.dataset.occurrenceId = occurrence.id;
        form.addEventListener('submit', event => { event.preventDefault(); flush(); }); form.append(field('Nome da ocorrência', 'name', occurrence.name));
        const pair = dom('div', 'sa-form-pair'); pair.append(field('Data', 'date', occurrence.date, 'date'), field('Horário', 'time', occurrence.time, 'time')); form.append(pair, field('Observações da ocorrência', 'notes', occurrence.notes, 'textarea'));
        const teams = dom('section', 'sa-stack sa-teams'); teams.append(dom('h3', '', 'Equipes neste culto'));
        const cards = dom('div', 'sa-stack'); cards.id = 'sa-team-cards'; occurrence.teams.forEach(team => cards.append(teamCard(team))); teams.append(cards);
        const add = dom('div', 'sa-add-team'), select = dom('select'); select.id = 'sa-add-team-select'; select.setAttribute('aria-label', 'Equipe para adicionar');
        add.append(select, button('+', '', () => { const team = state.teams.find(item => item.id === Number(select.value)); if (!team) return; cards.append(teamCard({team_id: team.id, name: team.name, color: team.color, member_count: 2, activities: team.activities, notes: ''})); refreshAddTeams(); scheduleSave(); }));
        add.lastChild.setAttribute('aria-label', 'Adicionar equipe selecionada'); teams.append(add); form.append(teams);
        const batch = dom('details', 'sa-batch'); batch.append(dom('summary', '', 'Aplicar equipes em outras datas'), dom('p', '', 'A composição, as quantidades, atividades e observações das equipes substituirão as equipes das ocorrências selecionadas. Datas e horários serão mantidos.'));
        const choices = dom('div', 'sa-batch-list');
        state.occurrences.filter(item => item.id !== occurrence.id).forEach(item => { const label = dom('label', 'sa-check-label'), checkbox = dom('input'); checkbox.type = 'checkbox'; checkbox.value = item.id; checkbox.dataset.batch = ''; label.append(checkbox, dom('span', '', `${fmtDate(item.date)} · ${item.time} · ${item.name}`)); choices.append(label); }); batch.append(choices); form.append(batch);
        form.append(button('Remover ocorrência do planejamento', 'sa-editor-remove', async () => {
            if (!window.confirm('Retirar esta ocorrência do planejamento? O evento original da agenda será preservado.')) return;
            if (!await flush()) return;
            if (await send('remove_occurrence', {occurrence_id: occurrence.id})) { selected = null; $('sa-editor-dialog').close(); }
        }));
        const changed = event => { if (event.target.id !== 'sa-add-team-select') scheduleSave(); };
        form.addEventListener('input', changed); form.addEventListener('change', changed); editor.append(form); refreshAddTeams();
    }
    function renderStep() {
        const step = state.current_step;
        root.querySelectorAll('[data-step]').forEach(node => { if (Number(node.dataset.step) === step) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current'); });
        $('sa-planning').hidden = step !== 1; $('sa-availability').hidden = step !== 2; $('sa-generation').hidden = step !== 3;
        $('sa-prev').hidden = step === 1; $('sa-next').hidden = step === 3;
    }
    async function navigate(step) {
        if (!await flush()) return;
        if (step === 3 && state.availability.summary.warnings.length && !window.confirm('Há pendências de disponibilidade ou acompanhamento no resumo. Deseja prosseguir mantendo esses avisos? Nenhuma disponibilidade desconhecida será confirmada.')) return;
        await send('navigate', {step});
    }
    root.querySelectorAll('[data-step]').forEach(node => node.addEventListener('click', () => navigate(Number(node.dataset.step))));
    $('sa-prev').addEventListener('click', () => navigate(state.current_step - 1)); $('sa-next').addEventListener('click', () => navigate(state.current_step + 1));
    root.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => { view = node.dataset.view; root.querySelectorAll('[data-view]').forEach(item => item.setAttribute('aria-pressed', String(item === node))); renderCalendar(); }));
    root.querySelectorAll('[data-close-dialog]').forEach(node => node.addEventListener('click', () => node.closest('dialog').close()));
    $('sa-editor-dialog').addEventListener('cancel', event => { event.preventDefault(); closeEditor(); });
    $('sa-add').addEventListener('click', async () => {
        if (!await flush()) return;
        const select = $('sa-source'); select.replaceChildren(); const option = dom('option', '', 'Selecione uma ocorrência da agenda'); option.value = ''; select.append(option);
        const included = new Set(state.occurrences.map(item => item.source_key));
        state.sources.forEach(item => { const node = dom('option', '', item.label + (included.has(item.key) ? ' · Já incluída' : '')); node.value = item.key; node.disabled = included.has(item.key); select.append(node); });
        const events = $('sa-manual-event'); events.replaceChildren();
        const unlinked = dom('option', '', 'Somente neste planejamento'); unlinked.value = ''; events.append(unlinked);
        state.events.forEach(item => { const option = dom('option', '', item.label); option.value = item.id; events.append(option); });
        dateBounds($('sa-manual-date')); $('sa-add-dialog').showModal();
    });
    async function submitAdd(form, action, payload, predicate) {
        const buttons = $('sa-add-dialog').querySelectorAll('button[type=submit]'); buttons.forEach(node => { node.disabled = true; });
        try { if (await send(action, payload)) { $('sa-add-dialog').close(); form.reset(); const item = state.occurrences.find(predicate); if (item) await selectOccurrence(item.id); } }
        finally { buttons.forEach(node => { node.disabled = false; }); }
    }
    $('sa-source-form').addEventListener('submit', event => { event.preventDefault(); const key = $('sa-source').value; submitAdd(event.currentTarget, 'add_source', {source_key: key}, item => item.source_key === key); });
    $('sa-manual-form').addEventListener('submit', event => { event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget)); submitAdd(event.currentTarget, 'add_manual', data, item => item.date === data.date && item.time === data.time && item.name === data.name); });
    $('sa-discard').addEventListener('click', () => { if (!blocked && !failed) { clearTimeout(timer); $('sa-discard-dialog').showModal(); } });
    $('sa-discard-dialog').addEventListener('close', () => { if (dirty && !leaving) scheduleSave(); });
    $('sa-confirm-discard').addEventListener('click', async () => { clearTimeout(timer); dirty = false; $('sa-confirm-discard').disabled = true; if (!await send('discard', {confirmed: true})) { $('sa-confirm-discard').disabled = false; $('sa-discard-dialog').close(); } });
    $('sa-reload').addEventListener('click', () => { if (window.confirm('Recarregar o rascunho? Alterações locais ainda não salvas serão descartadas.')) { leaving = true; window.location.reload(); } });
    $('sa-retry').addEventListener('click', async () => { failed = false; if (!lastFailed) return; const retry = lastFailed; if (retry.action === 'save_occurrence') { dirty = true; await flush(); } else if (retry.action === 'save_availability') await availabilityUI.retrySave(retry.payload); else if (retry.action === 'clear_availability') { if (await send(retry.action, retry.payload)) availabilityUI.cleared(retry.payload); } else await send(retry.action, retry.payload); });
    window.addEventListener('beforeunload', event => { if (!leaving && (dirty || availabilityUI?.isDirty() || generationUI?.isDirty() || pending || failed)) { event.preventDefault(); event.returnValue = ''; } });
    availabilityUI = window.ScheduleAvailabilityUI.create({getState: () => state, onSave: payload => send('save_availability', payload), onClear: payload => send('clear_availability', payload), onDirty: updateStatus});
    generationUI = window.ScheduleGenerationUI.create({getState: () => state, onDirty: updateStatus,
        onSave: payload => send('set_assignments', payload),
        onRemoveObsolete: assignment_id => send('remove_obsolete_assignment', {assignment_id}),
        onGenerate: async () => {
            if (!await flush()) return false;
            if (!window.confirm('Gerar sugestões para todo o mês? Designações desbloqueadas serão recalculadas; as bloqueadas serão preservadas. A escala continuará em rascunho.')) return false;
            return send('generate', {confirmed: true});
        }});
    generationUI.render(state);
    renderCalendar(); renderStep(); renderReview(); updateStatus();
})();
