const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');

function app({height = 844, visualHeight = height, editable = false, scale = 1, standalone = false} = {}) {
    const events = {}, classes = new Set(), properties = {};
    const viewport = {height: visualHeight, offsetTop: 0, scale, addEventListener: (name, fn) => {events[name] = fn;}};
    class HTMLElement {matches() {return editable;}}
    const root = {classList: {toggle(name, on) {on ? classes.add(name) : classes.delete(name);}}, style: {setProperty(name, value) {properties[name] = value;}}};
    const window = {innerHeight: height, visualViewport: viewport, matchMedia: () => ({matches: standalone}), addEventListener: (name, fn) => {events[name] = fn;}};
    vm.runInNewContext(read('pwa.js'), {window, navigator: {}, document: {documentElement: root, activeElement: new HTMLElement(), addEventListener: (name, fn) => {events[name] = fn;}}, HTMLElement, requestAnimationFrame: fn => {fn();return 1;}, cancelAnimationFrame() {}});
    return {events, classes, properties, viewport};
}
test('installed mode and viewport remain independent of keyboard', () => {
    const state = app({standalone: true, editable: true, visualHeight: 544});
    assert.ok(state.classes.has('is-standalone'));
    assert.equal(state.properties['--app-keyboard-inset'], '300px');
    assert.ok(state.classes.has('has-keyboard'));
    assert.equal(state.properties['--app-viewport-height'], undefined);
    state.viewport.height = 844;
    state.events.resize();
    assert.equal(state.properties['--app-keyboard-inset'], '0px');
    assert.ok(!state.classes.has('has-keyboard'));
});
test('zoom, browser chrome and native layout resizing do not add keyboard space', () => {
    for (const options of [{visualHeight: 544}, {editable: true, visualHeight: 780}, {editable: true, visualHeight: 400, scale: 2}, {height: 544, visualHeight: 544, editable: true}]) {
        assert.equal(app(options).properties['--app-keyboard-inset'], '0px');
    }
});
test('worker ignores writes, foreign origins and private data; navigation fails safely offline', async () => {
    const listeners = {};
    vm.runInNewContext(read('sw.js'), {self: {location: {origin: 'https://connect.test'}, addEventListener: (name, fn) => {listeners[name] = fn;}}, URL, Response, fetch: () => Promise.reject(new Error('offline'))});
    for (const request of [{method: 'POST', url: 'https://connect.test/login/'}, {method: 'GET', url: 'https://other.test/static/a.css'}, {method: 'GET', url: 'https://connect.test/api/private'}, {method: 'GET', url: 'https://connect.test/media/private.mp4'}]) {
        listeners.fetch({request, respondWith() {assert.fail('request should not be intercepted');}});
    }
    let response;
    listeners.fetch({request: {method: 'GET', mode: 'navigate', url: 'https://connect.test/dashboard/'}, respondWith(promise) {response = promise;}});
    assert.equal((await response).status, 503);
    assert.match(await (await response).text(), /sem conexão/);
});
