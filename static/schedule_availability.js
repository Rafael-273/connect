(function (scope) {
    'use strict';
    const LABELS = {available: 'Disponível', unavailable: 'Indisponível', unknown: 'Não informado'};
    function proposedStatus(mode, selected, id) {
        if (!['all', 'only', 'except'].includes(mode)) return 'unknown';
        const checked = selected.includes(id);
        return mode === 'all' || (mode === 'only' && checked) || (mode === 'except' && !checked) ? 'available' : 'unavailable';
    }
    function summaryText(name, team, mode, selected, occurrences) {
        if (!mode) return `A disponibilidade de ${name} ainda não foi informada.`;
        if (!occurrences.length) return 'Esta equipe não possui ocorrências neste planejamento. Nenhuma disponibilidade será confirmada.';
        const selectedRows = occurrences.filter(item => selected.includes(item.id));
        const available = occurrences.filter(item => proposedStatus(mode, selected, item.id) === 'available').length;
        const labels = selectedRows.map(item => `${item.date.slice(8, 10)}/${item.date.slice(5, 7)} às ${item.time} (${item.name})`).join('; ');
        if (!available) return `${name} confirmou indisponibilidade em todas as ${occurrences.length} ocorrências de ${team} neste mês.`;
        if (mode === 'all' || (mode === 'except' && !selectedRows.length)) return `${name} está disponível em todas as ${occurrences.length} ocorrências de ${team} neste mês.`;
        if (mode === 'only') return `${name} está disponível somente em: ${labels}. Total: ${available} de ${occurrences.length} ocorrências de ${team}.`;
        return `${name} está disponível em todas as ocorrências de ${team}, exceto em: ${labels}. Total disponível: ${available} de ${occurrences.length}.`;
    }
    if (typeof module !== 'undefined') module.exports = {proposedStatus, summaryText};
    if (typeof document === 'undefined') return;
    const $ = id => document.getElementById(id);
    const node = (tag, cls, text) => { const result = document.createElement(tag); if (cls) result.className = cls; if (text !== undefined) result.textContent = text; return result; };
    const action = (text, handler) => { const result = node('button', '', text); result.type = 'button'; result.addEventListener('click', handler); return result; };
    const dateLabel = value => new Intl.DateTimeFormat('pt-BR', {weekday: 'long', day: '2-digit', month: 'short', timeZone: 'America/Sao_Paulo'}).format(new Date(value + 'T12:00:00-03:00'));
    class AvailabilityUI {
        constructor(options) {
            this.options = options; this.state = options.getState(); this.teamId = null; this.memberId = null;
            this.dirty = false; this.busy = false; this.blocked = false; this.mode = ''; this.selection = new Set(); this.saving = null;
            $('av-team').addEventListener('change', () => {
                const requested = Number($('av-team').value);
                if (!this.canLeave()) { $('av-team').value = this.teamId; return; }
                this.teamId = requested; this.memberId = null; this.render(this.state);
            });
            $('av-search').addEventListener('input', () => this.renderRoster());
            $('av-form').addEventListener('submit', event => { event.preventDefault(); this.save(); });
            $('av-form').querySelectorAll('[name=availability-mode]').forEach(input => input.addEventListener('change', () => { this.mode = input.value; this.markDirty(); this.renderDates(); }));
            $('av-notes').addEventListener('input', () => this.markDirty());
            $('av-confirm-review').addEventListener('change', () => this.markDirty());
            $('av-clear').addEventListener('click', () => this.clear());
            this.closeDialog = () => { if (this.canLeave()) $('av-editor-dialog').close(); };
            $('av-close-dialog').addEventListener('click', this.closeDialog);
            $('av-editor-dialog').addEventListener('cancel', event => { event.preventDefault(); this.closeDialog(); });
            this.render(this.state);
        }
        isDirty() { return this.dirty; }
        markDirty() { this.dirty = true; $('av-feedback').textContent = 'Alterações ainda não salvas'; this.renderPreview(); this.options.onDirty(); }
        canLeave() {
            if (this.busy || this.blocked) return false;
            if (this.dirty && !window.confirm('Há alterações de disponibilidade ainda não salvas. Deseja descartá-las para trocar de integrante ou equipe?')) return false;
            this.dirty = false; this.options.onDirty(); return true;
        }
        person() { return this.state.availability.roster.find(item => item.team_id === this.teamId && item.member_id === this.memberId); }
        occurrences() {
            const ids = new Set(this.state.availability.summary.rows.filter(item => item.team_id === this.teamId).map(item => item.occurrence_id));
            return this.state.occurrences.filter(item => ids.has(item.id)).sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        }
        render(state) {
            this.state = state;
            const teamSelect = $('av-team');
            const catalogKey = JSON.stringify(state.teams.map(team => [team.id, team.name]));
            if (teamSelect.dataset.catalogKey !== catalogKey) {
                teamSelect.replaceChildren();
                state.teams.forEach(team => { const option = node('option', '', team.name); option.value = team.id; teamSelect.append(option); });
                teamSelect.dataset.catalogKey = catalogKey;
            }
            if (!state.teams.some(team => team.id === this.teamId)) { this.teamId = state.teams[0]?.id || null; this.memberId = null; }
            teamSelect.value = this.teamId || '';
            this.renderRoster();
            if (!this.dirty && !this.busy) this.loadPerson();
            this.setBlocked(this.blocked);
        }
        renderRoster() {
            const search = $('av-search').value.toLocaleLowerCase('pt-BR');
            const members = this.state.availability.roster.filter(item => item.team_id === this.teamId && item.name.toLocaleLowerCase('pt-BR').includes(search));
            const container = $('av-members'); container.replaceChildren();
            $('av-roster-count').textContent = `${members.length} integrante${members.length === 1 ? '' : 's'} encontrado${members.length === 1 ? '' : 's'}`;
            members.forEach(person => {
                const button = action('', () => {
                    if (!this.canLeave()) return;
                    this.memberId = person.member_id; this.renderRoster(); this.loadPerson();
                    const dialog = $('av-editor-dialog'); if (!dialog.open) dialog.showModal();
                    $('av-person-name').tabIndex = -1; $('av-person-name').focus({preventScroll: true});
                });
                button.className = 'av-member' + (this.memberId === person.member_id ? ' is-selected' : '');
                button.setAttribute('aria-pressed', String(this.memberId === person.member_id));
                const caption = person.needs_review ? 'Revisar disponibilidades' : person.record ? 'Informado' : 'Não informado';
                button.append(node('strong', '', person.name), node('span', 'av-muted', person.experience_label), node('span', person.needs_review ? 'av-unknown' : person.record ? 'av-available' : 'av-unknown', caption));
                if (person.other_teams.length) button.append(node('small', 'av-muted', 'Também: ' + person.other_teams.map(team => team.name).join(', ')));
                container.append(button);
            });
            if (!members.length) container.append(node('p', 'av-muted', 'Nenhum integrante ativo encontrado nesta equipe.'));
        }
        loadPerson() {
            const person = this.person(); $('av-empty').hidden = !!person; $('av-form').hidden = !person;
            if (!person) return;
            this.mode = person.record?.mode || ''; this.selection = new Set(person.record?.selected_occurrence_ids || []);
            $('av-person-name').textContent = person.name;
            $('av-experience').textContent = person.experience_label;
            $('av-other-teams').textContent = person.other_teams.length ? 'Também participa de: ' + person.other_teams.map(team => team.name).join(', ') : 'Participa somente desta equipe.';
            $('av-record-status').textContent = person.needs_review ? 'Precisa de revisão' : person.record ? 'Disponibilidade informada' : 'Ainda não informado';
            $('av-record-status').className = 'av-badge ' + (person.needs_review || !person.record ? 'av-unknown' : 'av-available');
            $('av-form').querySelectorAll('[name=availability-mode]').forEach(input => { input.checked = input.value === this.mode; });
            $('av-review').hidden = !person.needs_review; $('av-confirm-review').checked = false;
            $('av-notes').value = person.record?.notes || ''; $('av-clear').hidden = !person.record;
            $('av-feedback').textContent = person.record ? 'Informação salva para este mês e esta equipe' : 'Selecione um modo para registrar a informação';
            $('av-save').disabled = !this.occurrences().length;
            $('av-removed').hidden = !person.removed_selections.length;
            $('av-removed').textContent = person.removed_selections.length ? 'Seleções originais fora do planejamento atual, preservadas: ' + person.removed_selections.map(item => `${item.date.slice(8)}/${item.date.slice(5, 7)} às ${item.time} · ${item.name}`).join('; ') + '.' : '';
            this.renderDates(); this.renderPreview();
        }
        renderDates() {
            const person = this.person(); if (!person) return;
            const focused = document.activeElement?.id;
            const occurrences = this.occurrences(), container = $('av-dates'); container.replaceChildren();
            const selectable = this.mode === 'only' || this.mode === 'except';
            $('av-selection-help').textContent = this.mode === 'only' ? 'Marque as ocorrências em que a pessoa pode servir. As demais serão confirmadas como indisponíveis.' : this.mode === 'except' ? 'Marque as exceções: as ocorrências em que a pessoa não pode servir.' : this.mode === 'all' ? 'Todas as ocorrências atuais serão confirmadas como disponíveis ao salvar.' : 'Escolha um modo. Até salvar, a disponibilidade continua não informada.';
            const shortcuts = $('av-shortcuts'); shortcuts.replaceChildren();
            const setSelection = ids => { ids.forEach(id => this.selection.add(id)); this.markDirty(); this.renderDates(); };
            const all = action('Selecionar todas', () => setSelection(occurrences.map(item => item.id)));
            const none = action('Limpar seleção', () => { this.selection.clear(); this.markDirty(); this.renderDates(); });
            all.id = 'av-select-all'; none.id = 'av-select-none'; all.disabled = none.disabled = !selectable; shortcuts.append(all, none);
            const days = new Set(occurrences.map(item => new Date(item.date + 'T12:00:00').getDay()));
            days.forEach(day => { const labels = ['domingos', 'segundas', 'terças', 'quartas', 'quintas', 'sextas', 'sábados']; const button = action('Marcar ' + labels[day], () => setSelection(occurrences.filter(item => new Date(item.date + 'T12:00:00').getDay() === day).map(item => item.id))); button.id = 'av-weekday-' + day; button.disabled = !selectable; shortcuts.append(button); });
            const groups = new Map(); occurrences.forEach(item => { if (!groups.has(item.date)) groups.set(item.date, []); groups.get(item.date).push(item); });
            groups.forEach((items, date) => {
                const card = node('section', 'av-date-card'); const heading = node('label', 'av-date-heading');
                const day = node('input'); day.id = 'av-day-' + date; day.type = 'checkbox'; day.disabled = !selectable;
                day.checked = items.every(item => this.selection.has(item.id)); day.indeterminate = !day.checked && items.some(item => this.selection.has(item.id));
                day.addEventListener('change', () => { items.forEach(item => { if (day.checked) this.selection.add(item.id); else this.selection.delete(item.id); }); this.markDirty(); this.renderDates(); });
                heading.append(day, node('strong', '', dateLabel(date))); card.append(heading);
                items.forEach(item => {
                    const original = person.occurrences.find(cell => cell.occurrence_id === item.id)?.status || 'unknown';
                    const status = this.dirty ? proposedStatus(this.mode, [...this.selection], item.id) : original;
                    const label = node('label', 'av-occurrence av-' + status); const input = node('input'); input.id = 'av-occurrence-' + item.id; input.type = 'checkbox'; input.checked = this.selection.has(item.id); input.disabled = !selectable;
                    input.addEventListener('change', () => { if (input.checked) this.selection.add(item.id); else this.selection.delete(item.id); this.markDirty(); this.renderDates(); });
                    const text = node('span'); text.append(node('strong', '', item.time + ' · ' + item.name));
                    if (original === 'unknown' && person.record) text.append(node('small', 'av-muted', 'Ocorrência ou horário a revisar'));
                    label.append(input, text, node('span', 'av-cell-status', LABELS[status])); card.append(label);
                });
                container.append(card);
            });
            if (!occurrences.length) container.append(node('p', 'av-muted', 'Esta equipe não possui ocorrências no planejamento mensal. Inclua a equipe na etapa 1.'));
            this.renderPreview();
            if (focused) $(focused)?.focus({preventScroll: true});
        }
        renderPreview() {
            const person = this.person(); if (!person) return;
            const team = this.state.teams.find(item => item.id === this.teamId);
            $('av-preview').textContent = summaryText(person.name, team?.name || 'esta equipe', this.mode, [...this.selection], this.occurrences());
            $('av-preview-review').hidden = !person.needs_review;
        }
        payload() {
            const person = this.person(); const ids = new Set(this.occurrences().map(item => item.id));
            const selection = this.mode === 'all' ? [] : [...this.selection].filter(id => ids.has(id) || (this.mode === person.record?.mode && person.record.selected_occurrence_ids.includes(id)));
            return {team_id: this.teamId, member_id: this.memberId, mode: this.mode, selected_occurrence_ids: selection, notes: $('av-notes').value, confirm_current_plan: $('av-confirm-review').checked};
        }
        async save() {
            if (this.saving) return this.saving;
            const person = this.person(); if (!person || !this.mode || this.blocked) { if (person) $('av-form').reportValidity(); return false; }
            if (person.needs_review && !$('av-confirm-review').checked) { $('av-feedback').textContent = 'Confirme que revisou as ocorrências e os horários atuais.'; $('av-confirm-review').focus(); return false; }
            this.busy = true; $('av-form-fields').disabled = true; $('av-team').disabled = true; $('av-search').disabled = true; $('av-feedback').textContent = 'Salvando disponibilidade…';
            this.saving = (async () => {
                try {
                    const ok = await this.options.onSave(this.payload());
                    if (ok) { this.dirty = false; this.options.onDirty(); }
                    return ok;
                } finally {
                    this.busy = false; this.saving = null; this.render(this.options.getState());
                }
            })();
            return this.saving;
        }
        matches(payload) { return this.teamId === payload.team_id && this.memberId === payload.member_id; }
        retrySave(payload) { return this.matches(payload) ? this.save() : this.options.onSave(payload); }
        cleared(payload) {
            if (this.matches(payload)) { this.dirty = false; this.options.onDirty(); this.render(this.options.getState()); }
        }
        async clear() {
            if (this.blocked || this.busy || !window.confirm('Marcar a disponibilidade desta pessoa, nesta equipe e neste mês, como não informada?')) return;
            this.busy = true; this.setBlocked(false);
            try { if (await this.options.onClear({team_id: this.teamId, member_id: this.memberId})) { this.dirty = false; this.options.onDirty(); } }
            finally { this.busy = false; this.render(this.options.getState()); }
        }
        setBlocked(value) {
            this.blocked = value;
            $('av-form-fields').disabled = value || this.busy;
            $('av-team').disabled = $('av-search').disabled = value || this.busy;
            $('av-close-dialog').disabled = value || this.busy;
            $('av-members').querySelectorAll('button').forEach(button => { button.disabled = value || this.busy; });
        }
        renderOverview() {
            const summary = this.state.availability.summary; const totals = $('av-totals'); totals.replaceChildren();
            [['Integrantes no planejamento', summary.total_members], ['Disponibilidades informadas', summary.recorded_members], ['Ainda pendentes', summary.pending_members]].forEach(([label, count]) => { const card = node('div'); card.append(node('strong', '', String(count)), node('span', '', label)); totals.append(card); });
            const atRisk = summary.rows.filter(item => item.shortage || item.mentor_shortage);
            const summaryMessage = $('av-summary-message'); summaryMessage.replaceChildren();
            const progress = node('p', '');
            if (summary.pending_members) {
                progress.textContent = `Faltam informações de ${summary.pending_members} integrante${summary.pending_members === 1 ? '' : 's'}. Você pode continuar preenchendo ou avançar para revisar a escala depois.`;
            } else if (atRisk.length) {
                progress.textContent = `As disponibilidades foram registradas. Há ${atRisk.length} combinaç${atRisk.length === 1 ? 'ão' : 'ões'} de culto e equipe que podem precisar de atenção na geração.`;
            } else {
                progress.textContent = 'Todas as disponibilidades necessárias foram informadas. Você já pode seguir para a geração da escala.';
            }
            summaryMessage.append(progress);
            $('av-alerts-title').textContent = summary.warnings.length ? `${summary.warnings.length} avisos para revisar antes da geração` : 'Nenhuma falta identificada nas contagens atuais';
            $('av-alert-list').replaceChildren(...summary.warnings.map(warning => node('li', '', warning)));
            const table = $('av-coverage'); table.replaceChildren();
            summary.rows.forEach(item => {
                const row = node('tr'); const label = node('td'); label.append(node('strong', '', `${item.date.slice(8)}/${item.date.slice(5, 7)} · ${item.time}`), node('small', 'av-muted', item.name)); row.append(label);
                [item.team_name, item.required, item.available, item.unavailable, item.unknown, item.mentors_available, item.shortage || item.mentor_shortage ? 'Pendências' : item.pending_experience_available ? 'Revisar classificação' : 'Contagem suficiente'].forEach((value, index) => row.append(node('td', index === 7 ? '' : index === 2 ? 'av-available' : index === 4 ? 'av-unknown' : '', String(value))));
                table.append(row);
            });
            const daily = $('av-daily'); daily.replaceChildren();
            summary.daily.filter(item => item.members_with_multiple_cults || item.shortage || item.mentor_shortage).forEach(item => {
                daily.append(node('p', 'av-muted', `${item.date.slice(8)}/${item.date.slice(5, 7)}: ${item.unique_available} pessoas únicas disponíveis; mínimo de ${item.minimum_unique_people} para os cultos do dia. ${item.unique_mentors} aptas a acompanhar; mínimo de ${item.minimum_unique_mentors}. ${item.members_with_multiple_cults} pessoas aparecem em mais de uma possibilidade de culto e poderão servir em somente uma.`));
            });
        }
    }
    scope.ScheduleAvailabilityUI = {create: options => new AvailabilityUI(options)};
})(typeof window !== 'undefined' ? window : this);
