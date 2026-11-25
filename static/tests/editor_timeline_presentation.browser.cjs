const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url),shots=process.env.FILADELFIA_QA_TIMELINE_SHOTS;
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  for(const [cuts,mobile] of [[4,false],[36,true],[125,false]]){
   const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},isMobile:mobile,hasTouch:mobile,serviceWorkers:'block'});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
   const page=await context.newPage(),errors=[],posts=[],thumbnails=new Set();
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{if(request.method()==='POST')posts.push(request.url())});
   await page.route('**/qa-timeline/**',route=>{
    const source=new URL(route.request().url());
    if(source.searchParams.has('thumbnail')){
     thumbnails.add(source.pathname);
     return route.fulfill({contentType:'image/svg+xml',headers:{'Cache-Control':'public, max-age=3600'},body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="135"><rect width="240" height="135" fill="#39617d"/><path d="M0 111 L58 60 96 74 150 34 240 105 240 135 0 135Z" fill="#638697"/><circle cx="196" cy="33" r="13" fill="#aabcc6"/></svg>'});
    }
    if(source.searchParams.has('waveform'))return route.fulfill({json:{schema:'connect.waveform.v1',durationMs:5000,peaks:Array.from({length:800},(_,i)=>(.15+.75*Math.abs(Math.sin(i*.47)*Math.cos(i*.19)) ))}});
    return route.abort();
   });
   await page.route(url,async route=>{
    const response=await route.fetch(),html=await response.text();
    const replaced=html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,before,raw,after)=>{
     const t=JSON.parse(raw),clip=t.clips[0],original=t.assets.find(asset=>asset.id===clip.asset_id);
     const durations=Array.from({length:cuts},(_,index)=>[4000,1100,380,2200,900][index%5]);
     t.assets=durations.map((_,index)=>({...original,id:'qa-take-'+index,name:'Take '+(index+1),url:'/qa-timeline/take-'+index+'.mp4'}));
     let cursor=0;
     t.clips=durations.map((duration,index)=>{
      const start=cursor;
      cursor+=duration;
      return {...clip,id:'qa-'+index,asset_id:t.assets[index].id,block:null,timeline_in_ms:start,timeline_out_ms:cursor,master_in_ms:start,master_out_ms:cursor,source_in_ms:0,source_out_ms:duration};
     });
     t.sequence.duration_ms=cursor;t.review_master_url=null;t.review_proxy_status='SOURCE';t.review_rendered_layers=[];
     const cueCount=Math.floor((cursor-1400)/800);
     t.captions=['pt','en'].flatMap((language,track)=>Array.from({length:cueCount},(_,index)=>({
      id:10000*(track+1)+index,track_id:track+1,language,is_source:track===0,start_ms:index===0?0:index*800+1400,end_ms:Math.min(cursor,index===0?2000:index*800+2110),
      text:(track?'Welcome to the opening of our new site':'Bem-vindos à inauguração do nosso novo espaço'),
     })));
     t.has_music=true;t.music={volume:.15,tracks:[{id:1,name:'Trilha ambiente',url:'/qa-timeline/music.mp4',duration_ms:5000,start_ms:0,end_ms:cursor,source_in_ms:0}]};
     return before+JSON.stringify(t)+after;
    });
    assert.notEqual(html,replaced);
    return route.fulfill({response,body:replaced});
   });
   await page.goto(url);
   const take=page.locator('.editor-track--video [data-clip-key]').first(), captions=page.locator('.editor-track--caption [data-clip-key]');
   await take.waitFor();
   await page.evaluate(()=>{window.clipNodes=[...document.querySelectorAll('[data-clip-key]')];window.head=document.querySelector('.editor-playhead');window.editSnapshot=clipNodes.map(node=>[node.dataset.clipKey,node._timeline.item.start,node._timeline.item.end])});
   const inspect=()=>page.evaluate(()=>({
    retained:clipNodes.every(node=>node.isConnected)&&head===document.querySelector('.editor-playhead'),
    unchanged:JSON.stringify(editSnapshot)===JSON.stringify(clipNodes.map(node=>[node.dataset.clipKey,node._timeline.item.start,node._timeline.item.end])),
    density:[...document.querySelectorAll('.editor-track--caption .editor-clip__text')].filter(node=>getComputedStyle(node).display!=='none').length,
    artwork:document.querySelectorAll('.editor-track__clip.has-thumbnail').length,
   }));
   if(cuts>4)assert.equal((await inspect()).density,0);
   assert.ok(await captions.count()>0);
   assert.equal(await page.locator('.editor-track--caption').count(),2);
   const zoom=value=>page.locator('#timeline-zoom-range').evaluate((input,value)=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}))},value);
   if(cuts>4){
    await zoom(16);await page.waitForFunction(()=>Number(document.getElementById('timeline-zoom-range').value)===16);
    await page.locator('#editor-scroller').evaluate(node=>node.scrollLeft=0);
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('.editor-track--caption .editor-clip__text')).display!=='none');
    if(cuts===125){
     await page.waitForFunction(()=>document.querySelector('.editor-track--video .has-thumbnail'));
     assert.ok(thumbnails.size<cuts/4,`loaded ${thumbnails.size} thumbnail sources for ${cuts} cuts`);
     const previous=new Set(thumbnails);
     await page.locator('#editor-scroller').evaluate(node=>node.scrollLeft=node.scrollWidth/2);
     await page.waitForFunction(()=>[...document.querySelectorAll('.editor-track--video .has-thumbnail')].some(node=>node._timeline.item.start>30000));
     assert.ok([...thumbnails].some(source=>!previous.has(source)));
     await page.locator('#editor-scroller').evaluate(node=>node.scrollLeft=0);
    }
   }
   const screenshot=async mode=>{
    const transport=await page.locator('.editor-timeline__transport').boundingBox(),{width,height}=page.viewportSize();
    await page.screenshot({path:path.join(shots,`${mobile?'mobile':'desktop'}-${mode}.png`),clip:{x:0,y:Math.floor(transport.y),width,height:height-Math.floor(transport.y)}});
   };
   if(shots&&cuts!==4)await screenshot('detalhado');
   const open=async()=>{
    if(mobile){await page.locator('#mobile-tools').tap();await page.locator('#mobile-timeline-display').tap()}
    else await page.locator('#timeline-display-open').click();
   };
   const close=()=>page.locator('[data-close-display]').click();
   const videoRow=page.locator('.editor-track--video'),audioKey=await page.locator('.editor-track--audio').getAttribute('data-track-key');
   const before=(await videoRow.boundingBox()).height;
   await open();await page.locator('[data-compact]').click();
   await page.locator(`[data-track="${audioKey}"]`).click();await close();
   assert.ok((await videoRow.boundingBox()).height<before);
   assert.equal(await page.locator('.editor-track--audio').evaluate(node=>node.classList.contains('is-track-collapsed')),true);
   if(shots&&cuts!==4){
    await zoom(1);await page.waitForTimeout(200);
    await screenshot('compacto');
    await zoom(16);await page.waitForTimeout(150);
    await page.locator('#editor-scroller').evaluate(node=>node.scrollLeft=0);
   }
   const b=await take.boundingBox();
   if(mobile)await page.touchscreen.tap(b.x+Math.min(30,b.width/2),b.y+b.height/2);
   else await take.click({position:{x:Math.min(30,b.width/2),y:b.height/2}});
   await open();await page.locator('[data-focus-track]').click();await close();
   assert.equal(await videoRow.evaluate(row=>row.classList.contains('is-track-focused')),true);
   assert.deepEqual(await inspect().then(({retained,unchanged})=>({retained,unchanged})),{retained:true,unchanged:true});
   assert.deepEqual(posts.filter(request=>!request.endsWith('/session/')),[]);
   await page.reload();await take.waitFor();
   assert.equal(await page.locator('#editor-scroller').evaluate(node=>node.classList.contains('is-timeline-compact')),true);
   assert.equal(await page.locator('.editor-track--audio').evaluate(node=>node.classList.contains('is-track-collapsed')),true);
   await open();await page.locator('[data-detailed]').click();await page.locator(`[data-track="${audioKey}"]`).click();await close();
   assert.equal(await page.locator('.editor-track--audio').evaluate(node=>node.classList.contains('is-track-collapsed')),false);
   assert.deepEqual(errors,[]);
   console.log('PASS timeline presentation',JSON.stringify({cuts,captions:await captions.count(),mobile,thumbnailSources:thumbnails.size}));
   await context.close();
  }
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
