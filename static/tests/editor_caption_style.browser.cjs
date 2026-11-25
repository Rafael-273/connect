// Compare the editable caption pixels to an export frame produced by libass.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{
 const address=new URL(process.env.FILADELFIA_QA_URL);
 const fixture=JSON.parse(process.env.FILADELFIA_QA_CAPTION_CASE);
 const reference=fs.readFileSync(process.env.FILADELFIA_QA_CAPTION_FRAME).toString('base64');
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route(address.href,async route=>{
   const response=await route.fetch(),html=await response.text();
   const body=html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,raw,b)=>{
    const t=JSON.parse(raw),cue=t.captions[0];
    cue.text=fixture.texts.pt;
    t.caption_styles.translated=fixture.translatedStyle;
    t.captions.push({...cue,id:999999,track_id:999999,language:'en',is_source:false,text:fixture.texts.en});
    t.captions[1].text='Uma legenda longa ocupa a largura disponível '.repeat(6).trim();
    return a+JSON.stringify(t)+b;
   });
   assert.notEqual(body,html);
   await route.fulfill({response,body});
  });
  await page.goto(address.href);
  // The disposable editor fixture has no playable take. Compare only the caption layers.
  await page.addStyleTag({content:'#preview-load-error{display:none!important}'});
  const seek=ms=>page.locator('#preview-range').evaluate((range,ms)=>{range.value=ms;range.dispatchEvent(new Event('input'))},ms);
  const stage=page.locator('.preview-stage');
  await seek(1500);
  await page.waitForFunction(()=>!document.getElementById('preview-caption-translated').hidden);
  await page.evaluate(()=>document.fonts.ready);
  const readGlyphs=([preview,exported])=>Promise.all([preview,exported].map(async encoded=>{
   const image=new Image();image.src='data:image/png;base64,'+encoded;await image.decode();
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
   const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
   const {data}=ctx.getImageData(0,0,image.width,image.height);
   const colors=[(r,g,b)=>r>160&&g>115&&b<70,(r,g,b)=>r<75&&g>155&&b>65&&b<175];
   return colors.map(match=>{
    let left=image.width,right=-1,top=image.height,bottom=-1;
    for(let y=0;y<image.height;y++)for(let x=0;x<image.width;x++){
     const i=4*(y*image.width+x);
     if(!match(data[i],data[i+1],data[i+2]))continue;
     left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
    }
    return {width:(right-left+1)/image.width,height:(bottom-top+1)/image.height,bottom:bottom/image.height};
   });
  }));
  for(const [width,height] of [[390,844],[320,568],[1440,900]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(200);
   const screenshot=await stage.screenshot();
   const [live,render]=await page.evaluate(readGlyphs,[screenshot.toString('base64'),reference]);
   for(let i=0;i<render.length;i++){
    const actual=live[i],expected=render[i],context=JSON.stringify({width,language:i?'en':'pt',actual,expected});
    assert.ok(Math.abs(actual.width-expected.width)<.025,context);
    assert.ok(Math.abs(actual.height-expected.height)<.017,context);
    assert.ok(Math.abs(actual.bottom-expected.bottom)<.022,context);
   }
   console.log('PASS preview versus FFmpeg',JSON.stringify({width,live,render}));
  }
  const source=page.locator('#preview-caption-source');
  assert.match(await source.getAttribute('style'),/scaleX\(1\)/,'short text keeps its glyph proportions');
  await seek(3500);
  await page.waitForFunction(()=>document.getElementById('preview-caption-source').textContent.startsWith('Uma legenda longa'));
  assert.match(await source.getAttribute('style'),/scaleX\(0\./,'long text fits horizontally');
  assert.deepEqual(errors,[]);
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
