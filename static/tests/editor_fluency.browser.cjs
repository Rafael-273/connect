// Optional Chrome integration check. Run through website.test_editor_browser.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core'),assert=require('assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,session=process.env.FILADELFIA_QA_SESSION;
 if(!url||!session)throw new Error('Use the disposable Django browser fixture to run this check.');
 const address=new URL(url); const seed={url:address.pathname,cookie:session};
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
 const context=await browser.newContext({viewport:{width:1600,height:1000}});await context.addCookies([{name:'sessionid',value:seed.cookie,domain:address.hostname,path:'/'}]);
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.waitForSelector('[data-clip-key]');await page.waitForTimeout(250);
 await page.evaluate(()=>{window.savedNodes=[...document.querySelectorAll('[data-clip-key]')];window.savedHead=document.querySelector('.editor-playhead');window.trackMutations=0;new MutationObserver(records=>{for(const record of records)for(const node of [...record.addedNodes,...record.removedNodes])if(node.nodeType===1&&node.matches('[data-clip-key],.editor-track'))window.trackMutations++}).observe(document.getElementById('editor-tracks'),{childList:true,subtree:true})});
 await page.locator('.editor-track--caption .editor-track__clip').first().click();await page.waitForTimeout(150);
 await page.locator('#timeline-zoom-range').evaluate(input=>{for(const value of [2,4,8,16]){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))}});await page.waitForTimeout(150);
 assert.equal(await page.evaluate(()=>savedNodes.every(node=>node.isConnected)&&savedHead===document.querySelector('.editor-playhead')),true);
 assert.equal(await page.evaluate(()=>trackMutations),0);
 console.log('PASS selection + 16x zoom: zero clip/row replacements; playhead retained');
 await page.locator('#timeline-zoom-range').evaluate(input=>{input.value=1;input.dispatchEvent(new Event('input',{bubbles:true}))});await page.waitForTimeout(100);
 // A paused preview must consume the optimistic timing, before HTTP acknowledgement.
 await page.route('**/preview/subtitles/*',async route=>{if(route.request().method()!=='POST')return route.continue();const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,2000));return route.fulfill({response})});
 const previewRuler=await page.locator('.editor-ruler__scale').boundingBox();
 await page.mouse.move(previewRuler.x+previewRuler.width*.15,previewRuler.y+previewRuler.height/2);await page.mouse.down();await page.mouse.up();
 await page.waitForFunction(()=>document.getElementById('preview-caption-source').textContent==='Um');
 const caption=await page.locator('.editor-track--caption .editor-track__clip').first().boundingBox();
 await page.mouse.move(caption.x+caption.width*.7,caption.y+caption.height/2);await page.mouse.down();await page.mouse.move(caption.x+caption.width*.7+previewRuler.width*.4,caption.y+caption.height/2,{steps:5});await page.mouse.up();
 await page.waitForFunction(()=>document.getElementById('preview-caption-source').textContent==='',null,{timeout:500});
 assert.notEqual(await page.locator('#editor-save-status').textContent(),'Salvo');
 await page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
 console.log('PASS paused preview updates optimistic timing before a 2-second save reply');
 const focus=await page.evaluate(()=>{
  const container=document.createElement('div');document.body.append(container);container.dataset.viewIdentity='same';container.innerHTML='<input data-field="name" value="abcdef">';const before=container.firstChild;before.focus();before.setSelectionRange(2,4);
  ConnectEditorView.preserveFocus(container,'same',()=>container.innerHTML='<input data-field="name" value="abcdef">');
  const result={focused:document.activeElement===container.firstChild,selection:[container.firstChild.selectionStart,container.firstChild.selectionEnd]};container.remove();return result;
 });assert.deepEqual(focus,{focused:true,selection:[2,4]});
 assert.deepEqual(errors,[]);console.log('PASS inspector focus and caret restoration; no JavaScript errors');
 
 // Exercise the same view with a large, disposable timeline, without editorial POSTs.
 const big=await context.newPage();big.on('pageerror',error=>errors.push(error.message));
 await big.route('**'+seed.url,async route=>{
  const response=await route.fetch();let html=await response.text();
  html=html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,raw,b)=>{
   const timeline=JSON.parse(raw);timeline.sequence.duration_ms=1000000;timeline.clips[0].timeline_out_ms=1000000;timeline.clips[0].source_out_ms=1000000;
   timeline.captions=Array.from({length:1000},(_,i)=>({id:i+100000,track_id:1,language:'pt',is_source:true,start_ms:i*1000,end_ms:i*1000+800,text:'Legenda '+i}));
   return a+JSON.stringify(timeline)+b;
  });return route.fulfill({response,body:html});
 });
 await big.goto(url);await big.waitForFunction(()=>document.querySelectorAll('.editor-track--caption [data-clip-key]').length===1000);await big.waitForTimeout(200);
 await big.locator('#timeline-zoom-range').evaluate(input=>{input.value=16;input.dispatchEvent(new Event('input',{bubbles:true}))});await big.waitForTimeout(150);await big.locator('.editor-track--caption .editor-track__clip').first().click();await big.waitForTimeout(150);await big.locator('#timeline-zoom-range').evaluate(input=>{input.value=1;input.dispatchEvent(new Event('input',{bubbles:true}))});await big.waitForTimeout(150);
 await big.evaluate(()=>{window.nodes=[...document.querySelectorAll('[data-clip-key]')];window.renders=[];document.addEventListener('pointermove',()=>{const started=performance.now();requestAnimationFrame(()=>renders.push(performance.now()-started))},true)});
 const ruler=await big.locator('.editor-ruler__scale').boundingBox();
 await big.mouse.move(ruler.x+10,ruler.y+ruler.height/2);await big.mouse.down();await big.mouse.move(ruler.x+ruler.width*.8,ruler.y+ruler.height/2,{steps:35});await big.mouse.up();await big.waitForTimeout(100);
 const stats=await big.evaluate(()=>({retained:nodes.every(n=>n.isConnected),p95:renders.sort((a,b)=>a-b)[Math.floor(renders.length*.95)],samples:renders.length,position:Number(document.getElementById('preview-range').value)}));
 assert.equal(stats.retained,true);assert.ok(stats.position>700000);assert.deepEqual(errors,[]);console.log('PASS 1000 captions + open transcript + scrubbing',JSON.stringify(stats));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
