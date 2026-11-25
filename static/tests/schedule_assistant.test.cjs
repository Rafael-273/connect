const test = require('node:test');
const assert = require('node:assert/strict');
const {calendarDays} = require('../schedule_assistant.js');
test('November 2026 keeps five distinct Sundays and all monthly dates', () => {
    const days = calendarDays(2026, 11);
    assert.equal(days.length, 42);
    assert.equal(days[0].date, '2026-11-01');
    assert.equal(days.filter(day => day.inMonth).length, 30);
    assert.equal(new Set(days.map(day => day.date)).size, 42);
    assert.equal(days.filter((day, index) => day.inMonth && index % 7 === 0).length, 5);
});
test('calendar includes leap day and adjacent dates without moving the selected month', () => {
    const days = calendarDays(2028, 2);
    assert.equal(days.filter(day => day.inMonth).length, 29);
    assert.ok(days.some(day => day.date === '2028-02-29' && day.inMonth));
    assert.ok(days.some(day => day.date.startsWith('2028-03') && !day.inMonth));
});
test('January grid crosses the year correctly', () => {
    const days = calendarDays(2027, 1);
    assert.equal(days[0].date, '2026-12-27');
    assert.equal(days.filter(day => day.inMonth).length, 31);
});
