const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH});
 try{
  for(const mobile of [false,true]){
   const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:900},isMobile:mobile,hasTouch:mobile});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:new URL(process.env.FILADELFIA_QA_URL).hostname,path:'/'}]);
   const page=await context.newPage();await page.goto(process.env.FILADELFIA_QA_URL);
   const clip=page.locator('.editor-track--video [data-clip-key]').first();await clip.waitFor();
   if(mobile)await clip.tap();else await clip.click();
   await page.locator('#preview-transform-box.is-active').waitFor();
   await page.waitForTimeout(400);
   const stage=await page.locator('.preview-stage').boundingBox();
   for(const handle of await page.locator('[data-transform-handle]').all()){
    const box=await handle.boundingBox();
    assert.ok(box.x>=stage.x&&box.y>=stage.y&&box.x+box.width<=stage.x+stage.width+1&&box.y+box.height<=stage.y+stage.height+1, JSON.stringify({mobile,box,stage}));
    assert.ok(box.width>= (mobile?40:30));
   }
   const h=await page.locator('.preview-transform-box__handle--nw').boundingBox();
   await page.mouse.move(h.x+h.width/2,h.y+h.height/2);await page.mouse.down();await page.mouse.move(h.x+h.width/2-15,h.y+h.height/2-15,{steps:5});await page.mouse.up();
   await page.waitForFunction(()=>Number(document.querySelector('[data-transform="scale"]').value)>1);
   await context.close();
  }
  console.log('PASS visible handles and direct resizing on desktop and mobile');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
