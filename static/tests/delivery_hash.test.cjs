const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const SHA = require('../delivery_hash_worker.js');
for (const size of [0, 1, 55, 56, 63, 64, 65, 127, 128, 129, 4097, 1024 * 1024]) {
    test(`SHA-256 matches node crypto for ${size} bytes and uneven chunks`, () => {
        const bytes = crypto.randomBytes(size), hash = new SHA();
        for (let i = 0; i < bytes.length; i += 37) hash.update(bytes.subarray(i, i + 37));
        assert.equal(hash.hex(), crypto.createHash('sha256').update(bytes).digest('hex'));
    });
}
