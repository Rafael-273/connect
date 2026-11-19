const { chromium } = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.FILADELFIA_CHROME_PATH });
  try {
    for (const width of [320, 390, 430, 1024]) {
      const mobile = width < 768;
      const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: mobile, hasTouch: mobile });
      await context.addCookies([{ name: 'sessionid', value: process.env.FILADELFIA_QA_SESSION, domain: new URL(process.env.FILADELFIA_QA_URL).hostname, path: '/' }]);
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.FILADELFIA_QA_URL);
      const toggle = page.locator('.demands-page-nav-toggle'), menu = page.locator('.demands-page-nav-menu');
      if (mobile) {
        const bounds = await page.evaluate(() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
          return { header: rect('.event-types-header > section'), title: rect('.event-types-header h1'), toggle: rect('.demands-page-nav-toggle'), create: rect('.demands-page-create'), width: document.documentElement.scrollWidth };
        });
        assert.ok(bounds.toggle.bottom <= bounds.header.bottom && bounds.title.right <= bounds.toggle.left + 1 && bounds.width <= width + 1, JSON.stringify(bounds));
        const card = page.locator('.event-type-card').filter({ hasText: 'Tipo de evento de teste mobile' });
        assert.equal(await card.isVisible(), true);
        assert.equal(await card.getByText('Demandas padrão não configuradas').isVisible(), true);
        assert.ok(await card.evaluate(node => node.scrollWidth <= node.clientWidth));
        await card.getByRole('button', { name: /Excluir/ }).click();
        const dialog = page.locator('#delete-event-type-modal');
        assert.equal(await dialog.isVisible(), true);
        const box = await dialog.locator('.type-modal-card').boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) <= 2, JSON.stringify(box));
        await dialog.getByRole('button', { name: 'Cancelar' }).click();
        assert.equal(await dialog.isVisible(), false);
      }
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      if (mobile) {
        for (const label of ['Todos', 'Tipos', 'Calendário']) assert.equal(await menu.getByText(label, { exact: true }).isVisible(), true);
        const anchor = await toggle.boundingBox(), dropdown = await menu.boundingBox();
        assert.ok(dropdown.y >= anchor.y + anchor.height && dropdown.x >= 0 && dropdown.x + dropdown.width <= width + 1, JSON.stringify({ anchor, dropdown }));
      }
      await page.keyboard.press('Escape');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
      assert.equal(await menu.getAttribute('inert'), '');
      if (mobile) {
        await page.getByRole('link', { name: 'Novo tipo', exact: true }).click();
        const dialog = page.locator('.type-modal[aria-labelledby="create-event-type-title"]');
        await dialog.waitFor({ state: 'visible' });
        const box = await dialog.locator('.type-modal-card').boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) <= 2, JSON.stringify(box));
        assert.ok(await dialog.locator('input[name="name"]').evaluate(node => parseFloat(getComputedStyle(node).fontSize) >= 16));
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS event types mobile header, cards, navigation and bottom sheets');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
