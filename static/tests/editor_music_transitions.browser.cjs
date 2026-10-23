const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core'),assert=require('node:assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url),browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined,args:['--autoplay-policy=no-user-gesture-required']});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000}});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{window.qaAudio=[];const NativeAudio=window.Audio;window.Audio=class extends NativeAudio{constructor(...args){super(...args);window.qaAudio.push(this)}}});
  let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/preview/music/',async route=>{
   if(route.request().method()!=='POST')return route.continue();
   requests.push(JSON.parse(route.request().postData()));
   if(requests.length===1)await gate;
   return route.continue();
  });
  await page.goto(url);const join=page.locator('.editor-music-transition');await join.waitFor();
  await page.waitForFunction(()=>window.qaAudio.filter(audio=>audio.src.includes('/preview/music/')).every(audio=>audio.duration>0));
  assert.equal((await join.textContent()).trim(),'+');
  await join.click();await page.locator('[data-transition-type]').selectOption('CONSTANT_POWER');
  await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.includes('2.00s'));
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvando alterações…');
  const input=page.locator('[data-transition-duration]');await input.fill('0.50');await input.dispatchEvent('input');
  await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.includes('0.50s'));
  await page.waitForTimeout(300);release();
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  assert.equal(requests.at(-1).tracks[1].audio_transition.duration_ms,500);
  assert.deepEqual(requests.at(-1).tracks.map(track=>[track.start_ms,track.end_ms]),[[0,5000],[5000,10000]]);
  await page.reload();await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.includes('0.50s'));
  await join.click();await page.locator('[data-transition-type]').selectOption('NONE');
  await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.trim()==='+');
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.locator('[data-history="undo"]').click();
  await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.includes('0.50s'));
  await page.waitForFunction(()=>!document.querySelector('.preview-shell').inert);
  await page.locator('[data-history="redo"]').click();
  await page.waitForFunction(()=>document.querySelector('.editor-music-transition')?.textContent.trim()==='+');
  assert.deepEqual(errors,[]);
  console.log('PASS explicit join, immediate duration edits while save is delayed, serialized snapshots, reload, removal, undo/redo');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
