// Delayed and lost cut acknowledgements must never block or revert local deletion.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core'),assert=require('node:assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url),browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000}});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
  let attempt=0;
  await page.route('**/preview/cuts/',async route=>{
   if(route.request().method()!=='POST')return route.continue();
   const number=++attempt;requests.push({body:JSON.parse(route.request().postData()),id:route.request().headers()['x-editor-operation']});
   const response=await route.fetch();
   if(number===1)return route.abort('failed'); // Commit succeeded; its response was lost.
   await new Promise(resolve=>setTimeout(resolve,number===2?1200:1800));
   return route.fulfill({response});
  });
  await page.goto(url);const takes=page.locator('.editor-track--video [data-clip-key]');await takes.first().waitFor();assert.equal(await takes.count(),4);
  const remove=async index=>{await takes.nth(index).click();const started=Date.now();await page.locator('#delete-segment').click();return Date.now()-started};
  const firstLatency=await remove(1);assert.equal(await takes.count(),3);assert.match(await page.locator('#preview-time').textContent(),/00:00:08:00$/);
  assert.equal(await page.locator('.editor-track--caption [data-clip-key]').count(),1,'caption belonging to the removed take must disappear with it');
  await page.locator('#editor-save-retry').waitFor({state:'visible'});
  const secondLatency=await remove(1);assert.equal(await takes.count(),2);assert.match(await page.locator('#preview-time').textContent(),/00:00:06:00$/);
  const nextCut=page.waitForRequest(request=>request.method()==='POST'&&request.url().endsWith('/preview/cuts/')&&JSON.parse(request.postData()).start_ms===4000);
  await page.locator('#editor-save-retry').click();
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvando alterações…');
  await nextCut;
  assert.equal(await takes.count(),2,'acknowledging only the first cut must not restore the second pending deletion');
  assert.deepEqual(requests.map(request=>request.body),[{start_ms:2000,end_ms:4000},{start_ms:2000,end_ms:4000},{start_ms:4000,end_ms:6000}]);
  assert.equal(requests[0].id,requests[1].id,'retry must reuse operation identity after a lost acknowledgement');
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.reload();await takes.first().waitFor();assert.equal(await takes.count(),2);assert.match(await page.locator('#preview-time').textContent(),/00:00:06:00$/);
  assert.equal(await page.locator('.editor-track--caption [data-clip-key]').count(),1);
  await page.keyboard.press('Control+z');await page.waitForFunction(()=>document.querySelectorAll('.editor-track--video [data-clip-key]').length===3);
  await page.keyboard.press('Control+Shift+z');await page.waitForFunction(()=>document.querySelectorAll('.editor-track--video [data-clip-key]').length===2);
  await page.waitForFunction(()=>!document.querySelector('.preview-shell').inert);
  await remove(0);assert.equal(await takes.count(),1);await remove(0);assert.equal(await takes.count(),0);assert.match(await page.locator('#preview-time').textContent(),/00:00:00:00$/);
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.keyboard.press('Control+z');await page.waitForFunction(()=>document.querySelectorAll('.editor-track--video [data-clip-key]').length===1);
  assert.deepEqual(errors,[]);console.log('PASS instant successive deletions, lost-response retry, original master coordinates, reload, undo/redo, empty sequence',JSON.stringify({firstLatency,secondLatency}));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
