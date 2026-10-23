const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const template=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const clockCode=template.slice(template.indexOf('    let playbackClock='),template.indexOf('    const sourceEventBound='));
function setup({rate=1,nextSource='b',nextIn=0}={}){
 const timers=new Map(),seeks=[],renders=[];let id=0;
 const first={asset_id:'a',source_in_ms:0,source_out_ms:1000,timeline_in_ms:0,timeline_out_ms:1000};
 const second={asset_id:nextSource,source_in_ms:nextIn,source_out_ms:nextIn+1000,timeline_in_ms:1000,timeline_out_ms:2000};
 const video={paused:false,seeking:false,currentTime:.966,playbackRate:rate,pause(){this.paused=true}};
 const context=vm.createContext({video,activeClip:first,first,second,globalMs:0,loading:false,draggingTimelineNeedle:false,
  timeline:{sequence:{duration_ms:2000}},performance:{now:()=>100},frameDurationMs:()=>1000/30,
  useReviewMaster:()=>false,nextClipAfter:clip=>clip===first?second:null,sourceFor:clip=>clip.asset_id,
  seek:(...args)=>seeks.push(args),renderPosition:()=>renders.push(1),warmNextClip:()=>{},
  setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id},clearTimeout:id=>timers.delete(id)});
 vm.runInContext(clockCode,context);
 return {context,video,timers,seeks,renders,run:code=>vm.runInContext(code,context)};
}
test('the final outgoing frame remains visible until the exact cut deadline, including playback speed',()=>{
 for(const rate of [.5,1,2]){
  const h=setup({rate});h.run('syncPlaybackPosition(video,.9666666667);schedulePlaybackBoundary(video,.9666666667,105)');
  assert.equal(h.seeks.length,0,'the old 40ms threshold must not advance this frame');
  const timer=[...h.timers.values()][0];assert.ok(Math.abs(timer.delay-(5+1000/30/rate))<.001);
  timer.fn();assert.deepEqual(h.seeks,[[1000,true]]);
 }
});
test('manual seek cancellation and a real waiting event cannot leave a pending boundary runnable',()=>{
 const h=setup();h.run('schedulePlaybackBoundary(video,.98,100);cancelPlaybackBoundary()');assert.equal(h.timers.size,0);
 h.run('schedulePlaybackBoundary(video,.98,100)');const timer=[...h.timers.values()][0];h.video.seeking=true;timer.fn();assert.equal(h.seeks.length,0);
});
test('timeline replacement invalidates a scheduled cut and continuous source spans never schedule a handoff',()=>{
 const h=setup();h.run('schedulePlaybackBoundary(video,.98,100)');const timer=[...h.timers.values()][0];h.run('activeClip={...first}');timer.fn();assert.equal(h.seeks.length,0);
 const contiguous=setup({nextSource:'a',nextIn:1000});contiguous.run('schedulePlaybackBoundary(video,.98,100);syncPlaybackPosition(video,1)');
 assert.equal(contiguous.timers.size,0);assert.equal(contiguous.seeks.length,0);assert.equal(contiguous.run('activeClip===second'),true);
});
test('preparation skips continuous blade spans to warm the next actual discontinuity',()=>{
 const start=template.indexOf('    function warmNextClip('),end=template.indexOf('    function seek(',start);
 const warmed=[],clips=[{source:'a',source_in_ms:0,source_out_ms:1},{source:'a',source_in_ms:1,source_out_ms:2},{source:'a',source_in_ms:8,source_out_ms:9}];
 vm.runInNewContext(template.slice(start,end)+';warmNextClip(clips[0]);',{clips,useReviewMaster:()=>false,nextClipAfter:clip=>clips[clips.indexOf(clip)+1],sourceFor:clip=>clip.source,sourcePlayer:{warm:(...args)=>warmed.push(args)}});
 assert.deepEqual(warmed,[['a',.008]]);
});


test('save reconciliation at the current frame avoids a seek while manual seeks remain explicit',()=>{
 const start=template.indexOf('    function seek('),end=template.indexOf('    function renderPosition(',start),calls=[];
 const clip={source_in_ms:1000,timeline_in_ms:0},video={paused:false};let prepared=true;
 const context=vm.createContext({video,globalMs:500,activeClip:clip,previewTransition:null,timeline:{sequence:{duration_ms:1000}},cancelPlaybackBoundary:()=>{},clipAt:()=>clip,useReviewMaster:()=>false,sourceFor:()=>'/a.mp4',sourcePlayer:{isAt:()=>prepared,seek:(...args)=>calls.push(args)},warmNextClip:()=>{},renderPosition:()=>{},syncMusicPosition:()=>{}});
 vm.runInContext(template.slice(start,end),context);
 vm.runInContext('seek(500,true,{preserveContinuous:true})',context);assert.equal(calls.length,0);
 vm.runInContext('seek(500,true)',context);assert.equal(calls.length,1);
 prepared=false;vm.runInContext('seek(500,true,{preserveContinuous:true})',context);assert.equal(calls.length,2);
});
