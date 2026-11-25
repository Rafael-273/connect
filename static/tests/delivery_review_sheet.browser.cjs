const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH});
 try{
  for(const mobile of [true,false]){
   const viewport=mobile?{width:390,height:640}:{width:1200,height:800};
   const context=await browser.newContext({viewport,isMobile:mobile,hasTouch:mobile,acceptDownloads:true});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:new URL(process.env.FILADELFIA_QA_URL).hostname,path:'/'}]);
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(process.env.FILADELFIA_QA_URL);await page.locator('[data-open-delivery-review]').first().click();
   const dialog=page.locator('#delivery-review-dialog[open]');await dialog.waitFor();await page.locator('.review-delivery-download').first().waitFor();
   const box=await dialog.boundingBox();
   if(mobile){
    assert.ok(Math.abs(box.x)<1&&Math.abs(box.width-viewport.width)<1&&Math.abs(box.y+box.height-viewport.height)<1,JSON.stringify(box));
    const metrics=await page.locator('.delivery-review-body').evaluate(n=>({scroll:n.scrollHeight,client:n.clientHeight,overflow:getComputedStyle(n).overflowY}));
    assert.ok(metrics.scroll>metrics.client&&metrics.overflow==='auto',JSON.stringify(metrics));
   }else assert.ok(box.x>0&&box.y>0&&box.width<viewport.width);
   const actions=page.locator('.review-delivery-download');assert.equal(await actions.count(),2);
   for(const [index,name] of [[0,'imagem.png'],[1,'video.mp4']]){
    const [download]=await Promise.all([page.waitForEvent('download'),actions.nth(index).click()]);
    assert.equal(download.suggestedFilename(),name);
    assert.ok(fs.statSync(await download.path()).size>0);
   }
   await page.locator('[data-close-delivery-review]').click();assert.equal(await dialog.count(),0);
   assert.deepEqual(errors,[]);
   await context.close();
  }
  console.log('PASS mobile bottom sheet, scrolling and authenticated image/video downloads on mobile and desktop');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
