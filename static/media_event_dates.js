(function (global) {
    'use strict';

    function renumberBadges(list) {
        list.querySelectorAll('.event-date-row').forEach(function (row, index) {
            var badge = row.querySelector('.event-date-badge');
            if (badge) badge.textContent = String(index + 1);
        });
    }

    function initEventDates(root) {
        (root || document).querySelectorAll('.event-dates-section').forEach(function (section) {
            if (section.dataset.bound) return;
            section.dataset.bound = '1';
            var prefix = section.getAttribute('data-prefix') || 'event';
            var list = section.querySelector('.event-dates-list');
            var tpl = document.getElementById(prefix + '-date-row-template');
            var addBtn = section.querySelector('.event-add-date');
            if (!list || !tpl || !addBtn) return;

            renumberBadges(list);

            addBtn.addEventListener('click', function () {
                list.appendChild(tpl.content.cloneNode(true));
                renumberBadges(list);
            });

            list.addEventListener('click', function (event) {
                var btn = event.target.closest('.event-remove-date');
                if (!btn) return;
                var rows = list.querySelectorAll('.event-date-row');
                if (rows.length <= 1) {
                    var row = rows[0];
                    var dateInput = row.querySelector('[name$="-event_date"], .event-extra-date');
                    var timeInput = row.querySelector('[name$="-event_time"], .event-extra-time');
                    if (dateInput) dateInput.value = '';
                    if (timeInput) timeInput.value = '';
                    return;
                }
                btn.closest('.event-date-row').remove();
                renumberBadges(list);
            });
        });
    }

    global.MediaEventDates = { init: initEventDates };
}(window));
