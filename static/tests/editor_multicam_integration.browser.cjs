const assert=require('node:assert/strict');
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE);
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url),context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);const panel=page.locator('#multicam-panel');await panel.waitFor({state:'visible'});await panel.locator('summary').click();
 const added=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 await panel.locator('form').first().locator('input').nth(0).fill('1.250');
 await panel.locator('form').first().locator('input').nth(1).fill('5.500');
 await panel.locator('form').first().locator('input').nth(2).fill('250');
 await panel.getByRole('button',{name:'Adicionar alternância'}).click();
 let response=await added;assert.equal(response.status(),200,await response.text());
 await panel.getByRole('button',{name:'Salvar câmera e limites'}).waitFor();
 await page.reload();await panel.waitFor({state:'visible'});await panel.locator('summary').click();
 assert.equal(Number(await panel.locator('form').first().locator('input').nth(0).inputValue()),1.25);
 assert.equal(Number(await panel.locator('form').first().locator('input').nth(2).inputValue()),250);
 const moved=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 await panel.locator('form').first().locator('input').nth(0).fill('2.125');
 await panel.getByRole('button',{name:'Salvar câmera e limites'}).click();
 response=await moved;assert.equal(response.status(),200,await response.text());
 const timeline=(await response.json()).timeline;
 assert.equal(timeline.camera_segments[0].start_ms,2125);assert.equal(timeline.camera_segments[0].source_in_ms,2375);
 await page.waitForFunction(()=>!document.querySelector('#editor-save-state')?.classList.contains('is-saving'));
 const removed=page.waitForResponse(response=>response.url().endsWith('/preview/cameras/')&&response.request().method()==='POST');
 await panel.getByRole('button',{name:'Usar principal / remover'}).click();
 response=await removed;assert.equal(response.status(),200);assert.deepEqual((await response.json()).timeline.camera_segments,[]);
 assert.deepEqual(errors,[]);
 console.log('Real editor: add, reload, manual offset persistence, move, remove and saved revision: OK');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
