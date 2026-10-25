const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const source=html.slice(html.indexOf('    const musicWaveformCache='),html.indexOf('    let timelinePlayheadFrame='));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(fetch){
    let decodes=0;
    class AudioContext{async decodeAudioData(){decodes++;return {duration:4,getChannelData:()=>new Float32Array([.1,.3,.8,.2])}}}
    const context=vm.createContext({URL,Float32Array,fetch,location:{href:'https://editor.test/preview/'},
        window:{AudioContext,ConnectEditorView:{waveformWindow:()=>({offset:0,width:100,from:0,to:1})}},
        performance:{now:()=>0},frameLatest:()=>()=>{},document:{getElementById:()=>({getBoundingClientRect:()=>({})})},setTimeout,
    });
    vm.runInContext(source,context);return {context,get decodes(){return decodes}};
}
test('waveform requests stay on the protected endpoint and share one decode',async()=>{
    const requests=[];const h=harness(async(url,options)=>{requests.push([url,options.credentials]);return {ok:true,arrayBuffer:async()=>new ArrayBuffer(0)}});
    const [a,b]=await Promise.all([h.context.loadMusicWaveform('/preview/music/?track_id=7'),h.context.loadMusicWaveform('/preview/music/?track_id=7')]);
    assert.equal(a,b);assert.equal(h.decodes,1);
    assert.deepEqual(requests,[['https://editor.test/preview/music/?track_id=7&waveform=1','same-origin']]);
});
test('a failed waveform download can be retried instead of poisoning the cache',async()=>{
    let attempts=0;const h=harness(async()=>{if(++attempts===1)throw new Error('network');return {ok:true,arrayBuffer:async()=>new ArrayBuffer(0)}});
    await assert.rejects(h.context.loadMusicWaveform('/music?track_id=1'));
    const result=await h.context.loadMusicWaveform('/music?track_id=1');
    assert.ok(result.peaks.length);assert.equal(attempts,2);
});
test('split music waveforms use source offsets and repeat across original-file loop boundaries',()=>{
    const {context}=harness();const audio={durationMs:4000,peaks:[.1,.2,.8,.9]};
    const range=(start,end,buckets)=>Array.from(context.waveformRange(audio,start,end,buckets,true));
    assert.deepEqual(range(0,2000,2),[.1,.2]);
    assert.deepEqual(range(2000,4000,2),[.8,.9]);
    assert.deepEqual(range(3000,6000,3),[.9,.1,.2]);
});
test('failure from a replaced music download cannot erase the new clip waveform',async()=>{
    const {context}=harness();let reject;
    context.loadMusicWaveform=()=>new Promise((resolve,no)=>{reject=no});
    const classes=new Set(['has-real-waveform']),canvas={};
    const clip={isConnected:true,querySelector:()=>canvas,getBoundingClientRect:()=>({height:30}),classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)}};
    context.clip=clip;context.observeWaveform(clip,'old');vm.runInContext('visibleWaveforms.add(clip);paintVisibleWaveforms()',context);
    context.observeWaveform(clip,'new');const current=clip._waveform;
    reject(new Error('old download'));await tick();
    assert.equal(clip._waveform,current);assert.equal(current.failed,false);assert.equal(classes.has('has-real-waveform'),true);
});
