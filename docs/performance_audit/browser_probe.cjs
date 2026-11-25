// Read-only audit harness. Extracts production functions; uses synthetic local media.
// No Django, project credentials, database or S3 access.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const root = path.resolve(__dirname, '../..');
// Keep the audit evidence reproducible after product fixes land.
const revision = '0867fd0831f77f61c579913c7e606c27b5f16e6a';
const baseline = file => execFileSync('git', ['show', `${revision}:${file}`], {cwd:root, encoding:'utf8'});
const template = baseline('templates/member/external_media/preview.html');
const start = template.indexOf('    function renderBrolls(){');
const end = template.indexOf('    function reframeDecisionAt(', start);
assert.ok(start >= 0 && end > start);
(async () => {
  const browser = await chromium.launch({headless:true,
    executablePath:process.env.FILADELFIA_CHROME_PATH || '/usr/bin/google-chrome'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setContent('<div id="broll"></div><div id="stage"></div><details id="panel"></details>');
    for (const name of ['external_media_preview_source.js', 'external_media_multicam.js']) {
      await page.addScriptTag({content:baseline(`static/${name}`)});
    }
    await page.evaluate(async () => {
      const canvas = document.createElement('canvas'); canvas.width=160; canvas.height=90;
      const context = canvas.getContext('2d'), stream=canvas.captureStream(30);
      const recorder=new MediaRecorder(stream, {mimeType:'video/webm;codecs=vp8'}), chunks=[];
      recorder.ondataavailable=event=>chunks.push(event.data);
      const done=new Promise(resolve=>recorder.onstop=resolve);
      let frame=0;
      const timer=setInterval(()=>{context.fillStyle=frame++%2?'red':'blue';context.fillRect(0,0,160,90)},33);
      recorder.start(); await new Promise(resolve=>setTimeout(resolve,1000)); recorder.stop(); await done;
      clearInterval(timer); stream.getTracks().forEach(track=>track.stop());
      window.auditURL=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));
    });
    await page.addScriptTag({content:`
      const brollLayer=document.getElementById('broll');
      let globalMs=0;
      const video={paused:true,playbackRate:1};
      const items=Array.from({length:12},(_,i)=>({id:'b'+i,asset_id:i,media_type:'VIDEO',start_ms:i*2000,end_ms:i*2000+1000}));
      const asset=()=>({url:window.auditURL});
      const reviewRenders=()=>false;
      const indexedPreview=()=>({brolls:{at:time=>items.filter(item=>item.start_ms<=time&&time<item.end_ms)}});
      ${template.slice(start,end)}
      window.auditRender=(time)=>{globalMs=time;renderBrolls()};
    `});
    for (let i=0;i<12;i++) {
      await page.evaluate(i=>auditRender(i*2000),i);
      await page.waitForFunction(i=>document.querySelector('[data-broll-id="b'+i+'"]').readyState>=2,i);
    }
    const retained=await page.evaluate(()=>{
      auditRender(25000);
      const nodes=[...document.querySelectorAll('#broll video')];
      return {visited:12,elements:nodes.length,hidden:nodes.filter(v=>v.hidden).length,
        withSource:nodes.filter(v=>v.hasAttribute('src')).length,readyWithData:nodes.filter(v=>v.readyState>=2).length};
    });
    assert.equal(retained.withSource,12); assert.equal(retained.hidden,12);
    const brollSeeks=await page.evaluate(()=>{
      const node=document.querySelector('#broll video'); let writes=0,current=.2;
      Object.defineProperty(node,'duration',{configurable:true,get:()=>1});
      Object.defineProperty(node,'currentTime',{configurable:true,get:()=>current,set:value=>{writes++;current=value}});
      auditRender(300); const pausedSmallSeekWrites=writes;
      // Lengthen this synthetic clip past the loop boundary.
      // Access lexical `items` through a script expression below instead.
      return {pausedSmallSeekWrites};
    });
    await page.addScriptTag({content:'items[0].end_ms=5000;'});
    const loop=await page.evaluate(()=>{
      const node=document.querySelector('#broll video');let writes=0;
      Object.defineProperty(node,'currentTime',{configurable:true,get:()=>.2,set:()=>writes++});
      for(let i=0;i<5;i++)auditRender(2200);
      return {renderCalls:5,seekWrites:writes};
    });
    assert.equal(brollSeeks.pausedSmallSeekWrites,0); assert.equal(loop.seekWrites,5);
    const camera=await page.evaluate(async()=>{
      const main=document.createElement('video');
      const timeline={cameras:[{id:'side',url:auditURL}],camera_segments:[{id:'c',camera_source_id:'side',start_ms:0,end_ms:1000,source_in_ms:0,rate:1}],decisions:[]};
      const stage=document.getElementById('stage');
      const editor=ConnectMulticam.create({panel:document.getElementById('panel'),stage,mainVideo:main,
        getTimeline:()=>timeline,getTime:()=>0,save:async()=>{},seek:()=>{},editor:false});
      editor.sync(); const image=stage.querySelector('video');
      await new Promise((resolve,reject)=>{image.addEventListener('loadeddata',resolve,{once:true});image.addEventListener('error',reject,{once:true})});
      editor.sync(); return {target:0,currentTime:image.currentTime,readyState:image.readyState,hidden:image.hidden};
    });
    assert.equal(camera.hidden,true); assert.ok(camera.readyState>=2);
    const waiting=await page.evaluate(async()=>{
      const host=document.createElement('div'),main=document.createElement('video');host.append(main);document.body.append(host);
      const player=new ConnectPreviewSource(main);
      // Simulate a reserved decoder that never emits readiness or error.
      player.preloader.dataset.source='audit-unavailable';player.preloadedTime=0;
      player.seek('audit-unavailable',0,{autoplay:true});
      await new Promise(resolve=>setTimeout(resolve,1200));
      const result={observedAfterMs:1200,reservationPending:Boolean(player.waitingForPreload),mainPaused:main.paused};
      player.pause();return result;
    });
    assert.equal(waiting.reservationPending,true);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({browser:browser.version(),retained,brollSeeks,loop,camera,waiting},null,2));
    await page.evaluate(()=>URL.revokeObjectURL(auditURL));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
