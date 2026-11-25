const test = require('node:test');
const assert = require('node:assert/strict');
const State = require('../external_media_take_suggestions.js');
const result = {suggestions: [{start_ms: 1000, end_ms: 2000}], trim_reviewed: false};
test('loads suggestions once in an untouched modal', () => {
    const state = new State();
    assert.equal(state.receive(result), true);
    assert.equal(state.receive(result), false);
});
test('late AI response cannot replace manual edits', () => {
    const state = new State(); state.edit();
    assert.equal(state.receive(result), false);
    assert.equal(state.latest.length, 1);
});
test('saved approval, including full video, prevents automatic replacement', () => {
    assert.equal(new State(true).receive(result), false);
    assert.equal(new State().receive({...result, trim_reviewed: true}), false);
});
test('manual edits after suggestions are loaded remain protected', () => {
    const state = new State(); state.receive(result); state.edit();
    assert.equal(state.receive(result), false);
});
