const { chromium } = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.FILADELFIA_CHROME_PATH });
  try {
    for (const width of [320, 390, 1024]) {
      const mobile = width <= 640;
      const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: mobile, hasTouch: mobile });
      await context.addCookies([{ name: 'sessionid', value: process.env.FILADELFIA_QA_SESSION, domain: new URL(process.env.FILADELFIA_QA_URL).hostname, path: '/' }]);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.FILADELFIA_QA_URL);
      const toggle = page.locator('[data-event-facts-toggle]');
      const fields = page.locator('.event-fact-summary__fields');
      await toggle.waitFor({ state: 'attached' });
      if (mobile) {
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
        assert.equal(await fields.isVisible(), false);
        const order = await page.evaluate(() => ({ summary: document.querySelector('.event-fact-summary').getBoundingClientRect().bottom, description: document.querySelector('.event-description-card').getBoundingClientRect().top }));
        assert.ok(order.summary <= order.description, JSON.stringify(order));
        await toggle.tap();
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
        assert.equal(await fields.locator('form').count(), 3);
        const sizes = await fields.locator('select, input:not([type="hidden"])').evaluateAll(nodes => nodes.map(node => ({ height: node.getBoundingClientRect().height, font: parseFloat(getComputedStyle(node).fontSize) })));
        assert.ok(sizes.length >= 4 && sizes.every(size => size.height >= 48 && size.font >= 16), JSON.stringify(sizes));
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
        await toggle.tap();
        assert.equal(await fields.isVisible(), false);
        if (width === 390) {
          await page.goto(process.env.FILADELFIA_QA_HUB);
          await page.locator(`[data-demands-select][href^="${new URL(process.env.FILADELFIA_QA_URL).pathname}?"]`).click();
          const modal = page.locator('#demands-detail-modal');
          await modal.locator('[data-event-facts-toggle]').waitFor({ state: 'visible' });
          await modal.locator('[data-event-facts-toggle]').tap();
          await modal.locator('.event-dates-card input[type="date"]').first().scrollIntoViewIfNeeded();
          assert.equal(await modal.locator('.event-fact-summary__fields').isVisible(), true);
          assert.ok(await modal.locator('.event-detail-grid').evaluate(node => node.scrollHeight > node.clientHeight));
          for (const closeMethod of ['button', 'escape', 'backdrop']) {
            const demand = modal.locator('[data-open-demand-detail]').first();
            await demand.scrollIntoViewIfNeeded();
            await demand.click();
            await modal.locator('.demand-detail-grid').waitFor({ state: 'visible' });
            if (closeMethod === 'button') await modal.locator('.detail-modal-close').click();
            else if (closeMethod === 'escape') await page.keyboard.press('Escape');
            else await modal.locator('.demands-detail-modal-backdrop').click({ position: { x: 2, y: 2 } });
            assert.equal(await modal.locator('.event-fact-summary__fields').isVisible(), true);
            assert.equal(await modal.getAttribute('aria-hidden'), 'false');
            assert.equal(await modal.locator('[data-event-facts-toggle]').getAttribute('aria-expanded'), 'true');
          }
          await modal.locator('.detail-modal-close').click();
          assert.equal(await modal.getAttribute('aria-hidden'), 'true');
        }
      } else {
        assert.equal(await toggle.isVisible(), false);
        assert.equal(await fields.locator('form').first().isVisible(), true);
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS event details at mobile and desktop widths');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
