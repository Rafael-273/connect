const {test}=require('node:test'),assert=require('node:assert/strict');
const {waitFor}=require('../external_media_cached_media.js');
test('cached media polls lightweight pending responses until persisted data is ready',async()=>{
    let calls=0;const sleeps=[];
    const result=await waitFor('/waveform',{fetcher:async()=>({ok:true,status:++calls<3?202:200,json:async()=>calls<3?{status:'PENDING',retry_after:5}:{peaks:[.5],durationMs:1000}}),sleep:async ms=>sleeps.push(ms)});
    assert.deepEqual(result,{peaks:[.5],durationMs:1000});assert.deepEqual(sleeps,[5000,5000]);
});
test('failed preparation and exhausted retries stop without an infinite request loop',async()=>{
    await assert.rejects(waitFor('/noise',{fetcher:async()=>({ok:true,status:202,json:async()=>({status:'ERROR'})})}));
    let calls=0;await assert.rejects(waitFor('/noise',{attempts:2,sleep:async()=>{},fetcher:async()=>{calls++;return {ok:true,status:202,json:async()=>({status:'PENDING'})}}}));
    assert.equal(calls,2);
});
test('cancelled comparisons stop polling before making another request',async()=>{
    const controller=new AbortController();controller.abort();let calls=0;
    await assert.rejects(waitFor('/noise',{signal:controller.signal,fetcher:async()=>{calls++}}),{name:'AbortError'});
    assert.equal(calls,0);
});
test('closing a comparison panel stops later status requests',async()=>{
    let connected=true,calls=0;
    await assert.rejects(waitFor('/noise',{isCurrent:()=>connected,sleep:async()=>{connected=false},fetcher:async()=>{calls++;return {ok:true,status:202,json:async()=>({status:'PENDING'})}}}),{name:'AbortError'});
    assert.equal(calls,1);
});
