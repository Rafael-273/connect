const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE || 'playwright-core');
const http=require('http'),fs=require('fs'),assert=require('assert/strict');
const root=require('path').resolve(__dirname, '../..');
(async()=>{
 const server=http.createServer((req,res)=>{const u=new URL(req.url,'http://localhost');
 if(u.pathname==='/sw.js'){res.setHeader('Content-Type','application/javascript');res.end(fs.readFileSync(root+'/static/sw.js'));}
 else if(u.pathname.startsWith('/static/')){res.setHeader('Content-Type',u.pathname.endsWith('.css')?'text/css':'application/javascript');res.end(fs.readFileSync(root+u.pathname));}
 else {res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Connect</title><script src="/static/pwa.js?v=1" defer></script><main>Private content</main>');}
 });await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH || undefined,args:['--no-sandbox']});
 try{const context=await browser.newContext();const page=await context.newPage();await page.goto('http://127.0.0.1:'+server.address().port+'/login/');
 await page.evaluate(()=>navigator.serviceWorker.ready);await page.waitForFunction(()=>navigator.serviceWorker.controller);
 assert.equal(await page.evaluate(()=>new URL(navigator.serviceWorker.controller.scriptURL).pathname),'/sw.js');
 await page.goto('http://127.0.0.1:'+server.address().port+'/dashboard/');
 const keys=await page.evaluate(async()=>{const c=await caches.open('filadelfia-connect-v4');return (await c.keys()).map(r=>new URL(r.url).pathname)});
 assert.ok(keys.includes('/static/pwa.css'));assert.ok(!keys.some(k=>!k.startsWith('/static/')));
 await context.setOffline(true);await page.reload();assert.match(await page.content(),/Você está sem conexão/);
 console.log('PASS root worker install, app control, static-only cache and offline navigation');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exit(1)});
