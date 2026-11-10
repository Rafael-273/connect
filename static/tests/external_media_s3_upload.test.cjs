const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const MiB=1024**2;
const plan={upload_mode:'multipart',part_size:64*MiB,multipart_url:'/multipart',complete_url:'/complete',allocation:{project_id:'project',size:8*1024**3},allocation_sig:'signed-allocation'};
const file=(size=8*1024**3)=>({size,slice(start,end){return {start,end,size:end-start}}});
function setup({outcome=()=>200,fetchFailure=()=>false,redirect=false}={}){
 const sent=[],posts=[],window={},progress=[];let active=0,maxActive=0;
 class XHR extends EventTarget {
  constructor(){super();this.upload=new EventTarget();this.status=0;this.responseText='';this.finished=false}
  open(method,url){this.method=method;this.url=url}
  send(blob){this.blob=blob;sent.push(this);active++;maxActive=Math.max(maxActive,active);queueMicrotask(()=>{
   if(this.finished)return;this.finished=true;active--;
   const event=new Event('progress');Object.assign(event,{lengthComputable:true,loaded:blob.size,total:blob.size});this.upload.dispatchEvent(event);
   const result=outcome(this,sent);if(result==='network')return this.dispatchEvent(new Event('error'));
   this.status=result;this.dispatchEvent(new Event('load'));
  })}
  abort(){if(!this.finished){this.finished=true;active--;this.dispatchEvent(new Event('abort'))}}
 }
 let signs=0;
 const fetch=async(url,options)=>{
  const body=JSON.parse(options.body);posts.push({url,body,options});
  if(fetchFailure(url,body,posts))throw new TypeError('connection reset');
  return {redirected:redirect,ok:true,status:200,json:async()=>body.action==='part'?{upload_url:`https://s3.test/part/${body.part_number}?signature=${++signs}`}:{id:123}};
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_s3_upload.js'),'utf8'),{window,XMLHttpRequest:XHR,fetch,AbortController,setTimeout:callback=>queueMicrotask(callback)});
 return {api:window.ConnectS3Upload,sent,posts,progress,options:{csrfToken:'csrf',onProgress:n=>progress.push(n)},get maxActive(){return maxActive}};
}
test('8 GiB is sent in 128 bounded slices with at most three active PUTs',async()=>{
 const h=setup();await h.api.upload(file(),plan,h.options);
 assert.equal(h.sent.length,128);assert.ok(h.maxActive<=3);
 assert.equal(h.sent.reduce((sum,xhr)=>sum+xhr.blob.size,0),8*1024**3);
 assert.ok(h.sent.every(xhr=>xhr.blob.size<=64*MiB&&xhr.method==='PUT'));
 assert.equal(h.sent.at(-1).blob.end,8*1024**3);assert.equal(h.progress.at(-1),100);
 assert.deepEqual(h.posts.map(p=>p.body.part_number).sort((a,b)=>a-b),Array.from({length:128},(_,i)=>i+1));
 assert.ok(h.posts.every(p=>p.body.allocation_sig==='signed-allocation'&&p.options.headers['X-CSRFToken']==='csrf'));
 assert.equal(h.posts.filter(p=>p.url==='/complete').length,0);
});
test('a transient part error signs a new URL and resends only that part',async()=>{
 let failed=false;const h=setup({outcome:xhr=>{if(xhr.url.includes('/part/2?')&&!failed){failed=true;return 'network'}return 200}});
 await h.api.upload(file(140*MiB),plan,h.options);
 const two=h.sent.filter(xhr=>xhr.url.includes('/part/2?'));assert.equal(two.length,2);assert.notEqual(two[0].url,two[1].url);
 assert.equal(h.sent.filter(xhr=>xhr.url.includes('/part/1?')).length,1);
 assert.equal(h.progress.at(-1),100);
});
test('expired S3 credentials obtain a fresh part URL before retry',async()=>{
 let first=true;const h=setup({outcome:()=>{if(first){first=false;return 403}return 200}});
 await h.api.upload(file(64*MiB),plan,h.options);assert.equal(h.sent.length,2);assert.notEqual(h.sent[0].url,h.sent[1].url);
});
test('a permanent S3 rejection stops workers and aborts the remote multipart session',async()=>{
 const h=setup({outcome:()=>400});await assert.rejects(h.api.upload(file(),plan,h.options),/HTTP 400/);
 assert.equal(h.posts.filter(p=>p.body.action==='abort').length,1);
 assert.ok(h.posts.filter(p=>p.body.action==='part').length<128);
 assert.equal(h.posts.filter(p=>p.url==='/complete').length,0);
});
test('network retry exhaustion aborts the session and preserves the original failure',async()=>{
 const h=setup({outcome:()=>'network',fetchFailure:(_,body)=>body.action==='abort'});
 await assert.rejects(h.api.upload(file(64*MiB),plan,h.options),/conexão com o S3/);
 assert.equal(h.sent.length,3);assert.equal(h.posts.filter(p=>p.body.action==='abort').length,1);
});
test('a lost registration response retries completion without retransmitting any data',async()=>{
 let fail=true;const h=setup({fetchFailure:url=>{if(url==='/complete'&&fail){fail=false;return true}return false}});
 const result=await h.api.complete(plan,'csrf');assert.equal(result.id,123);
 assert.equal(h.posts.length,2);assert.equal(h.sent.length,0);
 assert.ok(h.posts.every(p=>p.body.allocation_sig==='signed-allocation'));
});
test('small files retain single PUT and legacy registration is not retried',async()=>{
 const h=setup({fetchFailure:url=>url==='/complete'}),single={upload_mode:'single',upload_url:'https://s3.test/small',complete_url:'/complete'};
 await h.api.upload(file(1*MiB),single,h.options);assert.equal(h.sent.length,1);assert.equal(h.posts.length,0);
 await assert.rejects(h.api.complete(single,'csrf'));assert.equal(h.posts.length,1);
});
test('large files cannot fall back to an oversized single PUT when backend metadata is stale',async()=>{
 const h=setup();await assert.rejects(h.api.upload(file(),{upload_url:'https://s3.test/single'},h.options),/envio em partes/);assert.equal(h.sent.length,0);
});
test('invalid multipart sizing is rejected before issuing requests',async()=>{
 const h=setup();await assert.rejects(h.api.upload(file(),{...plan,part_size:0},h.options),/inválida/);assert.equal(h.sent.length,0);assert.equal(h.posts.length,0);
});


test('an expired login redirect cannot send video bytes back to a relative Django URL',async()=>{
 const h=setup({redirect:true});await assert.rejects(h.api.upload(file(),plan,h.options),/sessão/);
 assert.equal(h.sent.length,0);
 await assert.rejects(h.api.complete(plan,'csrf'),/sessão/);
});
