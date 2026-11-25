const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');

const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(path.join(root, 'templates/member/external_media/preview.html'), 'utf8');
const start = template.indexOf('    function editableTransitions(');
const end = template.indexOf('    function renderAdjustmentTabs()', start);
assert.ok(start >= 0 && end > start);

(async () => {
    const browser = await chromium.launch({
        executablePath: process.env.FILADELFIA_CHROME_PATH || '/usr/bin/google-chrome',
        headless: true,
    });
    try {
        if (process.env.FILADELFIA_QA_URL) {
            const url = process.env.FILADELFIA_QA_URL;
            for (const mobile of [true, false]) {
                const page = await browser.newPage({viewport: {width: mobile ? 390 : 1280, height: 900}, hasTouch: mobile});
                await page.context().addCookies([{name: 'sessionid', value: process.env.FILADELFIA_QA_SESSION, url}]);
                await page.goto(url, {waitUntil:'domcontentloaded'});
                const attribute = mobile ? 'data-mobile-adjustment' : 'data-preview-adjustment';
                const broll = page.locator('.editor-track--broll .editor-track__clip').first();
                if (mobile) await broll.tap();
                else await broll.click();
                const elements = page.locator(`[${attribute}="elements"]`);
                await elements.waitFor({state:'visible'});
                assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'), 'true');
                await elements.click();
                const brollPanel = page.locator('#review-panel.is-open .broll-inspector');
                await brollPanel.waitFor({state:'visible'});
                assert.equal(await brollPanel.getByText('Tempo na timeline', {exact:true}).count(), 0);
                const mode = brollPanel.getByRole('combobox', {name:'Exibição',exact:true});
                const desiredMode = mobile ? 'OVERLAY' : 'FULLSCREEN';
                assert.notEqual(await mode.inputValue(), desiredMode);
                const [modeResponse] = await Promise.all([
                    page.waitForResponse(response => response.request().method()==='POST' && response.url().includes('/brolls/')),
                    mode.selectOption({label:mobile ? 'Sobre o vídeo' : 'Tela cheia'}),
                ]);
                assert.equal((await modeResponse.json()).timeline.brolls[0].display_mode, desiredMode);
                const position = brollPanel.getByRole('spinbutton', {
                    name:mobile ? 'Posição horizontal em porcentagem' : 'Recorte horizontal em porcentagem',
                });
                const positionSave = page.waitForResponse(response => response.request().method()==='POST'
                    && response.url().includes('/brolls/'));
                await position.fill(mobile ? '62.5' : '74');
                await position.press('Tab');
                const positionResult = await (await positionSave).json();
                assert.equal(positionResult.timeline.brolls[0].transform.x, mobile ? .625 : .74);
                await page.locator('#close-review-panel').click();
                const text = page.locator('.editor-track--overlay .editor-track__clip').first();
                if (mobile) await text.tap();
                else await text.click();
                await page.locator(mobile ? '[data-mobile-adjustment="transitions"]' : '[data-preview-adjustment="transitions"]').click();
                const card = page.locator('.transition-inspector');
                const name = mobile ? 'Wipe' : 'Zoom';
                const saved = page.waitForResponse(response => response.request().method() === 'POST'
                    && response.url().endsWith('/overlays/text-transition/'));
                await card.getByRole('tabpanel', {name:'Entrada'}).getByLabel(name, {exact:true}).check();
                assert.equal((await saved).status(), 200);
                await page.reload({waitUntil:'domcontentloaded'});
                const transitions = await page.locator('#preview-timeline').evaluate(node =>
                    JSON.parse(node.textContent).overlays.find(item => item.id === 'text-transition').transitions);
                assert.ok(transitions.in.effects.some(effect => effect.type === name.toLowerCase()));
                await page.close();
            }
            return;
        }
        for (const width of [390, 1280]) {
            const page = await browser.newPage({viewport: {width, height: 840}, hasTouch: width < 600});
            const cssStart = template.indexOf('    .transition-inspector{');
            const cssEnd = template.indexOf('    .editor-track__clip.has-transition-in', cssStart);
            await page.setContent(`<style>${template.slice(cssStart, cssEnd)}</style>
                <div id="frame" style="position:relative;width:320px;height:180px"><div id="layer" style="position:absolute;left:70%;top:50%;width:80px;height:40px"></div></div>
                <div id="inspector" style="box-sizing:border-box;max-width:430px;padding:12px"></div>`);
            await page.addScriptTag({path: path.join(root, 'static/external_media_transitions.js')});
            await page.addScriptTag({content: `
                const list = document.getElementById('inspector');
                const item = {id:'x',start_ms:100,end_ms:2100};
                const requestTimelineRender=()=>{};
                const queuePropertySave=(kind,target,card,payload)=>Object.assign(target,payload);
                ${template.slice(start,end).replace(/{%.*?%}/g, '')}
                renderTransitionInspector('broll',item);
                window.qa={item,apply(time){
                    const node=document.getElementById('layer');
                    ConnectVisualTransitions.apply(node,item,time,2000,{x:'-50%',y:'-50%',centerX:.7,centerY:.5,scale:1,rotation:30,opacity:.8});
                    const rect=node.getBoundingClientRect(),stage=node.parentElement.getBoundingClientRect();
                    return {right:rect.right-stage.left,center:(rect.left+rect.right)/2-stage.left,
                            opacity:Number(node.style.opacity),clip:node.style.clipPath};
                }};
            `});
            const entry = page.locator('#transition-panel-in'), exit = page.locator('#transition-panel-out');
            assert.equal(await exit.isVisible(), false);
            assert.equal(await page.getByRole('button', {name:/Testar/}).count(), 0);
            assert.equal(await entry.getByRole('heading', {name:'Ajustes finos'}).isVisible(), true);
            assert.equal(await entry.locator('[name=blur]').isVisible(), false);
            for (const effect of ['Blur + Fade', 'Slide', 'Zoom', 'Wipe']) await entry.getByLabel(effect, {exact: true}).check();
            const blur = entry.locator('[name=blur]');
            assert.equal(await blur.isVisible(), true);
            await blur.press('End');
            assert.equal(await page.evaluate(() => qa.item.transitions.in.effects.find(effect=>effect.type==='blur_fade').blur), 32);
            await entry.getByRole('button', {name:'0,6 s', exact:true}).click();
            assert.equal(await page.evaluate(() => qa.item.transitions.in.duration_ms), 600);
            await entry.getByRole('group', {name:'Direção de Wipe'}).getByRole('button', {name:'Base'}).click();
            assert.equal(await page.evaluate(() => qa.item.transitions.in.effects.find(effect=>effect.type==='wipe').direction), 'bottom');
            await page.getByRole('tab', {name:'Entrada'}).press('ArrowRight');
            assert.equal(await entry.isVisible(), false);
            assert.equal(await exit.isVisible(), true);
            await exit.getByLabel('Zoom', {exact: true}).check();
            const starting = await page.evaluate(() => qa.apply(0));
            assert.ok(starting.right < 1);
            assert.equal(starting.opacity, 0);
            assert.match(starting.clip, /100%/);
            const midpoint = await page.evaluate(() => qa.apply(1000));
            assert.equal(midpoint.opacity, .8);
            assert.ok(Math.abs(midpoint.center - 224) < 1);
            assert.equal(await exit.getByRole('heading', {name:'Ajustes finos'}).isVisible(), true);
            await exit.getByRole('button', {name:'Limpar saída'}).click();
            assert.equal(await page.evaluate(() => qa.item.transitions.in.effects.length), 4);
            await page.getByRole('tab', {name:'Entrada'}).click();
            await entry.getByRole('button', {name:'Limpar entrada'}).click();
            assert.deepEqual(await page.evaluate(() => qa.item.transitions), {});
            await page.close();
        }
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
