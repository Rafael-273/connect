// Optional Chrome integration check. Run through website.test_editor_browser.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core'),assert=require('assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,session=process.env.FILADELFIA_QA_SESSION;
 if(!url||!session)throw new Error('Use the disposable Django browser fixture to run this check.');
 const address=new URL(url); const seed={url:address.pathname,cookie:session};
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined,args:['--autoplay-policy=no-user-gesture-required']});
 try{
 const context=await browser.newContext({viewport:{width:1600,height:1000}});await context.addCookies([{name:'sessionid',value:seed.cookie,domain:address.hostname,path:'/'}]);
 const recorder=await context.newPage();await recorder.goto(url);
 let bytes;
 {
  const encoded=await recorder.evaluate(async()=>{
   const canvas=document.createElement('canvas');canvas.width=480;canvas.height=270;const drawing=canvas.getContext('2d'),stream=canvas.captureStream(30);
   const audio=new AudioContext(),destination=audio.createMediaStreamDestination(),oscillator=audio.createOscillator();oscillator.connect(destination);oscillator.start();await audio.resume();destination.stream.getAudioTracks().forEach(track=>stream.addTrack(track));
   const chunks=[],record=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8,opus'}),started=performance.now();let stopped=false;
   const paint=()=>{if(stopped)return;drawing.fillStyle=`hsl(${(performance.now()-started)/30} 70% 40%)`;drawing.fillRect(0,0,480,270);drawing.fillStyle='white';drawing.font='40px Arial';drawing.fillText(((performance.now()-started)/1000).toFixed(2),120,140);requestAnimationFrame(paint)};
   record.ondataavailable=e=>chunks.push(e.data);const finished=new Promise(resolve=>record.onstop=resolve);record.start();paint();await new Promise(resolve=>setTimeout(resolve,6200));stopped=true;record.stop();await finished;oscillator.stop();await audio.close();stream.getTracks().forEach(track=>track.stop());
   const data=await new Blob(chunks,{type:'video/webm'}).arrayBuffer();let binary='';for(const value of new Uint8Array(data))binary+=String.fromCharCode(value);return btoa(binary);
  });bytes=Buffer.from(encoded,'base64');
 }
 await recorder.close();const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/qa-p1-media.webm*',async route=>{
  const range=route.request().headers().range;if(range){const match=/bytes=(\d+)-(\d*)/.exec(range),start=Number(match[1]),end=match[2]?Math.min(bytes.length-1,Number(match[2])):bytes.length-1;return route.fulfill({status:206,headers:{'Content-Type':'video/webm','Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${bytes.length}`},body:bytes.subarray(start,end+1)})}
  return route.fulfill({status:200,headers:{'Content-Type':'video/webm','Accept-Ranges':'bytes'},body:bytes});
 });
 await page.route('**'+seed.url,async route=>{
  const response=await route.fetch();let html=await response.text();html=html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,raw,b)=>{
   const t=JSON.parse(raw),original=t.clips[0],id=original.asset_id,first={...original,timeline_in_ms:0,timeline_out_ms:5000,source_in_ms:0,source_out_ms:5000,master_in_ms:0,master_out_ms:5000},second={...first,asset_id:id+'-qa',timeline_in_ms:5000,timeline_out_ms:10000,master_in_ms:5000,master_out_ms:10000};
   t.assets.find(asset=>asset.id===id).url='/qa-p1-media.webm?take=1';t.assets.push({...t.assets.find(asset=>asset.id===id),id:id+'-qa',url:'/qa-p1-media.webm?take=2'});t.clips=[first,second];t.review_master_url=null;t.review_rendered_layers=[];t.review_proxy_status='SOURCE';t.sequence.duration_ms=10000;
   t.has_music=true;t.music={volume:.15,ducking_enabled:true,tracks:[{id:1,name:'QA music',url:'/qa-p1-media.webm?music=1',duration_ms:6200,start_ms:0,end_ms:10000,source_in_ms:0}]};
   return a+JSON.stringify(t)+b;
  });return route.fulfill({response,body:html});
 });
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('video.preview-source-buffer')?.readyState>=2);await page.waitForTimeout(300);
 const scale=page.locator('.editor-ruler__scale');const box=await scale.boundingBox();
 await page.mouse.move(box.x+box.width*.1,box.y+box.height/2);await page.mouse.down();
 for(const point of [.8,.2,.7,.1,.6,.4]){await page.mouse.move(box.x+box.width*point,box.y+box.height/2);await page.waitForTimeout(30)}await page.mouse.up();await page.waitForTimeout(700);
 const scrub=await page.evaluate(()=>({position:Number(document.getElementById('preview-range').value),videos:[...document.querySelectorAll('video.preview-source-buffer')].map(v=>({source:v.dataset.source,time:v.currentTime,paused:v.paused}))}));
 assert.ok(Math.abs(scrub.position-4000)<100,JSON.stringify(scrub));assert.ok(scrub.videos.every(v=>v.paused));console.log('PASS rapid scrubbing across two real video sources',JSON.stringify(scrub));
 await page.locator('#preview-play').click();const positions=[];
 for(let i=0;i<28;i++){await page.waitForTimeout(40);positions.push(await page.locator('#preview-range').inputValue())}
 const updates=new Set(positions).size;assert.ok(updates>15,JSON.stringify(positions));console.log('PASS playback clock',JSON.stringify({updates,samples:positions.length,start:positions[0],end:positions.at(-1)}));
 await page.locator('#preview-play').click();
 await page.locator('#timeline-zoom-range').evaluate(input=>{input.value=16;input.dispatchEvent(new Event('input',{bubbles:true}))});await page.waitForTimeout(500);
 const waves=await page.locator('.editor-source-waveform,.editor-music-waveform').evaluateAll(nodes=>nodes.map(n=>({pixels:n.width,width:n.getBoundingClientRect().width,connected:n.isConnected})));assert.ok(waves.some(w=>w.pixels>300),JSON.stringify(waves));assert.ok(waves.every(w=>w.pixels<2000),JSON.stringify(waves));
 assert.deepEqual(errors,[]);console.log('PASS decoded audio waveforms at 16x zoom have bounded canvas allocations',JSON.stringify(waves));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
