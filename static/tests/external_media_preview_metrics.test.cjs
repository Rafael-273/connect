const {test}=require('node:test');
const assert=require('node:assert/strict');
const Metrics=require('../external_media_preview_metrics.js');
function harness(){
 let time=0,id=0;const frames=new Map();
 const video={dataset:{source:'a'},currentTime:0,readyState:2,seeking:false,paused:true,hidden:false,tagName:'VIDEO',getAttribute:()=>'/a'};
 const player={video,frameTolerance:1/30,pendingFrameCheck:null,waitingForPreload:null};
 const metrics=new Metrics({limit:5,now:()=>time,getMedia:()=>[video,video],requestFrame:callback=>{frames.set(++id,callback);return id},cancelFrame:key=>frames.delete(key)});
 return {metrics,video,player,advance:ms=>time+=ms,flush(){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(callback=>callback())}};
}
test('seek metrics distinguish superseded requests, presented frames, and readiness-only samples',()=>{
 const h=harness();h.metrics.beginSeek('a',1,h.player);h.advance(10);h.metrics.beginSeek('a',2,h.player);
 assert.equal(h.metrics.samples[0].outcome,'cancelled');h.video.currentTime=2;h.advance(20);h.metrics.frame(h.video,2);
 const presented=h.metrics.samples.find(sample=>sample.outcome==='presented');assert.equal(presented.elapsedMs,20);assert.equal(presented.signal,'video-frame');
 h.metrics.beginSeek('a',2,h.player);h.flush();h.flush();assert.equal(h.metrics.samples.at(-1).signal,'ready-after-raf');
});
test('resource diagnostics are deduplicated and bounded; failures never count as successful seeks',()=>{
 const h=harness();for(let i=0;i<20;i++)h.metrics.record('transition',{phase:'warm'});
 assert.equal(h.metrics.samples.length,5);const snapshot=h.metrics.export();assert.equal(snapshot.samples.at(-1).elements,1);
 h.metrics.beginSeek('a',2,h.player);h.player.failure={reason:'timeout'};h.flush();assert.equal(h.metrics.samples.at(-1).outcome,'error');
 assert.equal(snapshot.samples.length,5);assert.equal(snapshot.samples.at(-1).type,'resources');
});
