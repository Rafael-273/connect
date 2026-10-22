// Regression: a selected take without an automatic reframe still has controls.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url);
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000}});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[],requests=[];
  // Decode a real, asymmetric frame so screenshots detect actual image changes.
  const recorder=await context.newPage();await recorder.goto(url);
  const encoded=await recorder.evaluate(async()=>{
   const canvas=document.createElement('canvas');canvas.width=480;canvas.height=270;
   const draw=canvas.getContext('2d');draw.fillStyle='#17375b';draw.fillRect(0,0,480,270);
   for(let i=0;i<12;i++){draw.fillStyle=i%2?'#e6b32a':'#40c3a0';draw.fillRect(i*40,30+i*12,32,80)}
   const stream=canvas.captureStream(30),chunks=[],record=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'});
   const done=new Promise(resolve=>record.onstop=resolve);record.ondataavailable=event=>chunks.push(event.data);
   record.start();const tick=setInterval(()=>draw.fillRect(0,0,1,1),30);await new Promise(resolve=>setTimeout(resolve,500));record.stop();await done;clearInterval(tick);stream.getTracks().forEach(track=>track.stop());
   return await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(new Blob(chunks,{type:'video/webm'}))});
  });await recorder.close();const bytes=Buffer.from(encoded,'base64');
  const hydrate=timeline=>{
   timeline.assets.forEach(asset=>{if(!asset.asset_id)asset.url='/qa-framing.webm?source=1'});
   if(timeline.review_master_url)timeline.review_master_url='/qa-framing.webm?master=1';
   return timeline;
  };
  await page.route('**/preview/**',async route=>{
   if(route.request().resourceType()==='document'){
    const response=await route.fetch(),html=await response.text();
    return route.fulfill({response,body:html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,raw,b)=>a+JSON.stringify(hydrate(JSON.parse(raw)))+b)});
   }
   if(route.request().method()==='POST'){
    const response=await route.fetch(),body=await response.json();if(body.timeline)hydrate(body.timeline);
    if(route.request().url().includes('/transforms/'))await new Promise(resolve=>setTimeout(resolve,1500));
    return route.fulfill({response,json:body});
   }
   return route.continue();
  });
  await page.route('**/qa-framing.webm*',route=>route.fulfill({status:200,contentType:'video/webm',body:bytes}));
  const visibleVideo=()=>page.evaluate(()=>{
   const videos=[...document.querySelectorAll('.preview-stage>video')].filter(video=>Number(getComputedStyle(video).opacity)>0);
   const video=videos.sort((a,b)=>Number(getComputedStyle(b).zIndex)-Number(getComputedStyle(a).zIndex))[0];
   return video?{transform:video.style.transform,position:video.style.objectPosition,ready:video.readyState}:null;
  });
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/transforms/'))requests.push(JSON.parse(request.postData()))});
  await page.goto(url);const takes=page.locator('.editor-track--video [data-clip-key]');
  await takes.first().waitFor();assert.equal(await takes.count(),2);
  await takes.first().click();
  await page.locator('[data-transform="scale"]').waitFor({state:'visible'});
  await page.locator('#preview-transform-box.is-active').waitFor({state:'visible'});
  await page.waitForFunction(()=>[...document.querySelectorAll('.preview-stage>video')].some(video=>video.readyState>=2&&Number(getComputedStyle(video).opacity)>0));
  const before=await page.locator('.preview-stage').screenshot();
  assert.equal(requests.length,0,'selection must not create an editorial save');
  for(const [key,value] of [['scale','1.35'],['x','0.2'],['y','-0.1']]){
   await page.locator(`[data-transform="${key}"]`).evaluate((input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))},value);
  }
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.waitForTimeout(400);
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  assert.equal(requests.length,1);assert.equal(requests[0].segment_start_ms,0);assert.equal(requests[0].segment_end_ms,5000);
  assert.match((await visibleVideo()).transform,/scale\(1.35\)/);
  assert.notDeepEqual(await page.locator('.preview-stage').screenshot(),before,'decoded frame must visibly change');
  await page.locator('[data-transform="scale"]').evaluate(input=>{input.value='1.7';input.dispatchEvent(new Event('input',{bubbles:true}))});
  await page.waitForTimeout(100);
  assert.match((await visibleVideo()).transform,/scale\(1.7\)/,'a second adjustment must affect the displayed video before the delayed response');
  const zoomed=await page.locator('.preview-stage').screenshot();
  for(const [key,value] of [['x','0.6'],['y','0.5']]){
   await page.locator(`[data-transform="${key}"]`).evaluate((input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))},value);
  }
  await page.waitForTimeout(100);
  assert.equal((await visibleVideo()).position,'20% 25%','position changes must reach the displayed decoder immediately');
  assert.notDeepEqual(await page.locator('.preview-stage').screenshot(),zoomed,'moving X/Y must change decoded pixels');
  assert.notEqual(await page.locator('#editor-save-status').textContent(),'Salvo');
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  for(const [key,value] of [['scale','1.35'],['x','0.2'],['y','-0.1']]){
   await page.locator(`[data-transform="${key}"]`).evaluate((input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))},value);
  }
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  await page.reload();await takes.first().click();
  assert.equal(await page.locator('[data-transform="scale"]').inputValue(),'1.35');
  assert.equal(await page.locator('[data-transform="x"]').inputValue(),'0.2');
  assert.equal(await page.locator('[data-transform="y"]').inputValue(),'-0.1');
  await takes.nth(1).click();
  assert.equal(await page.locator('[data-transform="scale"]').inputValue(),'1','framing must not leak into another take');
  await takes.first().click();await page.keyboard.press('Control+z');
  await page.waitForFunction(()=>document.querySelector('[data-transform="scale"]')?.value==='1.7');
  await page.keyboard.press('Control+Shift+z');
  await page.waitForFunction(()=>document.querySelector('[data-transform="scale"]')?.value==='1.35');
  await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo'&&!document.querySelector('.preview-shell').inert);
  if(process.env.FILADELFIA_QA_SCREENSHOT)await page.screenshot({path:process.env.FILADELFIA_QA_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS real decoded frames: repeated zoom and X/Y change visible pixels before a 1.5-second save reply; scoped interval, reload, undo/redo');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
