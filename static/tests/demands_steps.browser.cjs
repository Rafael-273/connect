const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH});
 try{
  for(const width of [320,390,430,1024]){
   const mobile=width<=640,context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:new URL(process.env.FILADELFIA_QA_URL).hostname,path:'/'}]);
   const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.FILADELFIA_QA_URL);const steps=page.locator('.demand-task-step');await steps.first().waitFor();assert.equal(await steps.count(),2);
   if(mobile){
    const order=await page.evaluate(()=>({summary:document.querySelector('.demand-fact-summary').getBoundingClientRect().bottom,briefing:document.querySelector('.demand-detail-grid > .space-y-4').getBoundingClientRect().top}));
    assert.ok(order.summary<=order.briefing,JSON.stringify(order));
    const toggle=page.locator('[data-demand-facts-toggle]'),fieldsGroup=page.locator('.demand-fact-summary__fields');
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');assert.equal(await fieldsGroup.isVisible(),false);
    assert.equal(await page.locator('.demand-status-card').isVisible(),true);
    await toggle.tap();assert.equal(await toggle.getAttribute('aria-expanded'),'true');assert.equal(await fieldsGroup.isVisible(),true);
    const title=page.locator('input.demand-title-input');
    const titleStyle=await title.evaluate(node=>({height:node.getBoundingClientRect().height,font:parseFloat(getComputedStyle(node).fontSize),line:parseFloat(getComputedStyle(node).lineHeight)}));
    assert.ok(titleStyle.height>=44&&titleStyle.line>=titleStyle.font*1.35,JSON.stringify(titleStyle));
    const measurements=await steps.evaluateAll(nodes=>nodes.map(node=>{
     const box=selector=>node.querySelector(selector)?.getBoundingClientRect().toJSON();
     return {card:node.getBoundingClientRect().toJSON(),role:box('.demand-task-role'),actions:box('.demand-task-header > div'),meta:box('.demand-task-meta'),desc:box('.demand-task-desc'),check:box('.demand-task-check'),order:box('.demand-task-order'),delivery:box('.demand-review-link')};
    }));
    for(const m of measurements){
     assert.ok(m.actions.top>=m.desc.bottom-1,JSON.stringify(m));
     if(m.delivery){assert.ok(m.delivery.right<=m.actions.left+1,JSON.stringify(m));assert.ok(m.delivery.bottom>m.actions.top&&m.actions.bottom>m.delivery.top,JSON.stringify(m));assert.ok(m.actions.left-m.delivery.right<=12,JSON.stringify(m));}
     assert.ok(m.meta.top>=m.role.bottom-1&&m.desc.top>=m.meta.bottom-1,JSON.stringify(m));
     assert.ok(m.check.top>=m.order.bottom-1,JSON.stringify(m));
     for(const v of [m.role,m.actions,m.meta,m.desc,m.check,m.delivery].filter(Boolean))assert.ok(v.left>=m.card.left-1&&v.right<=m.card.right+1,JSON.stringify(m));
    }
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    const fields=await page.locator('.demand-fact-card select, .demand-fact-card input:not([type="hidden"])').evaluateAll(nodes=>nodes.map(node=>({height:node.getBoundingClientRect().height,fontSize:parseFloat(getComputedStyle(node).fontSize),lineHeight:parseFloat(getComputedStyle(node).lineHeight),fontFamily:getComputedStyle(node).fontFamily})));
    assert.ok(fields.length>=3);assert.ok(fields.every(field=>field.height>=48&&field.fontSize>=16&&field.lineHeight>=field.fontSize*1.35&&field.fontFamily.includes('system')),JSON.stringify(fields));
    await toggle.tap();assert.equal(await toggle.getAttribute('aria-expanded'),'false');assert.equal(await fieldsGroup.isVisible(),false);
    await steps.first().locator('[data-task-desc-add]').tap();
    await steps.first().locator('[data-task-desc-form]:not(.hidden) textarea').waitFor({state:'visible'});
    await steps.first().locator('[data-task-desc-cancel]').tap();
   }else {assert.equal(await steps.first().evaluate(node=>getComputedStyle(node).display),'flex');assert.equal(await page.locator('.demand-fact-summary__fields').isVisible(),true);assert.equal(await page.locator('[data-demand-facts-toggle]').isVisible(),false)}
   assert.deepEqual(errors,[]);
   if(process.env.FILADELFIA_QA_SCREENSHOT&&mobile)await steps.first().locator('..').screenshot({path:`${process.env.FILADELFIA_QA_SCREENSHOT}-${width}.png`});
   await context.close();
  }
  console.log('PASS readable stage metadata, actions and inline editing at mobile and desktop widths');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
