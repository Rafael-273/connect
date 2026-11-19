const {chromium} = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const assert = require('node:assert/strict');

(async () => {
    const url = process.env.FILADELFIA_QA_URL;
    const browser = await chromium.launch({headless:true, executablePath:process.env.FILADELFIA_CHROME_PATH});
    async function setup(options) {
        const context = await browser.newContext(options);
        await context.addCookies([{name:'sessionid', value:process.env.FILADELFIA_QA_SESSION, domain:new URL(url).hostname, path:'/'}]);
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(url);
        await page.locator('.editor-track--caption [data-clip-key]').first().waitFor();
        return {context,page,errors};
    }
    try {
        const desktop=await setup({viewport:{width:1400,height:900}});
        const lane=desktop.page.locator('.editor-track--caption .editor-track__lane').first();
        await desktop.page.locator('.editor-track--caption [data-clip-key]').first().click({button:'right'});
        await desktop.page.getByRole('menuitem',{name:'Copiar legenda'}).click();
        const box=await lane.boundingBox();
        await lane.click({button:'right',position:{x:box.width*.65,y:box.height/2}});
        await desktop.page.getByRole('menuitem',{name:'Colar legenda'}).click();
        await desktop.page.waitForFunction(()=>document.querySelectorAll('.editor-track--caption [data-clip-key]').length===3);
        await desktop.page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
        await desktop.page.reload();
        await desktop.page.waitForFunction(()=>document.querySelectorAll('.editor-track--caption [data-clip-key]').length===3);
        assert.deepEqual(desktop.errors,[]);
        await desktop.context.close();

        const mobile=await setup({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
        const cdp=await mobile.context.newCDPSession(mobile.page);
        const touch=(type,points=[])=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([x,y])=>({x:Math.round(x),y:Math.round(y),id:1}))});
        const hold=async (x,y,cancel=false)=>{
            await touch('touchStart',[[x,y]]);
            await mobile.page.waitForTimeout(500);
            await touch(cancel?'touchCancel':'touchEnd');
        };
        const caption=mobile.page.locator('.editor-track--caption [data-clip-key]').first();
        const first=await caption.boundingBox();
        await hold(first.x+first.width/2,first.y+first.height/2,true);
        assert.equal(await mobile.page.locator('#caption-context-menu').isVisible(),false);
        await hold(first.x+first.width/2,first.y+first.height/2);
        await mobile.page.getByRole('menuitem',{name:'Copiar legenda'}).tap();
        const mobileLane=mobile.page.locator('.editor-track--caption .editor-track__lane').first();
        const rect=await mobileLane.boundingBox();
        await hold(rect.x+rect.width*.84,rect.y+rect.height/2);
        await mobile.page.getByRole('menuitem',{name:'Colar legenda'}).tap();
        await mobile.page.waitForFunction(()=>document.querySelectorAll('.editor-track--caption [data-clip-key]').length===4);
        await mobile.page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
        assert.deepEqual(mobile.errors,[]);
        await mobile.context.close();
        console.log('PASS right click and long press copy/paste, cancellation and reload');
    } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
