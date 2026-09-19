(function (global) {
    'use strict';

    function applyTypeSelectLabels(select, hintId, subtitleId, iconEl, fallbackIcon) {
        if (!select) return;
        var opt = select.options[select.selectedIndex];
        var hintEl = hintId ? document.getElementById(hintId) : null;
        var subtitle = subtitleId ? document.getElementById(subtitleId) : null;
        if (!opt || !opt.value) {
            if (hintEl) hintEl.textContent = '';
            if (iconEl) iconEl.className = 'fas fa-' + (fallbackIcon || 'clipboard-list');
            return;
        }
        if (hintEl) hintEl.textContent = opt.getAttribute('data-hint') || '';
        if (subtitle) {
            subtitle.textContent = opt.getAttribute('data-group') === 'organizational'
                ? 'Tarefa de organização ou alinhamento'
                : 'Produção de conteúdo para mídia';
        }
        if (iconEl) {
            iconEl.className = 'fas fa-' + (opt.getAttribute('data-icon') || fallbackIcon || 'file');
        }
    }

    function getTypeRolesFromOption(opt) {
        if (!opt) return [];
        var raw = opt.getAttribute('data-default-roles') || '';
        return raw.split('|').map(function (s) { return s.trim(); }).filter(Boolean);
    }

    function isAssignmentListPristine(container) {
        if (!container) return true;
        var rows = container.querySelectorAll('.demand-assignment-row-wrap');
        if (!rows.length) return true;
        for (var i = 0; i < rows.length; i++) {
            var wrap = rows[i];
            var idInput = wrap.querySelector('[name$="-assignment_id"]');
            var user = wrap.querySelector('[name$="-assignment_user"]');
            var dueDays = wrap.querySelector('[name$="-assignment_due_days"]');
            var dueRel = wrap.querySelector('[name$="-assignment_due_relation"]');
            var desc = wrap.querySelector('[name$="-assignment_desc"]');
            if (idInput && idInput.value) return false;
            var hasDue = (dueDays && dueDays.value) || (dueRel && dueRel.value && dueRel.value !== 'before');
            if ((user && user.value) || hasDue || (desc && desc.value.trim())) return false;
        }
        return true;
    }

    function syncRoleField(row) {
        var sel = row.querySelector('.assignment-role-select');
        var custom = row.querySelector('.assignment-role-custom');
        if (!sel || !custom) return;
        var fieldName = sel.getAttribute('data-role-name');
        if (sel.value === '__custom__') {
            sel.removeAttribute('name');
            custom.classList.remove('hidden');
            custom.removeAttribute('disabled');
            custom.name = fieldName;
        } else {
            sel.name = fieldName;
            custom.classList.add('hidden');
            custom.setAttribute('disabled', '');
            custom.removeAttribute('name');
            if (sel.value !== '__custom__') custom.value = '';
        }
    }

    function bindRoleFields(scope) {
        (scope || document).querySelectorAll('.demand-assignment-row').forEach(function (row) {
            var sel = row.querySelector('.assignment-role-select');
            if (!sel || sel.dataset.bound) return;
            sel.dataset.bound = '1';
            sel.addEventListener('change', function () { syncRoleField(row); });
            syncRoleField(row);
        });
    }

    function setAssignmentRows(container, rows) {
        if (!container) return;
        var prefix = container.getAttribute('data-prefix');
        var list = container.querySelector('.demand-assignments-list');
        var tpl = document.getElementById(prefix + '-assignment-row-template');
        if (!list || !tpl) return;
        list.innerHTML = '';
        var data = rows && rows.length ? rows : [{ id: '', role: '', user_id: '', due_days: '', due_relation: 'before', description: '', is_custom_role: false }];
        data.forEach(function (row) {
            var node = tpl.content.cloneNode(true);
            var wrap = node.querySelector('.demand-assignment-row-wrap') || node;
            var idInput = wrap.querySelector('[name$="-assignment_id"]');
            var roleSelect = wrap.querySelector('.assignment-role-select');
            var roleCustom = wrap.querySelector('.assignment-role-custom');
            var userSelect = wrap.querySelector('[name$="-assignment_user"]');
            var dueDaysInput = wrap.querySelector('[name$="-assignment_due_days"]');
            var dueRelInput = wrap.querySelector('[name$="-assignment_due_relation"]');
            var descInput = wrap.querySelector('[name$="-assignment_desc"]');
            if (idInput) idInput.value = row.id || '';
            if (roleSelect) {
                var role = row.role || '';
                var isCustom = row.is_custom_role || (role && roleSelect.querySelector('option[value="' + role + '"]') === null);
                if (isCustom && role) {
                    roleSelect.value = '__custom__';
                    if (roleCustom) roleCustom.value = role;
                } else {
                    roleSelect.value = role;
                }
            }
            if (userSelect) userSelect.value = row.user_id ? String(row.user_id) : '';
            if (dueDaysInput) dueDaysInput.value = row.due_days || '';
            if (dueRelInput) dueRelInput.value = row.due_relation || 'before';
            if (descInput) descInput.value = row.description || '';
            list.appendChild(node);
        });
        bindRoleFields(list);
    }

    function maybeSuggestAssignmentRoles(select, container) {
        if (!select || !container || !isAssignmentListPristine(container)) return;
        var opt = select.options[select.selectedIndex];
        if (!opt || !opt.value) return;
        var roles = getTypeRolesFromOption(opt);
        if (!roles.length) {
            setAssignmentRows(container, [{ id: '', role: '', user_id: '', due_days: '', due_relation: 'before', description: '', is_custom_role: false }]);
            return;
        }
        setAssignmentRows(container, roles.map(function (role) {
            return { id: '', role: role, user_id: '', due_days: '', due_relation: 'before', description: '', is_custom_role: false };
        }));
    }

    function bindTypeSelect(select, hintId, subtitleId, assignmentsContainer, iconEl, fallbackIcon) {
        if (!select) return;
        var update = function () {
            applyTypeSelectLabels(select, hintId, subtitleId, iconEl, fallbackIcon);
            maybeSuggestAssignmentRoles(select, assignmentsContainer);
        };
        select.addEventListener('change', update);
        update();
    }

    function initAssignmentSections(root) {
        (root || document).querySelectorAll('.demand-assignments').forEach(function (container) {
            var prefix = container.getAttribute('data-prefix');
            var addBtn = container.querySelector('.demand-add-assignment');
            var list = container.querySelector('.demand-assignments-list');
            var tpl = document.getElementById(prefix + '-assignment-row-template');
            if (!addBtn || !list || !tpl || container.dataset.bound) return;
            container.dataset.bound = '1';

            bindRoleFields(container);

            addBtn.addEventListener('click', function () {
                var node = tpl.content.cloneNode(true);
                list.appendChild(node);
                bindRoleFields(list.lastElementChild);
            });

            list.addEventListener('click', function (e) {
                var btn = e.target.closest('.demand-remove-assignment');
                if (!btn) return;
                var rows = list.querySelectorAll('.demand-assignment-row-wrap');
                if (rows.length <= 1) {
                    var wrap = rows[0];
                    wrap.querySelector('[name$="-assignment_id"]').value = '';
                    var sel = wrap.querySelector('.assignment-role-select');
                    var custom = wrap.querySelector('.assignment-role-custom');
                    if (sel) sel.value = '';
                    if (custom) custom.value = '';
                    var userInput = wrap.querySelector('[name$="-assignment_user"]');
                    if (userInput) userInput.value = '';
                    var dueDaysClear = wrap.querySelector('[name$="-assignment_due_days"]');
                    var dueRelClear = wrap.querySelector('[name$="-assignment_due_relation"]');
                    if (dueDaysClear) dueDaysClear.value = '';
                    if (dueRelClear) dueRelClear.value = 'before';
                    var desc = wrap.querySelector('[name$="-assignment_desc"]');
                    if (desc) desc.value = '';
                    syncRoleField(wrap.querySelector('.demand-assignment-row') || wrap);
                    return;
                }
                btn.closest('.demand-assignment-row-wrap').remove();
            });
        });
    }

    global.MediaDemandAssignments = {
        init: initAssignmentSections,
        bindTypeSelect: bindTypeSelect,
        setAssignmentRows: setAssignmentRows,
    };
}(window));
