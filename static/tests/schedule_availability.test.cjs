const test = require('node:test');
const assert = require('node:assert/strict');
const {proposedStatus, summaryText} = require('../schedule_availability.js');
const occurrences = [
    {id: 1, date: '2026-11-15', time: '10:00', name: 'Culto da manhã'},
    {id: 2, date: '2026-11-15', time: '19:00', name: 'Culto da noite'},
    {id: 3, date: '2026-11-22', time: '19:00', name: 'Culto da noite'},
];
test('unknown is never presented as confirmed availability without a mode', () => {
    assert.equal(proposedStatus('', [], 1), 'unknown');
    assert.match(summaryText('Maria', 'Cobertura', '', [], occurrences), /ainda não foi informada/);
});
test('only and except distinguish occurrences at different times on the same date', () => {
    assert.equal(proposedStatus('only', [1], 1), 'available');
    assert.equal(proposedStatus('only', [1], 2), 'unavailable');
    assert.equal(proposedStatus('except', [1], 1), 'unavailable');
    assert.equal(proposedStatus('except', [1], 2), 'available');
});
test('empty only confirms none while empty except confirms all', () => {
    assert.equal(proposedStatus('only', [], 1), 'unavailable');
    assert.equal(proposedStatus('except', [], 1), 'available');
    assert.match(summaryText('Maria', 'Cobertura', 'only', [], occurrences), /indisponibilidade em todas/);
});
test('review summary identifies exact dates and hours and preserves a literal name', () => {
    const text = summaryText('Maria', 'Cobertura', 'except', [2], occurrences);
    assert.match(text, /exceto em: 15\/11 às 19:00/);
    assert.match(text, /Total disponível: 2 de 3/);
    assert.ok(!text.includes('10:00'));
    assert.match(summaryText('<Maria>', 'Cobertura', 'all', [], occurrences), /<Maria>/);
});
test('removed selections do not count as available dates in the current month', () => {
    assert.match(summaryText('Maria', 'Cobertura', 'only', [999], occurrences), /indisponibilidade em todas/);
    assert.match(summaryText('Maria', 'Cobertura', 'all', [], []), /Nenhuma disponibilidade será confirmada/);
});
