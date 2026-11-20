const {chromium} = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');

(async () => {
    const url = process.env.FILADELFIA_QA_URL;
    const browser = await chromium.launch({headless: true, executablePath: process.env.FILADELFIA_CHROME_PATH});
    const videoClip = '.editor-track--video [data-clip-key]';
    const readSequence = page => page.locator(videoClip).evaluateAll(nodes => nodes.map(node => {
        const {assetId, sourceTimeMs, start, end} = node._timeline.item;
        return {id: assetId, source: sourceTimeMs, start, end};
    }));
    const assertSequence = (actual, originals) => {
        assert.deepEqual(actual.map(clip => clip.id), originals.map(clip => clip.id));
        let cursor = 0;
        actual.forEach((clip, index) => {
            const previous = originals[index], duration = previous.end - previous.start;
            assert.deepEqual([clip.source, clip.start, clip.end], [previous.source, cursor, cursor + duration]);
            cursor += duration;
        });
    };
    async function pageFor(options) {
        const context = await browser.newContext(options);
        await context.addCookies([{
            name: 'sessionid', value: process.env.FILADELFIA_QA_SESSION, domain: new URL(url).hostname, path: '/',
        }]);
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(url, {waitUntil: 'domcontentloaded'});
        return {page, context, errors};
    }
    const saved = page => page.waitForFunction(() => document.querySelector('#editor-save-status')?.textContent === 'Salvo');
    try {
        const {page, context, errors} = await pageFor({
            viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 1,
        });
        assert.equal(await page.locator('#empty-media-state').isVisible(), true);
        await page.locator('#empty-media-state [data-open-media]').tap();
        const sheet = await page.locator('#editor-media-library').boundingBox();
        assert.ok(Math.abs(sheet.x) <= 1 && Math.abs(sheet.width - 390) <= 1);
        assert.ok(Math.abs(sheet.y + sheet.height - 844) <= 1);
        const form = page.locator('.editor-media-item').first();
        await form.locator('button').waitFor();
        assert.equal(await form.locator('input, select').count(), 0);
        for (let i = 0; i < 9; i++) {
            await form.locator('button').tap();
            await page.waitForFunction(count => document.querySelectorAll('.editor-track--video [data-clip-key]').length === count, i + 1);
            await saved(page);
        }
        assert.equal(await page.locator('.editor-media-item').count(), 0);
        await page.locator('#editor-media-library [aria-label="Fechar"]').tap();
        await page.locator('#mobile-add').tap();
        await page.locator('#add-timeline-media').tap();
        await page.locator('#editor-media-library[open]').waitFor();
        await page.waitForTimeout(300);
        assert.equal(await page.locator('.editor-media-item').count(), 0);
        await page.locator('#editor-media-library [aria-label="Fechar"]').tap();
        const original = await readSequence(page);
        const scroller = page.locator('#editor-scroller');
        const zoom = page.locator('#timeline-zoom-range');
        const originalZoom = await zoom.inputValue();
        const cdp = await context.newCDPSession(page);
        const touch = (type, points = []) => cdp.send('Input.dispatchTouchEvent', {
            type, touchPoints: points.map(([x, y]) => ({x: Math.round(x), y: Math.round(y), id: 1})),
        });
        const first = page.locator(videoClip).first();
        const startHold = async () => {
            await scroller.evaluate(node => { node.scrollLeft = 0; node.scrollTop = 0; });
            await first.scrollIntoViewIfNeeded();
            const box = await first.boundingBox();
            const point = [box.x + box.width / 2, box.y + box.height / 2];
            await touch('touchStart', [point]);
            await page.locator('.editor-compact-reorder').waitFor();
            return point;
        };
        let writes = 0;
        page.on('request', request => { if (request.url().endsWith('/video-move/')) writes++; });
        const size = await scroller.boundingBox();
        const right = size.x + size.width - 4;
        let [, y] = await startHold();
        assert.equal(await page.locator('.editor-compact-reorder__guide').isVisible(), true);
        assert.equal(await page.locator('.editor-compact-reorder__ghost').isVisible(), true);
        assert.equal(await page.evaluate(() => window.getSelection().toString()), '');
        assert.equal(await page.locator('.editor-compact-reorder__ghost span').evaluate(node => getComputedStyle(node).userSelect), 'none');
        assert.equal(await page.locator('.editor-compact-reorder__take.is-selected').count(), 1);
        await touch('touchMove', [[right, y]]);
        await page.waitForFunction(() => document.querySelector('.editor-compact-reorder__scroll').scrollLeft > 100);
        assert.ok(await page.locator('.editor-compact-reorder__take.is-selected').evaluate(n => n.style.transform !== 'translateX(0px)'));
        assert.deepEqual(await readSequence(page), original);
        await touch('touchCancel');
        await page.locator('.editor-compact-reorder').waitFor({state: 'detached'});
        assert.equal(await page.locator('.editor-compact-reorder__ghost').count(), 0);
        assert.equal(writes, 0);
        assert.equal(await zoom.inputValue(), originalZoom);
        assert.deepEqual(await readSequence(page), original);

        let unblock;
        const pending = new Promise(resolve => { unblock = resolve; });
        await page.route('**/video-move/', async route => { await pending; await route.continue(); });
        [, y] = await startHold();
        await touch('touchMove', [[right, y]]);
        await page.waitForFunction(() => {
            const strip = document.querySelector('.editor-compact-reorder__scroll');
            return strip.scrollWidth - strip.clientWidth - strip.scrollLeft < 8;
        });
        await touch('touchEnd');
        await page.waitForFunction(id => document.querySelectorAll('.editor-track--video [data-clip-key]')[8]._timeline.item.assetId === id, original[0].id);
        assertSequence(await readSequence(page), [...original.slice(1), original[0]]);
        assert.equal(await zoom.inputValue(), originalZoom);
        unblock();
        await saved(page);
        assert.equal(writes, 1);
        await page.reload({waitUntil: 'domcontentloaded'});
        await first.waitFor();
        assertSequence(await readSequence(page), [...original.slice(1), original[0]]);

        // Drag a take directly, without enabling a separate movement mode.
        const dragTake = async (from, to) => {
            const a = await page.locator(videoClip).nth(from).boundingBox();
            const b = await page.locator(videoClip).nth(to).boundingBox();
            const y = a.y + a.height / 2;
            await touch('touchStart', [[a.x + a.width / 2, y]]);
            await touch('touchMove', [[b.x + b.width * (to > from ? .9 : .1), y]]);
            await page.waitForTimeout(100);
            await touch('touchEnd');
            await saved(page);
        };
        await dragTake(0, 1);
        await page.waitForFunction(id => document.querySelectorAll('.editor-track--video [data-clip-key]')[1]._timeline.item.assetId === id, original[1].id);
        await dragTake(1, 0);
        await page.waitForFunction(id => document.querySelector('.editor-track--video [data-clip-key]')._timeline.item.assetId === id, original[1].id);
        await page.reload({waitUntil: 'domcontentloaded'});
        await first.waitFor();
        assertSequence(await readSequence(page), [...original.slice(1), original[0]]);

        // Scrub through a cut between two excerpts of the very same source URL.
        const scrub = page.locator('#preview-range');
        const seek = value => scrub.evaluate((node, ms) => {
            node.value = ms;
            node.dispatchEvent(new Event('input', {bubbles: true}));
        }, value);
        await seek(3900);
        await page.waitForFunction(() => [...document.querySelectorAll('.preview-stage video')].some(node =>
            node.readyState >= 2 && Math.abs(node.currentTime - 3.9) < .08));
        await page.locator('#preview-play').tap();
        await page.waitForFunction(() => Number(document.querySelector('#preview-range').value) > 4070);
        await page.locator('#preview-play').tap();
        assert.deepEqual(errors, []);
        await context.close();

        const desktop = await pageFor({viewport: {width: 1500, height: 900}});
        await desktop.page.locator(videoClip).first().waitFor();
        const last = await desktop.page.locator(videoClip).last().boundingBox();
        const initial = await desktop.page.locator(videoClip).first().boundingBox();
        await desktop.page.mouse.move(last.x + last.width / 2, last.y + last.height / 2);
        await desktop.page.mouse.down();
        await desktop.page.mouse.move(initial.x + 2, initial.y + initial.height / 2, {steps: 12});
        await desktop.page.locator('.editor-take-drop-guide').waitFor();
        assert.equal(await desktop.page.locator('.editor-take-drag-ghost').isVisible(), true);
        assert.equal(await desktop.page.locator('.is-take-drag-source').count(), 1);
        await desktop.page.mouse.up();
        assert.equal(await desktop.page.locator('.editor-take-drag-ghost, .editor-take-drop-guide').count(), 0);
        await desktop.page.waitForFunction(id => document.querySelector('.editor-track--video [data-clip-key]')._timeline.item.assetId === id, original[0].id);
        await saved(desktop.page);
        assertSequence(await readSequence(desktop.page), original);
        assert.deepEqual(desktop.errors, []);
        console.log('PASS free editor, touch hold, compact guide, autoscroll, cancellation, optimistic save, reload, seek/playback and mouse reorder');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
