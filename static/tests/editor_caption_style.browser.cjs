// The editable preview keeps the template font height and fits long text in the output frame.
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||undefined});
 try{
  const address=new URL(process.env.FILADELFIA_QA_URL);
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await context.addCookies([{name:'sessionid',value:process.env.FILADELFIA_QA_SESSION,domain:address.hostname,path:'/'}]);
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route(address.href,async route=>{
   const response=await route.fetch(),html=await response.text();
   await route.fulfill({response,body:html.replace(/(<script id="preview-timeline" type="application\/json">)([\s\S]*?)(<\/script>)/,(_,a,raw,b)=>{
    const t=JSON.parse(raw);t.sequence.width=3840;t.sequence.height=2160;
    t.caption_styles.source={font:'Montserrat',font_size:72,font_weight:700,alignment:2,margin_bottom:140,color:'#ffcc00',max_lines:1,max_characters:42,outline_width:3};
    t.captions[0].text='Esta legenda longa deve manter o tamanho configurado no template mesmo quando ultrapassa a largura disponível';
    return a+JSON.stringify(t)+b;
   })});
  });
  await page.goto(address.href);await page.waitForSelector('.editor-track--caption');
  await page.locator('#preview-range').evaluate(n=>{n.value=1500;n.dispatchEvent(new Event('input'))});
  await page.waitForFunction(()=>!document.getElementById('preview-caption-source').hidden);
  for(const [width,height] of [[390,844],[320,568],[1440,900]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(200);
   const result=await page.evaluate(()=>{
    const stage=document.querySelector('.preview-stage'),node=document.getElementById('preview-caption-source'),css=getComputedStyle(stage),style=getComputedStyle(node);
    return {font:parseFloat(style.fontSize),expectedFont:72*stage.clientWidth/Number(css.getPropertyValue('--preview-width')),bottom:parseFloat(style.bottom),expectedBottom:140*stage.clientHeight/Number(css.getPropertyValue('--preview-height')),weight:style.fontWeight,color:style.color,whiteSpace:style.whiteSpace,width:node.getBoundingClientRect().width,stageWidth:stage.clientWidth,stageLeft:stage.getBoundingClientRect().left,stageRight:stage.getBoundingClientRect().right,left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right,transform:node.style.transform};
   });
   assert.ok(Math.abs(result.font-result.expectedFont)<.05,JSON.stringify(result));
   assert.ok(Math.abs(result.bottom-result.expectedBottom)<.05,JSON.stringify(result));
   assert.equal(result.weight,'700');assert.equal(result.color,'rgb(255, 204, 0)');assert.equal(result.whiteSpace,'pre');assert.ok(result.width<=result.stageWidth,JSON.stringify(result));assert.ok(result.left>=result.stageLeft&&result.right<=result.stageRight,JSON.stringify(result));assert.match(result.transform,/scaleX\(0\./);
  }
  await page.locator('#preview-range').evaluate(n=>{n.value=3500;n.dispatchEvent(new Event('input'))});
  await page.waitForFunction(()=>document.getElementById('preview-caption-source').textContent==='Dois');
  assert.match(await page.locator('#preview-caption-source').getAttribute('style'),/scaleX\(1\)/,'short captions keep their original width');
  assert.deepEqual(errors,[]);console.log('PASS template font height, position, and long captions fitted within mobile and desktop output');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
