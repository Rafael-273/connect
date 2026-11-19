const { chromium } = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.FILADELFIA_CHROME_PATH });
  try {
    for (const width of [320, 390, 430, 1024]) {
      const mobile = width < 640;
      const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: mobile, hasTouch: mobile });
      await context.addCookies([{ name: 'sessionid', value: process.env.FILADELFIA_QA_SESSION, domain: new URL(process.env.FILADELFIA_QA_URL).hostname, path: '/' }]);
      if (process.env.FILADELFIA_FULLCALENDAR_PATH) await context.route('https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js', route => route.fulfill({ path: process.env.FILADELFIA_FULLCALENDAR_PATH, contentType: 'application/javascript' }));
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(process.env.FILADELFIA_QA_URL);
      const toggle = page.locator('.demands-page-nav-toggle'), menu = page.locator('.demands-page-nav-menu');
      await page.locator('#media-task-calendar .fc-view').waitFor();
      if (mobile) {
        const bounds = await page.evaluate(() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
          return { header: rect('.calendar-page-header > section'), title: rect('.calendar-page-header h1'), toggle: rect('.demands-page-nav-toggle'), width: document.documentElement.scrollWidth };
        });
        assert.ok(bounds.toggle.bottom <= bounds.header.bottom && bounds.title.right <= bounds.toggle.left + 1 && bounds.width <= width + 1, JSON.stringify(bounds));
        const task = page.locator('.fc-list-event').filter({ hasText: 'Revisar vídeo' });
        await task.waitFor({ state: 'visible' });
        const layout = await page.evaluate(() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
          return { card: rect('.calendar-card'), list: rect('.fc-list'), harness: rect('.fc-view-harness'), legend: rect('.calendar-legend'), root: rect('.media-calendar-page'), viewport: innerHeight };
        });
        assert.ok(layout.card.bottom >= 820 && layout.legend.bottom <= 845 && layout.list.bottom >= layout.legend.top - 16, JSON.stringify(layout));
        assert.equal(await task.getByText('Campanha do calendário').isVisible(), true);
        await task.click();
        const dialog = page.locator('#cal-task-modal');
        assert.equal(await dialog.isVisible(), true);
        await page.waitForTimeout(300);
        const box = await dialog.locator('.cal-task-modal-card').boundingBox();
        assert.ok(Math.abs(box.y + box.height - 844) <= 2, JSON.stringify(box));
        const header = await dialog.locator('.cal-task-modal-header').evaluate(node => {
          const rect = selector => node.querySelector(selector).getBoundingClientRect().toJSON();
          return { title: rect('.cal-task-modal-heading'), close: rect('.cal-task-modal-close'), action: rect('.cal-task-complete-toggle') };
        });
        assert.ok(header.title.right <= header.close.left + 1 && header.action.top >= header.title.bottom && header.close.width >= 44, JSON.stringify(header));
        if (width === 390) {
          await dialog.getByRole('button', { name: 'Concluir etapa' }).click();
          await dialog.getByRole('button', { name: 'Reabrir etapa' }).waitFor();
          assert.equal(await dialog.getByText('Etapa concluída · tocar para reabrir').isVisible(), true);
          await dialog.getByRole('button', { name: 'Reabrir etapa' }).click();
          await dialog.getByRole('button', { name: 'Concluir etapa' }).waitFor();
        }
        await dialog.getByRole('button', { name: 'Fechar' }).click();
      } else assert.equal(await page.locator('.fc-timeGridWeek-view').isVisible(), true);
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      if (width < 768) {
        for (const label of ['Todos', 'Tipos', 'Calendário']) await menu.getByText(label, { exact: true }).waitFor({ state: 'visible' });
        const anchor = await toggle.boundingBox(), dropdown = await menu.boundingBox();
        assert.ok(dropdown.y >= anchor.y + anchor.height && dropdown.x >= 0 && dropdown.x + dropdown.width <= width + 1, JSON.stringify({ anchor, dropdown }));
      }
      await page.keyboard.press('Escape');
      assert.equal(await menu.getAttribute('inert'), '');
      const title = page.locator('#cal-month-title');
      const originalTitle = await title.textContent();
      await page.locator('#cal-nav-next').click();
      assert.notEqual(await title.textContent(), originalTitle);
      await page.locator('#cal-nav-prev').click();
      assert.equal(await title.textContent(), originalTitle);
      await page.getByRole('button', { name: 'Mês', exact: true }).click();
      assert.equal(await page.locator('.fc-dayGridMonth-view').isVisible(), true);
      await page.getByRole('button', { name: 'Semana', exact: true }).click();
      assert.equal(await page.locator(mobile ? '.fc-listWeek-view' : '.fc-timeGridWeek-view').isVisible(), true);
      if (width === 390) {
        const fallbackPage = await context.newPage();
        await fallbackPage.goto(process.env.FILADELFIA_QA_URL);
        await fallbackPage.getByRole('link', { name: 'Voltar', exact: true }).click();
        assert.equal(new URL(fallbackPage.url()).pathname, new URL(process.env.FILADELFIA_QA_HUB).pathname);
        await fallbackPage.close();

        const previousUrl = process.env.FILADELFIA_QA_HUB + '?kind=all&status=open&q=calendario';
        await page.goto(previousUrl);
        await page.locator('#demands-view-toggle').click();
        await page.locator('#demands-view-menu').getByText('Calendário', { exact: true }).click();
        await page.getByRole('link', { name: 'Voltar', exact: true }).click();
        assert.equal(page.url(), previousUrl);
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS calendar mobile navigation, readable weekly events and bottom sheet');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
