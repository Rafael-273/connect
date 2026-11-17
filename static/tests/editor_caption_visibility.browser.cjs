// Browser regression against a disposable Django fixture.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const address=new URL(process.env.FILADELFIA_QA_URL),mobile=Boolean(process.env.FILADELFIA_QA_MOBILE),context=await browser.newContext(mobile?{viewport:{width:390,height:844},isMobile:true,hasTouch:true}:{viewport:{width:1600,height:1000}});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(address.href);
  const button=page.locator('.editor-track--caption .caption-visibility').first();
  await button.waitFor();
  async function change(visible){
   const response=page.waitForResponse(result=>result.url().includes('/subtitles/visibility/')&&result.request().method()==='POST');
   if(mobile)await button.tap();else await button.click();const result=await response;assert.equal(result.status(),200,await result.text());
   await page.waitForFunction(expected=>{const node=document.querySelector('.editor-track--caption .caption-visibility');return node&&!node.disabled&&node.getAttribute('aria-pressed')===String(expected)},visible);
  }
  await change(false);await change(true);await change(false);
  await page.reload();await button.waitFor();assert.equal(await button.getAttribute('aria-pressed'),'false');
  await change(true);await page.reload();await button.waitFor();assert.equal(await button.getAttribute('aria-pressed'),'true');
  assert.deepEqual(errors,[]);console.log('PASS hide, restore, reload hidden, restore and reload visible');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
