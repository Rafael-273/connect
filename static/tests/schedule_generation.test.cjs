const test = require('node:test');
const assert = require('node:assert/strict');
const {assignmentPayload, labels} = require('../schedule_generation.js');

test('manual composition only locks selected members', () => {
    assert.deepEqual(assignmentPayload(12, ['4', '9'], ['9', '20']), {
        plan_id: 12, member_ids: [4, 9], locked_member_ids: [9],
    });
});

test('experience labels remain explicit in review', () => {
    assert.equal(labels.mentor, 'Apto a acompanhar');
    assert.equal(labels.trainee, 'Em treinamento');
    assert.equal(labels.pending, 'Classificação pendente');
});
