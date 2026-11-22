// Local synthetic-media regression: no Django account, database or S3.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const root=path.resolve(__dirname,'../..');
const template=fs.readFileSync(path.join(root,'templates/member/external_media/preview.html'),'utf8');
const start=template.indexOf('    function releaseBrollNode('),end=template.indexOf('    function reframeDecisionAt(',start);
assert.ok(start>=0&&end>start);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||'/usr/bin/google-chrome'});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.setContent('<div id="broll"></div><div id="stage"></div><details id="panel"></details><p id="error" hidden></p>');
  await page.addStyleTag({content:template.match(/\.preview-broll-item\.is-fullscreen\.is-buffering\{[^}]+\}/)[0]});
  for(const name of ['external_media_editor_view.js','external_media_preview_source.js','external_media_preview_metrics.js','external_media_multicam.js'])await page.addScriptTag({path:path.join(root,'static',name)});
  const encoded=await page.evaluate(async()=>{
   const canvas=document.createElement('canvas');canvas.width=160;canvas.height=90;
   const drawing=canvas.getContext('2d'),stream=canvas.captureStream(30),recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks=[];
   recorder.ondataavailable=event=>chunks.push(event.data);const done=new Promise(resolve=>recorder.onstop=resolve);
   let tick=0;const timer=setInterval(()=>{drawing.fillStyle=tick++%2?'red':'blue';drawing.fillRect(0,0,160,90)},33);
   recorder.start();await new Promise(resolve=>setTimeout(resolve,700));recorder.stop();await done;clearInterval(timer);stream.getTracks().forEach(track=>track.stop());
   const blob=new Blob(chunks,{type:'video/webm'});window.mediaURL=URL.createObjectURL(blob);
   const bytes=new Uint8Array(await blob.arrayBuffer());let text='';for(const byte of bytes)text+=String.fromCharCode(byte);return btoa(text);
  });
  await page.addScriptTag({content:`
   const brollLayer=document.getElementById('broll'),video={paused:true,playbackRate:1};
   let globalMs=0,baked=false,items=[],indexes=null,renderQueued=false;
   const brollSources={},asset=id=>({url:brollSources[id]||mediaURL}),reviewRenders=()=>baked;
   function indexedPreview(){return indexes||=( {brolls:new ConnectEditorView.IntervalIndex(items),brollResources:new ConnectEditorView.IntervalIndex(items.filter(item=>item.enabled!==false),item=>item.start_ms-5000,item=>item.end_ms+1000)} )}
   function requestPreviewRender(){if(renderQueued)return;renderQueued=true;requestAnimationFrame(()=>{renderQueued=false;renderBrolls()})}
   ${template.slice(start,end)}
   window.qa={setItems:next=>{items=next;indexes=null},setSource:(id,url)=>{brollSources[id]=url},setPlaying:value=>{video.paused=!value},render:time=>{globalMs=time;renderBrolls()},bake:()=>{baked=true;renderBrolls()},unbake:()=>{baked=false},stats:()=>{const nodes=[...brollLayer.querySelectorAll('video')];return {total:nodes.length,loaded:nodes.filter(node=>node.hasAttribute('src')).length,visible:nodes.filter(node=>!node.hidden).length}}};
  `});
  for(const cuts of [50,100]){
   await page.evaluate(cuts=>qa.setItems(Array.from({length:cuts},(_,i)=>({id:'clip-'+i,asset_id:i,media_type:'VIDEO',start_ms:i*2000,end_ms:i*2000+1000}))),cuts);
   let peak=0;
   for(let i=0;i<cuts;i++){
    const stats=await page.evaluate(i=>{qa.render(i*2000);return qa.stats()},i);peak=Math.max(peak,stats.loaded);
    assert.equal(stats.visible,1);assert.ok(stats.loaded<=3,JSON.stringify(stats));
   }
   await page.evaluate(()=>{window.retired=[...document.querySelectorAll('#broll video')];qa.render(1000000)});
   assert.deepEqual(await page.evaluate(()=>qa.stats()),{total:0,loaded:0,visible:0});
   assert.equal(await page.evaluate(()=>retired.every(node=>node.paused&&!node.hasAttribute('src')&&!node.isConnected)),true);
   console.log('PASS bounded B-roll resources',JSON.stringify({cuts,peakLoaded:peak,afterLeaving:0}));
  }
  await page.evaluate(()=>{qa.setItems(Array.from({length:4},(_,i)=>({id:'layer-'+i,asset_id:i,media_type:'VIDEO',start_ms:0,end_ms:5000,source_in_ms:500})));qa.render(0)});
  await page.waitForFunction(()=>[...document.querySelectorAll('#broll video')].every(node=>node.readyState>=2&&!node.seeking&&Math.abs(node.currentTime-.5)<=1/30));
  assert.equal((await page.evaluate(()=>qa.stats())).visible,4);
  await page.evaluate(()=>{window.retired=[...document.querySelectorAll('#broll video')];qa.bake()});
  assert.equal(await page.evaluate(()=>retired.every(node=>node.paused&&!node.hasAttribute('src')&&!node.isConnected)),true);
  await page.evaluate(()=>{qa.unbake();qa.setItems([{id:'replace',asset_id:0,media_type:'VIDEO',start_ms:0,end_ms:1000}]);qa.render(0);window.replaced=document.querySelector('#broll video');qa.setItems([{id:'replace',asset_id:0,media_type:'IMAGE',start_ms:0,end_ms:1000}]);qa.render(0)});
  assert.equal(await page.evaluate(()=>replaced.paused&&!replaced.hasAttribute('src')&&document.querySelector('#broll img')!==null),true);
  await page.evaluate(()=>{qa.setItems([]);qa.render(0)});
  console.log('PASS simultaneous layers, paused source offset, baked cleanup and media-type replacement');
  await page.evaluate(()=>{
   qa.setItems([{id:'warm',asset_id:0,media_type:'VIDEO',start_ms:4000,end_ms:4400,source_in_ms:500}]);
   qa.render(0);
  });
  await page.waitForFunction(()=>{
   const node=document.querySelector('[data-broll-id="warm"]');
   return node.hidden&&node.readyState>=2&&!node.seeking&&Math.abs(node.currentTime-.5)<=1/30;
  });
  assert.equal(await page.evaluate(()=>{
   const warmed=document.querySelector('[data-broll-id="warm"]');
   qa.setPlaying(true);
   qa.render(4000);
   return document.querySelector('[data-broll-id="warm"]')===warmed
    &&!warmed.hidden&&!warmed.paused&&!warmed.classList.contains('is-buffering');
  }),true);
  let releaseBroll;const brollGate=new Promise(resolve=>releaseBroll=resolve);
  await page.route('http://preview.test/broll.webm',async route=>{
   await brollGate;await route.fulfill({contentType:'video/webm',body:Buffer.from(encoded,'base64')});
  });
  assert.deepEqual(await page.evaluate(()=>{
   qa.setPlaying(false);
   qa.setSource('broll-asset-1','http://preview.test/broll.webm');
   qa.setItems([
    {id:'full',asset_id:1,media_type:'VIDEO',start_ms:5000,end_ms:5500},
    {id:'overlay',asset_id:1,media_type:'VIDEO',start_ms:5000,end_ms:5500,display_mode:'OVERLAY'},
   ]);
   qa.render(5000);
   const full=document.querySelector('[data-broll-id="full"]'),overlay=document.querySelector('[data-broll-id="overlay"]');
   return {covered:!full.hidden&&getComputedStyle(full).backgroundColor==='rgb(8, 9, 13)',overlayHidden:overlay.hidden};
  }),{covered:true,overlayHidden:true});
  releaseBroll();
  await page.waitForFunction(()=>[...document.querySelectorAll('#broll video')].every(node=>!node.hidden&&!node.classList.contains('is-buffering')));
  await page.evaluate(()=>{qa.setItems([]);qa.render(0)});
  console.log('PASS prewarmed source offset, full-frame buffering cover and overlay readiness');
  await page.evaluate(()=>{
   window.cameraTime=0;const main=document.createElement('video');
   window.cameraTimeline={sequence:{fps:30},cameras:[{id:'side',url:mediaURL}],camera_segments:[{id:'zero',camera_source_id:'side',start_ms:0,end_ms:500,source_in_ms:0,rate:1}],decisions:[]};
   window.camera=ConnectMulticam.create({panel:document.getElementById('panel'),stage:document.getElementById('stage'),mainVideo:main,getTimeline:()=>cameraTimeline,getTime:()=>cameraTime,save:async()=>{},seek:()=>{},editor:false});camera.sync();
  });
  await page.waitForFunction(()=>{const node=document.querySelector('#stage video');return node.readyState>=2&&!node.hidden});
  await page.evaluate(()=>{cameraTime=100;camera.sync()});
  await page.waitForFunction(()=>{const node=document.querySelector('#stage video');return !node.hidden&&!node.seeking&&Math.abs(node.currentTime-.1)<=1/30});
  console.log('PASS native camera frame at zero without seeked and accurate paused small seek');
  let releaseFirst;const gate=new Promise(resolve=>releaseFirst=resolve);let requests=0;
  await page.route('http://preview.test/blocked.webm',async route=>{if(++requests===1)await gate;await route.fulfill({contentType:'video/webm',body:Buffer.from(encoded,'base64')}).catch(()=>{})});
  await page.evaluate(()=>{
   const host=document.createElement('div'),main=document.createElement('video');host.append(main);document.body.append(host);
   window.metrics=new ConnectPreviewMetrics({getMedia:()=>document.querySelectorAll('video')});
   window.player=new ConnectPreviewSource(main,{progressTimeoutMs:150,maxLoadMs:1000,onLoading:value=>window.isLoading=value,onError:failure=>{document.getElementById('error').hidden=!failure},play:()=>player.video.play()});
   player.warm('http://preview.test/blocked.webm',0);player.seek('http://preview.test/blocked.webm',0,{autoplay:true});
   metrics.beginSeek('http://preview.test/blocked.webm',0,player);
  });
  await page.waitForFunction(()=>player.failure?.reason==='timeout');
  await page.waitForFunction(()=>metrics.samples.some(sample=>sample.type==='seek'&&sample.outcome==='error'));
  assert.equal(await page.evaluate(()=>isLoading||document.getElementById('error').hidden||!player.video.paused),false);
  releaseFirst();await page.waitForTimeout(150);
  assert.equal(await page.evaluate(()=>player.failure?.reason==='timeout'&&player.video.paused),true);
  await page.evaluate(()=>{player.retry();metrics.beginSeek('http://preview.test/blocked.webm',0,player)});await page.waitForFunction(()=>!isLoading&&player.video.readyState>=2&&!player.failure);
  assert.equal(await page.evaluate(()=>player.video.paused&&document.getElementById('error').hidden),true);
  console.log('PASS stalled warm load reports error, late readiness cannot resume, explicit retry loads paused');
  await page.waitForFunction(()=>metrics.samples.some(sample=>sample.type==='seek'&&sample.outcome==='ready'));
  assert.equal(await page.evaluate(()=>metrics.export().samples.at(-1).type),'resources');
  console.log('PASS opt-in diagnostics distinguish failed seeks and paused readiness, with resource snapshot');
  assert.deepEqual(errors,[]);await page.evaluate(()=>URL.revokeObjectURL(mediaURL));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
