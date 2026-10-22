// Real pointer gestures, frame controls, persistence and history on a disposable project.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core'),assert=require('assert/strict');
(async()=>{
 const url=process.env.FILADELFIA_QA_URL,address=new URL(url);
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
 const context=await browser.newContext({viewport:{width:1600,height:1000}});
 await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
 const page=await context.newPage(),errors=[],posts=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/subtitles/'))posts.push(JSON.parse(request.postData()))});
 await page.goto(url);await page.waitForSelector('.editor-track--caption [data-clip-key]');
 const first=page.locator('.editor-track--caption [data-clip-key]').first();
 const timings=()=>first.evaluate(node=>({start:node._timeline.item.start,end:node._timeline.item.end}));
 const saved=()=>page.waitForFunction(()=>document.getElementById('editor-save-status').textContent==='Salvo');
 const ruler=await page.locator('.editor-ruler__scale').boundingBox(),box=await first.boundingBox();
 // Right edge resized to the next caption's start, from three pixels away.
 await page.mouse.move(box.x+box.width-2,box.y+box.height/2);await page.mouse.down();
 await page.mouse.move(box.x+box.width-2+ruler.width*.1-3,box.y+box.height/2,{steps:5});
 await page.waitForSelector('.editor-snap-guide:not([hidden])');
 assert.match(await page.locator('.editor-drag-feedback').textContent(),/Encaixe/);
 await page.mouse.up();await saved();assert.deepEqual(await timings(),{start:1000,end:3000});assert.equal(posts.length,1);
 await page.keyboard.press('Control+z');await saved();await page.waitForFunction(()=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.end===2000);
 // Move the clip so its end aligns with the next caption, preserving its duration.
 const moveBox=await first.boundingBox(),x=moveBox.x+moveBox.width*.7,y=moveBox.y+moveBox.height/2;
 await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+ruler.width*.1-3,y,{steps:5});
 await page.waitForSelector('.editor-snap-guide:not([hidden])');await page.mouse.up();await saved();
 await page.reload();await page.waitForSelector('.editor-track--caption [data-clip-key]');assert.deepEqual(await timings(),{start:2000,end:3000});
 console.log('PASS magnetic resize/move, one save per gesture, persistence after reload');
 // Selection + exact frame edit at high zoom. Editing an input must not trigger shortcuts.
 await first.click({position:{x:8,y:10}});await page.locator('#timeline-zoom-range').evaluate(input=>{input.value=16;input.dispatchEvent(new Event('input',{bubbles:true}))});await page.keyboard.press('t');await page.waitForSelector('#precision-dialog[open]');
 await page.locator('#precision-start').fill('61');await page.locator('#precision-end').fill('100');await page.locator('#precision-form button').last().click();assert.match(await page.locator('#precision-error').textContent(),/sobrepor/);await page.locator('#precision-end').fill('89');
 if(process.env.FILADELFIA_QA_SCREENSHOT)await page.screenshot({path:process.env.FILADELFIA_QA_SCREENSHOT,fullPage:true});
 assert.match(await page.locator('#precision-timecode').textContent(),/00:00:02:01 → 00:00:02:29/);
 await page.locator('#precision-form button[type=button]').click();
 await page.keyboard.press('t');await page.locator('#precision-start').fill('61');await page.locator('#precision-end').fill('89');
 await page.locator('#precision-form button').last().click();await saved();
 await page.waitForFunction(()=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.start===2033);
 assert.deepEqual(await timings(),{start:2033,end:2967});
 await page.keyboard.press('Control+z');await saved();await page.waitForFunction(()=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.start===2000);
 await page.keyboard.press('Control+Shift+z');await saved();await page.waitForFunction(()=>document.querySelector('.editor-track--caption [data-clip-key]')._timeline.item.start===2033);
 console.log('PASS frame interval, exclusive end, undo/redo through shared persistence');
 await page.locator('#timeline-zoom-range').evaluate(input=>{input.value=1;input.dispatchEvent(new Event('input',{bubbles:true}))});await page.waitForTimeout(100);
 // Alt temporarily bypasses magnetism; cancellation must discard the transient gesture.
 const beforePosts=posts.length,before=await timings(),b=await first.boundingBox();
 await page.keyboard.down('Alt');await page.mouse.move(b.x+b.width*.7,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width*.7+3,b.y+b.height/2,{steps:3});await page.waitForTimeout(50);
 assert.equal(await page.locator('.editor-snap-guide:not([hidden])').count(),0);
 await first.dispatchEvent('pointercancel',{pointerId:1});await page.mouse.up();await page.keyboard.up('Alt');
 assert.equal(await page.locator('.editor-drag-feedback').count(),0);assert.deepEqual(await timings(),before);assert.equal(posts.length,beforePosts);
 await page.keyboard.press('s');assert.equal(await page.locator('#timeline-snap').getAttribute('aria-pressed'),'false');await page.keyboard.press('s');
 await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');assert.match(await page.locator('#preview-time').textContent(),/^00:00:00:01/);
 await page.keyboard.press('?');await page.waitForSelector('#shortcuts-dialog[open]');await page.keyboard.press('s');assert.equal(await page.locator('#timeline-snap').getAttribute('aria-pressed'),'true');await page.keyboard.press('Escape');
 await page.locator('#delete-segment').scrollIntoViewIfNeeded();const tool=await page.locator('#delete-segment').boundingBox(),toolbar=await page.locator('.editor-timeline__bar').boundingBox();assert.ok(tool.y>=toolbar.y&&tool.y+tool.height<=toolbar.y+toolbar.height+1);
 assert.deepEqual(errors,[]);console.log('PASS Alt bypass, cancelled gesture without save, S toggle, frame transport, modal shortcut guard');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
