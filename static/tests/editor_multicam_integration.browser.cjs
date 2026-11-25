const assert=require('node:assert/strict');
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE);
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.FILADELFIA_CHROME_PATH?{executablePath:process.env.FILADELFIA_CHROME_PATH}:{channel:'chrome'})});
 try{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url),context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);const panel=page.locator('#multicam-panel');
 await page.locator('[data-preview-tool="add-timeline-camera"]').click();await panel.waitFor({state:'visible'});
 const added=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 await panel.locator('form').first().locator('input').nth(0).fill('00:01.250');
 await panel.locator('form').first().locator('input').nth(1).fill('00:05.500');
 await panel.locator('.multicam-advanced > summary').click();
 await panel.locator('form').first().locator('input').nth(2).fill('250');
 await panel.getByRole('button',{name:'Adicionar alternância'}).click();
 let response=await added;assert.equal(response.status(),200,await response.text());
 await page.locator('.editor-track--camera [data-clip-key]').waitFor();
 await page.reload();await page.locator('.editor-track--camera [data-clip-key]').click();
 const inspector=page.locator('#review-list');
 assert.equal(await inspector.locator('[data-camera-start]').inputValue(),'00:01.250');
 assert.equal(Number(await inspector.locator('[data-camera-offset]').inputValue()),250);
 const moved=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 await inspector.locator('[data-camera-start]').fill('00:02.125');
 await inspector.locator('[data-save-camera]').click();
 response=await moved;assert.equal(response.status(),200,await response.text());
 const timeline=(await response.json()).timeline;
 assert.equal(timeline.camera_segments[0].start_ms,2125);assert.equal(timeline.camera_segments[0].source_in_ms,2375);
 await page.waitForFunction(()=>document.querySelector('#editor-save-status')?.textContent==='Salvo');
 const removed=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 page.once('dialog',dialog=>dialog.accept());
 await inspector.locator('[data-remove-camera]').click();
 response=await removed;assert.equal(response.status(),200);assert.deepEqual((await response.json()).timeline.camera_segments,[]);
 assert.deepEqual(errors,[]);
 console.log('Real editor: add, reload, manual offset persistence, move, remove and saved revision: OK');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
