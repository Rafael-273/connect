const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH});
 try{
  for(const mobile of [false,true]){
   const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:900},isMobile:mobile,hasTouch:mobile});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:new URL(process.env.FILADELFIA_QA_URL).hostname,path:'/'}]);
   const page=await context.newPage(),errors=[],writes=[];
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{if(request.method()==='POST')writes.push(request.url())});
   await page.goto(process.env.FILADELFIA_QA_URL);
   const chrome=await page.evaluate(()=>({root:getComputedStyle(document.documentElement).backgroundColor,body:getComputedStyle(document.body).backgroundColor,header:getComputedStyle(document.querySelector('header')).backgroundColor,theme:document.querySelector('meta[name="theme-color"]').content}));
   assert.deepEqual(chrome,{root:'rgb(8, 8, 9)',body:'rgb(8, 8, 9)',header:'rgb(8, 8, 9)',theme:'#080809'});
   const clip=page.locator('.editor-track--video [data-clip-key]').first();await clip.waitFor();
   const select=async node=>{if(mobile)await node.tap();else await node.click()};
   const emptyPoint=async()=>page.evaluate(()=>{
    const layout=document.querySelector('.preview-layout'),card=document.querySelector('.preview-player-card'),r=layout.getBoundingClientRect();
    for(let y=r.top+8;y<r.bottom-8;y+=12)for(let x=r.left+8;x<r.right-8;x+=12){
     const target=document.elementFromPoint(x,y);if(target===layout||target===card)return {x,y};
    }
    return null;
   });
   const clear=async()=>{await page.waitForTimeout(350);const point=await emptyPoint();assert.ok(point,'empty preview area must be reachable');if(mobile)await page.touchscreen.tap(point.x,point.y);else await page.mouse.click(point.x,point.y)};
   await select(clip);await page.locator('#preview-transform-box.is-active').waitFor();
   assert.ok(await page.locator('#editor-tracks .is-selected').count()>0);
   assert.equal(await page.locator('[data-preview-adjustment="position"], [data-mobile-adjustment="position"], [data-move-start], [data-move-end]').count(),0);
   if(mobile){
    for(const category of ['framing','color']){
     const action=page.locator(`[data-mobile-adjustment="${category}"]`);assert.equal(await action.isVisible(),true);
     await action.tap();await page.waitForSelector('#review-panel.is-open');
     assert.equal(await page.locator(`.preview-decision[data-adjustment-category="${category}"]:not([hidden])`).count()>0,true);
     assert.equal(await page.locator('#review-panel .adjustment-tabs').isVisible(),false);
     assert.equal(await page.locator('#review-panel .preview-adjustments__head strong').textContent(),{framing:'Enquadramento',color:'Cor'}[category]);
     await page.locator('#close-review-panel').tap();
    }
    assert.equal(await page.locator('#mobile-split').isVisible(),true);
    assert.equal(await page.locator('.editor-mobile-actions #delete-segment').isVisible(),true);
   }
   if(!mobile){
    assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
    assert.equal(await page.locator('[data-preview-adjustment][aria-pressed="true"]').count(),0);
    for(const category of ['framing','color']){
     const action=page.locator(`[data-preview-adjustment="${category}"]`);assert.equal(await action.isVisible(),true);
     await action.click();
     assert.equal(await action.getAttribute('aria-pressed'),'true');
     assert.equal(await page.locator('#review-panel .adjustment-tabs').isVisible(),false);
     assert.equal(await page.locator('#review-panel .preview-adjustments__head strong').textContent(),{framing:'Enquadramento',color:'Cor'}[category]);
     assert.equal(await page.locator(`.preview-decision[data-adjustment-category="${category}"]:not([hidden])`).count()>0,true);
    }
    for(const name of ['add-timeline-media','add-timeline-text','add-timeline-music'])assert.equal(await page.locator(`[data-preview-tool="${name}"]`).isVisible(),true);
   }
   // Clicking the preview itself must preserve the selected take.
   await page.locator('.preview-transform-box__move').click({position:{x:80,y:80}});
   assert.ok(await page.locator('#editor-tracks .is-selected').count()>0);
   await clear();
   assert.equal(await page.locator('#editor-tracks .is-selected').count(),0);
   assert.equal(await page.locator('#preview-transform-box.is-active').count(),0);
   if(!mobile)assert.equal(await page.locator('[data-preview-adjustment]:visible').count(),0);
   assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
   assert.equal(await page.locator('#delete-segment').isDisabled(),true);
   const caption=page.locator('.editor-track--caption [data-clip-key]').first();await select(caption);
   assert.equal(await page.locator('#review-panel').getAttribute('aria-hidden'),'true');
   const captionsAction=page.locator(mobile?'[data-mobile-adjustment="captions"]':'[data-preview-adjustment="captions"]');
   assert.equal(await captionsAction.isVisible(),true);await select(captionsAction);
   assert.equal(await page.locator('#review-panel .adjustment-tabs').isVisible(),false);
   assert.equal(await page.locator('#review-panel .preview-adjustments__head strong').textContent(),'Legendas');
   await page.locator('.transcript-card').waitFor({state:'visible'});
   await select(page.locator('#close-review-panel'));
   const editCaption=page.locator(mobile?'[data-caption-edit="mobile"]':'[data-caption-edit="desktop"]');
   assert.equal(await editCaption.isVisible(),true);await select(editCaption);
   await page.waitForSelector('#subtitle-edit-modal.is-open');await select(page.locator('#cancel-subtitle-edit'));
   assert.ok(await page.locator('#editor-tracks .is-selected').count()>0);
   await clear();assert.equal(await page.locator('#editor-tracks .is-selected').count(),0);
   assert.equal(writes.length,0);assert.deepEqual(errors,[]);
   await context.close();
  }
  console.log('PASS clicking empty preview space clears take and caption selection on desktop and mobile');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
