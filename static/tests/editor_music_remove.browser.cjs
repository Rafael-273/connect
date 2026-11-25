// Remove the template's direct upload using the same control on phone and desktop.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const address=new URL(process.env.FILADELFIA_QA_URL),mobile=Boolean(process.env.FILADELFIA_QA_MOBILE);
  const context=await browser.newContext(mobile?{viewport:{width:390,height:844},isMobile:true,hasTouch:true}:{viewport:{width:1600,height:1000}});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(address.href);const clip=page.locator('.editor-track--music [data-clip-key]').first();await clip.waitFor();
  const select=async node=>{if(mobile)await node.tap();else await node.click()};
  const audioShortcut=page.locator(mobile?'[data-mobile-adjustment="audio"]':'[data-preview-adjustment="audio"]');
  const source=page.locator('.editor-track--audio [data-clip-key]').first();await select(source);
  assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
  assert.equal(await audioShortcut.isVisible(),true);await select(audioShortcut);
  assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'false');
  assert.equal(await page.locator('#review-panel .adjustment-tabs').isVisible(),false);
  assert.equal(await page.locator('#review-panel .preview-adjustments__head strong').textContent(),'Áudio');
  assert.ok(await page.locator('.preview-decision[data-adjustment-category="audio"]:not([hidden])').count()>0);
  await select(page.locator('#close-review-panel'));
  await select(clip);
  assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
  assert.equal(await audioShortcut.isVisible(),true);await select(audioShortcut);
  assert.equal(await page.locator('#review-panel .adjustment-tabs').isVisible(),false);
  await page.locator('[data-music-volume]').waitFor({state:'visible'});
  if(mobile)await select(page.locator('#close-review-panel'));

  const remove=page.locator('#delete-segment');assert.equal(await remove.isEnabled(),true);
  if(mobile)await remove.tap();else await page.getByRole('button',{name:'Remover esta música'}).click();
  await page.waitForFunction(()=>!document.querySelector('.editor-track--music'));
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.reload();assert.equal(await page.locator('.editor-track--music').count(),0);
  const undo=page.locator('[data-history="undo"]');if(mobile)await undo.tap();else await undo.click();await clip.waitFor();
  await page.waitForFunction(()=>!document.querySelector('.preview-shell').inert);
  const redo=page.locator('[data-history="redo"]');if(mobile)await redo.tap();else await redo.click();
  await page.waitForFunction(()=>!document.querySelector('.editor-track--music'));
  await page.reload();assert.equal(await page.locator('.editor-track--music').count(),0);
  assert.deepEqual(errors,[]);console.log('PASS remove template music, reload, undo, redo and reload');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
