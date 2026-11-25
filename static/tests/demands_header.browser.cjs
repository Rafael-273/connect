const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH});
 try{
  for(const width of [320,390,430,768,1440]){
   const mobile=width<768,context=await browser.newContext({viewport:{width,height:844},isMobile:mobile,hasTouch:mobile});
   await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:new URL(process.env.FILADELFIA_QA_URL).hostname,path:'/'}]);
   const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(process.env.FILADELFIA_QA_URL);await page.locator('#demands-view-toggle').waitFor();
   const view=page.locator('#demands-view-toggle'),create=page.locator('#demands-create-toggle'),menu=page.locator('#demands-view-menu');
   if(mobile){
    const bounds=await page.evaluate(()=>{const rect=s=>document.querySelector(s).getBoundingClientRect().toJSON();return {header:rect('.demands-hub-header > section'),title:rect('.demands-hub-header h1'),view:rect('#demands-view-toggle'),create:rect('#demands-create-toggle'),scrollWidth:document.documentElement.scrollWidth,titleOverflow:document.querySelector('.demands-hub-header h1').scrollWidth>document.querySelector('.demands-hub-header h1').clientWidth}});
    assert.ok(bounds.view.top>=bounds.header.top&&bounds.view.bottom<=bounds.header.bottom,JSON.stringify(bounds));
    assert.ok(bounds.title.right<=bounds.view.left+1,JSON.stringify(bounds));assert.ok(bounds.scrollWidth<=width+1&&!bounds.titleOverflow,JSON.stringify(bounds));
   }
   const filter=page.locator('#demands-filter-panel');
   await page.locator('#demands-filter-toggle').click();
   const filterBounds=await filter.evaluate(node=>({top:node.getBoundingClientRect().top,bottom:node.getBoundingClientRect().bottom,height:node.clientHeight,scrollHeight:node.scrollHeight,overflow:getComputedStyle(node).overflowY,actions:node.querySelector('.demands-filter-actions').getBoundingClientRect().toJSON()}));
   assert.ok(filterBounds.scrollHeight>filterBounds.height&&filterBounds.overflow==='auto'&&filterBounds.bottom<=844&&filterBounds.actions.bottom<=filterBounds.bottom+1,JSON.stringify(filterBounds));
   await filter.evaluate(node=>{node.scrollTop=node.scrollHeight});
   assert.equal(await filter.locator('input[name="period_to"]').isVisible(),true);
   await page.locator('#demands-filter-toggle').click();
   assert.equal(await menu.getAttribute('inert'),'');await view.click();
   assert.equal(await view.getAttribute('aria-expanded'),'true');
   if(mobile){
    for(const label of ['Todos','Tipos','Calendário'])assert.equal(await menu.getByText(label,{exact:true}).isVisible(),true);
    const trigger=await view.boundingBox(),dropdown=await menu.boundingBox();assert.ok(dropdown.y>=trigger.y+trigger.height,JSON.stringify({trigger,dropdown}));assert.ok(dropdown.x>=0&&dropdown.x+dropdown.width<=width+1);
   }
   await create.click();assert.equal(await view.getAttribute('aria-expanded'),'false');assert.equal(await page.locator('#demands-create-menu').isVisible(),true);
   assert.equal(await page.locator('#demands-create-menu').getByText('Demanda',{exact:true}).isVisible(),true);
   assert.equal(await page.locator('#demands-create-menu').getByText('Evento',{exact:true}).isVisible(),true);
   for(const [label,id] of [['Demanda','demand'],['Evento','event']]){
    if(!(await page.locator('#demands-create-menu').isVisible()))await create.click();
    await page.locator('#demands-create-menu').getByText(label,{exact:true}).click();
    const modal=page.locator(`#demands-modal-${id}.active`);await modal.waitFor();await page.waitForTimeout(300);
    const box=await modal.locator('.demands-create-header').evaluate(n=>{const r=s=>n.querySelector(s).getBoundingClientRect().toJSON();return {header:n.getBoundingClientRect().toJSON(),icon:r('.demands-modal-icon'),heading:r('.demands-create-heading'),close:r('.demands-modal-close'),overflow:n.querySelector('h2').scrollWidth>n.querySelector('h2').clientWidth}});
    assert.ok(box.icon.right<=box.heading.left+1&&box.heading.right<=box.close.left+1&&!box.overflow,JSON.stringify(box));
    if(mobile){assert.ok(box.close.width>=44&&box.close.height>=44);if(process.env.FILADELFIA_QA_SCREENSHOT&&id==='demand')await modal.screenshot({path:`${process.env.FILADELFIA_QA_SCREENSHOT}-modal-${width}.png`});}
    await modal.locator('[data-close-modal]').last().click();
   }
   await create.click();await page.keyboard.press('Escape');assert.equal(await page.locator('#demands-create-menu').isVisible(),false);
   await view.click();await page.keyboard.press('Escape');assert.equal(await view.getAttribute('aria-expanded'),'false');assert.equal(await menu.getAttribute('inert'),'');
   assert.deepEqual(errors,[]);
   if(process.env.FILADELFIA_QA_SCREENSHOT&&mobile){await view.click();await page.waitForTimeout(250);await page.screenshot({path:`${process.env.FILADELFIA_QA_SCREENSHOT}-${width}.png`})}
   await context.close();
  }
  console.log('PASS header placement, dropdown labels, focus dismissal and create menu at mobile and desktop widths');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
