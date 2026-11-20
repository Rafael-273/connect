const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('templates/member/external_media/project_detail.html', 'utf8');
const start = template.indexOf('    let validationRequest = 0;');
const end = template.indexOf('    function formatFileSize(', start);
assert.ok(start >= 0 && end > start);

const errors = {innerHTML: 'Vídeo ausente'};
const button = {
    disabled: true, style: {}, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
};
let serverState = {errors: '', disabled: false};
const context = {
    window: {location: {href: 'http://local/project/'}},
    document: {querySelector(selector) { return selector === '[data-project-validation]' ? errors : button; }},
    fetch: async () => ({ok: true, text: async () => JSON.stringify(serverState)}),
    DOMParser: class {
        parseFromString(html) {
            const state = JSON.parse(html);
            return {querySelector(selector) {
                return selector === '[data-project-validation]' ? {innerHTML: state.errors} : {disabled: state.disabled};
            }};
        }
    },
};
vm.createContext(context);
vm.runInContext(template.slice(start, end), context);
(async () => {
    await context.refreshProjectValidation();
    assert.equal(button.disabled, false);
    assert.equal(errors.innerHTML, '');
    serverState = {errors: 'Falta outro bloco', disabled: true};
    await context.refreshProjectValidation();
    assert.equal(button.disabled, true);
    assert.equal(errors.innerHTML, 'Falta outro bloco');
    assert.equal(button.attributes['aria-describedby'], 'project-validation-errors');
    console.log('Project validation follows the server after upload.');
})().catch(error => { console.error(error); process.exitCode = 1; });
