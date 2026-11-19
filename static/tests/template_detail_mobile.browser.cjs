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
          return { header: rect('.template-detail-header > section'), title: rect('.template-detail-header h1'), toggle: rect('.demands-page-nav-toggle'), create: rect('.demands-page-create'), search: rect('input[type="search"]'), width: document.documentElement.scrollWidth };
        });
        assert.ok(bounds.toggle.bottom <= bounds.header.bottom && bounds.title.right <= bounds.toggle.left + 1 && bounds.search.top > bounds.header.bottom && bounds.width <= width + 1, JSON.stringify(bounds));
        const card = page.locator('.template-item-card');
        assert.equal(await card.locator('.template-item-card__step').count(), 2);
        assert.equal(await card.getByText('Designer').isVisible(), true);
        assert.ok(await card.evaluate(node => node.scrollWidth <= node.clientWidth));
        await card.getByRole('button', { name: /Excluir/ }).click();
        const dialog = page.locator('#delete-template-demand-modal');
        assert.equal(await dialog.isVisible(), true);
        const box = await dialog.locator('.template-demand-card').boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) <= 2, JSON.stringify(box));
        await dialog.getByRole('button', { name: 'Cancelar' }).click();
      } else assert.equal(await page.locator('table.data-table').isVisible(), true);
      await toggle.click();
      if (mobile) for (const label of ['Todos', 'Tipos', 'Calendário']) assert.equal(await menu.getByText(label, { exact: true }).isVisible(), true);
      await page.keyboard.press('Escape');
      assert.equal(await menu.getAttribute('inert'), '');
      if (mobile) {
        await page.getByRole('link', { name: 'Nova demanda', exact: true }).click();
        const dialog = page.locator('.template-demand-modal[aria-labelledby="create-template-demand-title"]');
        await dialog.waitFor({ state: 'visible' });
        const box = await dialog.locator('.template-demand-card').boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) <= 2, JSON.stringify(box));
        assert.ok(await dialog.locator('input[name="template-title"]').evaluate(node => parseFloat(getComputedStyle(node).fontSize) >= 16));
        await dialog.getByRole('link', { name: 'Fechar' }).last().click();
        await page.locator('.template-item-card').getByRole('link', { name: /Editar/ }).click();
        assert.equal(await page.locator('.template-demand-modal[aria-labelledby="edit-template-demand-title"]').isVisible(), true);
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS template demands mobile header, steps and bottom sheets');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
