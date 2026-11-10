const assert=require('node:assert/strict');
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'/tmp/filadelfia-editor-browser/node_modules/playwright-core');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<div id="stage"><video id="main"></video></div><details id="panel"><summary>Câmeras</summary></details>');
 await page.addScriptTag({path:require('node:path').resolve(__dirname,'../external_media_multicam.js')});
 await page.evaluate(()=>{
  const alignment={status:'ALIGNED',rate:1,offset_ms:200,confidence:.9,validated_until_primary_ms:10000};
  window.t={sequence:{duration_ms:10000},cameras:[{id:'main',reference_source_id:'main',label:'Principal'},{id:'side',reference_source_id:'main',label:'Lateral',alignment,url:'/camera.mp4'}],camera_segments:[],decisions:[]};window.saved=[];
  window.editor=ConnectMulticam.create({panel:document.querySelector('#panel'),stage:document.querySelector('#stage'),mainVideo:document.querySelector('#main'),getTimeline:()=>t,getTime:()=>1000,seek:time=>window.seeked=time,save:async payload=>{saved.push(payload);if(payload.delete)t.camera_segments=[];else t.camera_segments=[{...payload,id:'selection',source_in_ms:payload.start_ms+200,rate:1}];editor.render()}});
  document.querySelector('#panel').open=true;
 });
 const form=page.locator('form').first();
 await form.locator('input').nth(0).fill('1.250');await form.locator('input').nth(1).fill('5.5');
 await form.getByRole('button',{name:'Adicionar alternância'}).click();
 await page.waitForFunction(()=>saved.length===1);
 assert.deepEqual(await page.evaluate(()=>saved[0]),{camera_source_id:'side',start_ms:1250,end_ms:5500});
 await page.locator('form').first().locator('input').nth(0).fill('2.000');
 await page.locator('form').first().locator('input').nth(2).fill('-300');
 await page.getByRole('button',{name:'Salvar câmera e limites'}).click();
 await page.waitForFunction(()=>saved.length===2);
 assert.equal((await page.evaluate(()=>saved[1])).offset_ms,-300);
 await page.getByRole('button',{name:'Usar principal / remover'}).click();
 await page.waitForFunction(()=>saved.length===3);
 assert.deepEqual(await page.evaluate(()=>saved[2]),{id:'selection',delete:true});
 assert.equal(await page.locator('#stage video').nth(1).evaluate(v=>v.muted),true);
 assert.deepEqual(errors,[]);
 console.log('Camera add, move, manual offset, removal and muted image layer: OK');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
