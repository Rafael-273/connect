// Trusted touch sequences against an isolated Django editor fixture.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const url=process.env.FILADELFIA_QA_URL,address=new URL(url);
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,deviceScaleFactor:1});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&/\/subtitles\//.test(r.url()))posts.push(r.postData())});
  await page.goto(url);await page.waitForSelector('.editor-track--caption [data-clip-key]');
  const geometry=await page.evaluate(()=>{const scroller=document.querySelector('#editor-scroller').getBoundingClientRect(),lane=document.querySelector('.editor-track--caption .editor-track__lane').getBoundingClientRect(),ruler=document.querySelector('.editor-ruler__scale').getBoundingClientRect();return {scroller:scroller.left,lane:lane.left,ruler:ruler.left,labels:[...document.querySelectorAll('.editor-track__label')].map(n=>getComputedStyle(n).display)}});
  assert.ok(Math.abs(geometry.lane-geometry.scroller)<=1&&Math.abs(geometry.ruler-geometry.lane)<=1,JSON.stringify(geometry));
  assert.ok(geometry.labels.every(display=>display==='none'));
  await page.locator('#mobile-tools').tap();
  const visibility=page.locator('[data-mobile-caption-visibility]').first();
  assert.equal(await visibility.getAttribute('aria-pressed'),'true');await visibility.tap();
  await page.waitForFunction(()=>document.querySelector('.caption-visibility')?.getAttribute('aria-pressed')==='false');
  await page.locator('#mobile-tools').tap();assert.equal(await page.locator('[data-mobile-caption-visibility]').first().getAttribute('aria-pressed'),'false');
  await page.locator('[data-mobile-caption-visibility]').first().tap();
  await page.waitForFunction(()=>document.querySelector('.caption-visibility')?.getAttribute('aria-pressed')==='true');
  posts.length=0;
  const first=page.locator('.editor-track--caption [data-clip-key]').first();
  const timing=()=>first.evaluate(n=>({start:n._timeline.item.start,end:n._timeline.item.end}));
  const saved=()=>page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
  const zoom=async value=>{await page.locator('#timeline-zoom-range').evaluate((n,v)=>{n.value=v;n.dispatchEvent(new Event('input',{bubbles:true}))},value);await page.waitForTimeout(150);await page.locator('#editor-scroller').evaluate(n=>{n.scrollLeft=0;n.scrollTop=0})};
  const cdp=await context.newCDPSession(page);
  const touch=(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points.map(([x,y,id=1])=>({x,y,id}))});
  const drag=async(x,y,dx,dy=0,cancel=false)=>{await touch('touchStart',[[x,y]]);for(let n=1;n<=6;n++){await touch('touchMove',[[x+dx*n/6,y+dy*n/6]]);await page.waitForTimeout(20)}await touch(cancel?'touchCancel':'touchEnd',[])};
  const beforeError=await page.locator('.preview-stage').boundingBox();
  await page.evaluate(()=>{const error=document.querySelector('#preview-load-error');error.classList.remove('hidden');document.querySelector('#preview-load-error-message').textContent='O carregamento está demorando. Tente novamente.';window.dispatchEvent(new Event('resize'))});
  await page.waitForTimeout(100);
  const duringError=await page.locator('.preview-stage').boundingBox(),errorBox=await page.locator('#preview-load-error').boundingBox();
  assert.ok(Math.abs(beforeError.width-duringError.width)<=1&&Math.abs(beforeError.height-duringError.height)<=1);
  assert.ok(errorBox.x>=0&&errorBox.x+errorBox.width<=390&&errorBox.width>=250);
  await page.evaluate(()=>document.querySelector('#preview-load-error').classList.add('hidden'));
  // Selecting items or opening the keyboard must not shrink the workspace.
  await page.locator('.editor-track--video [data-clip-key]').first().tap();
  await first.tap();await page.waitForTimeout(150);
  const selectedPreview=await page.locator('.preview-stage').boundingBox();
  assert.ok(Math.abs(beforeError.width-selectedPreview.width)<=1&&Math.abs(beforeError.height-selectedPreview.height)<=1);
  await page.locator('[data-caption-edit="mobile"]').tap();await page.waitForSelector('#subtitle-edit-modal.is-open');
  await page.locator('#subtitle-edit-input').focus();
  await page.evaluate(()=>{Object.defineProperty(window.visualViewport,'height',{configurable:true,value:500});window.visualViewport.dispatchEvent(new Event('resize'))});
  await page.waitForTimeout(150);
  const keyboardPreview=await page.locator('.preview-stage').boundingBox();
  assert.ok(Math.abs(selectedPreview.width-keyboardPreview.width)<=1&&Math.abs(selectedPreview.height-keyboardPreview.height)<=1);
  assert.equal(await page.evaluate(()=>document.body.style.getPropertyValue('--editor-viewport-height')),'500px');
  await page.evaluate(()=>{document.activeElement.blur();delete window.visualViewport.height;window.visualViewport.dispatchEvent(new Event('resize'))});
  await page.locator('#cancel-subtitle-edit').tap();
  await page.locator('#mobile-add').tap();await page.waitForSelector('#mobile-tools-modal.is-open');
  assert.equal(await page.locator('#add-timeline-text').isVisible(),true);
  assert.equal(await page.locator('#timeline-shortcuts').isVisible(),false);
  assert.equal(await page.locator('[data-tool="blade"]').isVisible(),false);
  await page.locator('#add-timeline-text').tap();await page.waitForSelector('#text-element-modal.is-open');
  assert.equal(await page.locator('#mobile-tools-modal').isVisible(),false);
  await page.locator('#text-element-modal button').filter({hasText:'Cancelar'}).tap();
  await zoom(4);
  let b=await first.boundingBox();await page.touchscreen.tap(b.x+b.width/2,b.y+b.height/2);
  assert.equal(await page.locator('#mobile-properties').isEnabled(),true);
  assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
  const original=await timing();
  const lane=await page.locator('.editor-track--caption .editor-track__lane').boundingBox();
  await drag(130,lane.y+lane.height/2,-90,0);
  assert.ok(await page.locator('#editor-scroller').evaluate(n=>n.scrollLeft)>50);
  assert.deepEqual(await timing(),original);assert.equal(posts.length,0);
  await page.locator('#editor-scroller').evaluate(n=>{n.scrollLeft=0;n.scrollTop=0;n.style.flex='none';n.style.height='100px'});
  b=await first.boundingBox();await drag(130,lane.y+lane.height/2,0,-30);
  assert.ok(await page.locator('#editor-scroller').evaluate(n=>n.scrollTop)>15);
  await page.locator('#editor-scroller').evaluate(n=>{n.style.removeProperty('flex');n.style.removeProperty('height');n.scrollTop=0});
  b=await first.boundingBox();const y=b.y+b.height/2;
  await touch('touchStart',[[160,y,1],[260,y,2]]);
  await touch('touchMove',[[130,y,1],[300,y,2]]);await page.waitForTimeout(80);await touch('touchEnd',[]);
  assert.ok(Number(await page.locator('#timeline-zoom-range').inputValue())>4);
  assert.deepEqual(await timing(),original);assert.equal(posts.length,0);
  console.log('PASS touch selection, horizontal/vertical navigation and pinch without editorial writes');
  await zoom(4);await page.locator('#mobile-tools').tap();assert.equal(await page.locator('#mobile-move').count(),0);await page.locator('#mobile-tools-close').tap();b=await first.boundingBox();
  await drag(b.x+b.width/2,b.y+b.height/2,30);await saved();
  assert.ok((await timing()).start>original.start);assert.equal(posts.length,1);
  const moved=await timing();assert.equal(moved.end-moved.start,original.end-original.start);
  b=await first.boundingBox();await drag(b.x+b.width/2,b.y+b.height/2,25,0,true);await page.waitForTimeout(100);
  assert.deepEqual(await timing(),moved);assert.equal(posts.length,1);
  // Adding a second finger must cancel an in-progress edit before zooming.
  b=await first.boundingBox();const cx=b.x+b.width/2,cy=b.y+b.height/2;
  await touch('touchStart',[[cx,cy,1]]);await touch('touchMove',[[cx+15,cy,1]]);await page.waitForTimeout(40);
  await touch('touchStart',[[cx+15,cy,1],[100,cy,2]]);await touch('touchMove',[[cx+25,cy,1],[80,cy,2]]);await page.waitForTimeout(40);await touch('touchEnd',[]);
  assert.deepEqual(await timing(),moved);assert.equal(posts.length,1);
  // Right edge gets a larger touch hit zone; cancellation never persists.
  await zoom(3);b=await first.boundingBox();await drag(b.x+b.width-4,b.y+b.height/2,24);await saved();
  assert.ok((await timing()).end>moved.end);assert.equal(posts.length,2);
  const resized=await timing();
  await page.locator('.timeline-zoom-control [data-history="undo"]').tap();
  await page.waitForFunction(expected=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.end===expected,moved.end);await saved();
  await page.locator('.timeline-zoom-control [data-history="redo"]').tap();
  await page.waitForFunction(expected=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.end===expected,resized.end);await saved();
  await page.reload();await page.waitForSelector('.editor-track--caption [data-clip-key]');assert.deepEqual(await timing(),resized);
  console.log('PASS trusted touch move, resize, cancellation and persistence after reload');
  await first.tap();await page.locator('#mobile-tools').tap();await page.locator('#mobile-edit-caption').tap();
  await page.locator('#subtitle-edit-input').fill('Editada no celular');await page.locator('#save-subtitle-edit').tap();await saved();
  await page.reload();await page.waitForSelector('.editor-track--caption [data-clip-key]');assert.match(await first.textContent(),/Editada no celular/);
  await first.tap();await page.locator('#mobile-tools').tap();await page.locator('#timeline-precision').tap();await page.waitForSelector('#precision-dialog[open]');await page.locator('#precision-cancel').tap();
  await page.locator('[data-mobile-adjustment="captions"]').tap();await page.waitForSelector('#review-panel.is-open');
  const panel=await page.locator('#review-panel').boundingBox();assert.ok(panel.x>=0&&panel.x+panel.width<=391);assert.ok(panel.y>100);
  // Model Safari's visual viewport shrinking while the layout viewport stays tall.
  await page.evaluate(()=>document.body.style.setProperty('--editor-viewport-height','500px'));await page.waitForTimeout(300);
  const keyboardPanel=await page.locator('#review-panel').boundingBox();assert.ok(keyboardPanel.y>=0&&keyboardPanel.y+keyboardPanel.height<=501);
  await page.evaluate(()=>window.visualViewport.dispatchEvent(new Event('resize')));await page.waitForTimeout(300);
  await page.locator('#close-review-panel').tap();
  await page.locator('#mobile-tools').tap();await page.locator('#mobile-expand').tap();assert.equal(await page.locator('.editor-timeline').isVisible(),false);await page.locator('#mobile-tools').tap();await page.locator('#mobile-expand').tap();
  // Reuse the ruler's real seek handler, then split through the shared command.
  await zoom(4);b=await first.boundingBox();const ruler=await page.locator('.editor-ruler').boundingBox();
  await page.touchscreen.tap(b.x+b.width/2,ruler.y+ruler.height/2);await first.tap();
  const count=await page.locator('.editor-track--caption [data-clip-key]').count();await page.locator('#mobile-split').tap();
  await page.waitForFunction(n=>document.querySelectorAll('.editor-track--caption [data-clip-key]').length===n+1,count);await saved();
  console.log('PASS property sheet, preview expansion, touch ruler seek and selected-caption cut');
  await page.locator('.preview-stage').evaluate(n=>{n.style.setProperty('--preview-width','1080');n.style.setProperty('--preview-height','1920');window.dispatchEvent(new Event('resize'))});
  for(const [width,height] of [[320,568],[390,844],[430,932],[740,360],[390,500],[820,1180],[1440,900]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(200);
   const metrics=await page.evaluate(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth,height:innerHeight,bottom:document.querySelector('.editor-timeline').getBoundingClientRect().bottom,preview:document.querySelector('.preview-stage').getBoundingClientRect().height}));
   assert.ok(metrics.overflow<=width+1,JSON.stringify({width,height,metrics}));assert.ok(metrics.bottom<=height+2,JSON.stringify({width,height,metrics}));assert.ok(metrics.preview>0);
   if(width<=760){
    const transport=await page.locator('.editor-timeline__transport').boundingBox();assert.equal(transport.height,54);
    const play=await page.locator('#preview-play').boundingBox();assert.ok(Math.abs(play.x+play.width/2-width/2)<=1);
    for(const id of ['preview-previous','preview-play','preview-next']){
     const control=await page.locator('#'+id).boundingBox();assert.ok(control.y>=transport.y&&control.y+control.height<=transport.y+transport.height+1);assert.ok(control.width>=44&&control.height>=44);
    }
    const cut=await page.locator('#mobile-split').boundingBox();assert.ok(Math.abs(cut.x+cut.width/2-width/2)<=2,'cut must be centered');
    assert.equal(await page.locator('.editor-mobile-actions #mobile-move').count(),0);
    const bar=await page.locator('.editor-mobile-actions').boundingBox(),timeline=await page.locator('.editor-timeline').boundingBox();
    assert.ok(bar.y>=timeline.y+timeline.height-1);assert.ok(bar.y+bar.height<=height+1);
    assert.equal(await page.locator('.editor-timeline__bar').isVisible(),false);
    const labels=await page.locator('.editor-mobile-actions button:not([hidden])').evaluateAll(nodes=>nodes.map(node=>{const icon=node.querySelector('i').getBoundingClientRect(),label=[...node.querySelectorAll('span')].find(n=>!n.classList.contains('sr-only')).getBoundingClientRect();return {label:label.y,icon:icon.bottom}}));
    assert.ok(labels.every(value=>value.label>=value.icon));
    const stage=await page.locator('.preview-stage').boundingBox();assert.ok(Math.abs(stage.width/stage.height-9/16)<.02);
    assert.ok(stage.y+stage.height<=transport.y+1,'preview must remain above playback controls');
    if(height>=800){assert.ok(stage.height>=height*.45,'portrait preview should use the available height');assert.ok(timeline.height>=200,'keep the ruler and three touch-sized tracks visible')}
    for(const action of ['undo','redo']){const control=await page.locator(`.timeline-zoom-control [data-history="${action}"]`).boundingBox();assert.ok(control&&control.x+control.width<=width+1&&control.width>=44)}
    assert.equal(await page.locator('#timeline-zoom-increase').isVisible(),false)}
   if(width>760){
    const dimensions=await page.evaluate(()=>({timeline:document.querySelector('.editor-timeline').getBoundingClientRect().width,scroller:document.querySelector('.editor-timeline__scroller').getBoundingClientRect().width,select:document.querySelector('.editor-timeline-actions [data-tool="select"]')!==null,blade:document.querySelector('.editor-timeline-actions [data-tool="blade"]')!==null}));
    assert.ok(dimensions.scroller>=dimensions.timeline-20,JSON.stringify(dimensions));assert.ok(dimensions.select&&dimensions.blade);
    const vertical=await page.evaluate(()=>{const area=document.querySelector('.editor-timeline').getBoundingClientRect(),last=document.querySelector('#editor-tracks .editor-track:last-child').getBoundingClientRect(),scroller=document.querySelector('#editor-scroller');return {bottom:area.bottom,last:last.bottom,scrollHeight:scroller.scrollHeight,clientHeight:scroller.clientHeight}});
    assert.ok(vertical.bottom-vertical.last>=10&&vertical.bottom-vertical.last<=30,JSON.stringify(vertical));
    assert.ok(vertical.scrollHeight<=vertical.clientHeight+1,JSON.stringify(vertical));
    assert.equal(await page.locator('.editor-mobile-actions #delete-segment').count(),0);assert.equal(await page.locator('.editor-mobile-sheet #timeline-precision').count(),0);assert.equal(await page.locator('#timeline-snap').isVisible(),true)}
   if(process.env.FILADELFIA_QA_SCREENSHOT)await page.screenshot({path:`${process.env.FILADELFIA_QA_SCREENSHOT}-${width}.png`});
  }
  assert.deepEqual(errors,[]);console.log('PASS small/large phone, landscape, keyboard-sized viewport, tablet and desktop without page overflow or JS errors');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
