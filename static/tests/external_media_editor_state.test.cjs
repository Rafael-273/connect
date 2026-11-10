const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const EditorPersistence = require('../external_media_editor_state.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {resolve=yes; reject=no}); return {promise,resolve,reject}; };

test('clicking source audio opens and keeps the audio inspector selected',()=>{
    const editor=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    assert.match(editor,/if\(openInspector&&selectKind\)selectInspector\(selectKind/);
    assert.match(editor,/selectSourceSegment\(Boolean\(selectKind\)\)/);
    assert.match(editor,/if\(selectKind\)selectInspector\(selectKind/);
    assert.match(editor,/if\(!\['video','audio'\]\.includes\(kind\)\)selectedSegment=null/);
    assert.match(editor,/inspectorSelection\?\.kind==='audio'/);
});

test('operation IDs work on HTTP LAN hosts without crypto.randomUUID',()=>{
    const context=vm.createContext({crypto:{getRandomValues:values=>{values.fill(7);return values}}});
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../external_media_editor_state.js'),'utf8'),context);
    const id=vm.runInContext('ConnectEditorPersistence.operationId()',context);
    assert.match(id,/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
});

test('pointer updates use the latest sample per frame and flush the final position on release',()=>{
    const frames=new Map(),seen=[];let id=0;
    const move=EditorPersistence.frameLatest(value=>seen.push(value),callback=>{frames.set(++id,callback);return id},handle=>frames.delete(handle));
    move(10);move(20);move(30);assert.equal(frames.size,1);assert.deepEqual(seen,[]);
    frames.values().next().value();assert.deepEqual(seen,[30]);assert.equal(frames.size,0);
    move(40);move(50);move.flush();assert.deepEqual(seen,[30,50]);assert.equal(frames.size,0);
});

test('two rapid caption edits persist serially, using the acknowledged version', async () => {
    const calls=[], gates=[deferred(),deferred()];
    const editor=new EditorPersistence({version:3,send:(op,base)=>{calls.push({op,base});return gates[calls.length-1].promise}});
    const a=editor.enqueue('/captions/1',{end_ms:1400});
    const b=editor.enqueue('/captions/2',{end_ms:2800});
    assert.equal(calls.length,1);
    gates[0].resolve({editorial_version:4});await a;await tick();
    assert.equal(calls.length,2);assert.equal(calls[1].base,4);
    gates[1].resolve({editorial_version:5});await b;await editor.flush();
    assert.equal(editor.busy,false);
});

test('repeated changes on one element are debounced outside DOM cards', async () => {
    const calls=[];const editor=new EditorPersistence({send:async op=>{calls.push(op.body);return {editorial_version:calls.length}}});
    editor.schedule('caption:1',()=>editor.enqueue('/captions/1',{end_ms:1500}),2000);
    editor.schedule('caption:1',()=>editor.enqueue('/captions/1',{end_ms:1300}),2000);
    await editor.flush();assert.deepEqual(calls,[{end_ms:1300}]);
});

test('an edit made while the first save is pending is preserved', async () => {
    const first=deferred(),calls=[];
    const editor=new EditorPersistence({send:op=>{calls.push(op.body);return calls.length===1?first.promise:Promise.resolve({editorial_version:2})}});
    const a=editor.enqueue('/captions/1',{end_ms:1500});
    editor.schedule('caption:1',()=>editor.enqueue('/captions/1',{end_ms:1100}),2000);
    const flushed=editor.flush();first.resolve({editorial_version:1});await a;await flushed;
    assert.deepEqual(calls,[{end_ms:1500},{end_ms:1100}]);
});

test('repeated queued gestures merge only unsent adjacent patches', async()=>{
    const first=deferred(),calls=[];
    const editor=new EditorPersistence({send:op=>{calls.push(JSON.parse(JSON.stringify(op.body)));return calls.length===1?first.promise:Promise.resolve({editorial_version:calls.length})}});
    const a=editor.enqueue('/brolls/1',{transform:{x:.4}},'broll:1');
    const b=editor.enqueue('/brolls/1',{transform:{x:.6}},'broll:1');
    const c=editor.enqueue('/brolls/1',{transform:{y:.7}},'broll:1');
    const d=editor.enqueue('/brolls/1',{transform:{x:.8}},'broll:1');
    first.resolve({editorial_version:1});await Promise.all([a,b,c,d]);await editor.flush();
    assert.deepEqual(calls,[{transform:{x:.4}},{transform:{x:.8,y:.7}}]);
});

test('queued patches are not coalesced across a history command', async()=>{
    const gate=deferred(),calls=[];
    const editor=new EditorPersistence({send:op=>{calls.push(op.url);return calls.length===1?gate.promise:Promise.resolve({editorial_version:calls.length})}});
    const ops=[editor.enqueue('/caption/1',{},'caption:1'),editor.enqueue('/caption/1',{},'caption:1'),editor.enqueue('/undo',{}),editor.enqueue('/caption/1',{},'caption:1')];
    gate.resolve({editorial_version:1});await Promise.all(ops);
    assert.deepEqual(calls,['/caption/1','/caption/1','/undo','/caption/1']);
});

test('network failure retains the operation, payload, id and base on retry', async () => {
    const calls=[];let fail=true;
    const editor=new EditorPersistence({version:7,send:async(op,base)=>{calls.push({id:op.id,body:op.body,base});if(fail)throw new Error('offline');return {editorial_version:8}}});
    const pending=editor.enqueue('/overlays/1',{position:{x:.8}});await tick();
    assert.equal(editor.queue.length,1);assert.ok(editor.error);assert.equal(editor.busy,true);
    fail=false;editor.retry();await pending;await editor.flush();
    assert.deepEqual(calls[0],calls[1]);assert.equal(editor.queue.length,0);
});

test('a lost response after commit is retried once without duplicating persistence', async () => {
    const receipts=new Map();let writes=0,lose=true;
    const editor=new EditorPersistence({send:async op=>{
        if(!receipts.has(op.id)){writes++;receipts.set(op.id,{editorial_version:writes})}
        if(lose){lose=false;throw new Error('response lost')}
        return receipts.get(op.id);
    }});
    const pending=editor.enqueue('/overlays',{content:{text:'Novo'}});await tick();editor.retry();await pending;
    assert.equal(writes,1);
});

test('a conflict pauses later writes and cannot be retried blindly', async () => {
    let calls=0;const editor=new EditorPersistence({send:async()=>{calls++;throw Object.assign(new Error('conflict'),{status:409})}});
    editor.enqueue('/overlays/1',{start_ms:100});editor.enqueue('/overlays/2',{start_ms:200});await tick();
    editor.retry();await tick();assert.equal(calls,1);assert.equal(editor.queue.length,2);
});

test('an active gesture prevents idle reconciliation even after HTTP completes', async () => {
    let reconciliations=0;const editor=new EditorPersistence({send:async()=>({editorial_version:1}),onIdle:()=>reconciliations++});
    editor.beginGesture();await editor.enqueue('/overlays/1',{});await tick();assert.equal(reconciliations,0);
    editor.endGesture();await tick();assert.equal(reconciliations,1);
});

test('approval barrier flushes property debounces and waits for persistence', async () => {
    const calls=[],gate=deferred();const editor=new EditorPersistence({send:async op=>{calls.push(op.url);if(op.url==='/save')await gate.promise;return {editorial_version:1}}});
    editor.schedule('transform',()=>editor.enqueue('/save',{x:.8}),2000);
    const approve=(async()=>{await editor.flush();await editor.enqueue('/approve',{})})();
    await tick();assert.deepEqual(calls,['/save']);gate.resolve();await approve;assert.deepEqual(calls,['/save','/approve']);
});

const template=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
function timelineHarness(){
    const apply=template.match(/    function applyTimeline\(data[^\n]+/)[0];
    const context=vm.createContext({});
    vm.runInContext(`let timeline={sequence:{duration_ms:10000},captions:[],music:null},appliedEditorialVersion=5,deferredTimeline=null,activePreviewRevision=5,selectedSegment=null,inspectorSelection=null,bladePoints=[],activeClip=null,globalMs=0,multicamEditor=null;const editorPersistence={busy:false};const video={paused:true};const sourcePlayer={target:null,waitingForPreload:null};let loading=false,seekCalls=[];const videoSegments=()=>[],clipAt=()=>null,configureMusic=()=>{},renderReviewProxyStatus=()=>{},renderList=()=>{},seek=(...args)=>seekCalls.push(args),renderPosition=()=>{},scheduleRevisionProxyPoll=()=>{},updateEditControls=()=>{},updateHistoryControls=()=>{},requestTimelineRender=()=>{},invalidatePreviewIndexes=()=>{};const document={getElementById:()=>({textContent:''})};${apply}`,context);
    return context;
}

test('actual applyTimeline accepts undo to an older revision with a newer state version',()=>{
    const context=timelineHarness();
    vm.runInContext(`result=applyTimeline({revision:4,editorial_version:6,timeline:{sequence:{duration_ms:10000},captions:[]}},false,false);observed=activePreviewRevision`,context);
    assert.equal(context.result,true);assert.equal(context.observed,4);
});

test('actual applyTimeline ignores out-of-order responses using state version',()=>{
    const context=timelineHarness();
    vm.runInContext(`result=applyTimeline({revision:6,editorial_version:4,timeline:{}},false,false)`,context);
    assert.equal(context.result,false);
});

test('actual applyTimeline preserves pending local edits and keeps only the newest deferred snapshot',()=>{
    const context=timelineHarness();
    vm.runInContext(`editorPersistence.busy=true;timeline.captions=[{id:1,end_ms:1500}];applyTimeline({revision:6,editorial_version:6,timeline:{captions:[{id:1,end_ms:2000}]}},false,false);applyTimeline({revision:5,editorial_version:5,timeline:{}},false,false);observed=timeline.captions[0].end_ms;pendingVersion=deferredTimeline.editorial_version`,context);
    assert.equal(context.observed,1500);assert.equal(context.pendingVersion,6);
});

test('minute conversion preserves 1000ms rather than rounding to 1020ms',()=>{
    const expression=template.match(/const minFromMs = ms => (.*);/)[1];
    const convert=vm.runInNewContext(`ms=>${expression}`);
    for(const ms of [33,1000,2000,1234567])assert.equal(Math.round(convert(ms)*60000),ms);
});


test('an editorial response preserves active playback and paused user intent',()=>{
    const context=timelineHarness();
    vm.runInContext(`video.paused=false;applyTimeline({editorial_version:6,timeline:{sequence:{duration_ms:10000},captions:[]}});observed=seekCalls.at(-1)[1]`,context);
    assert.equal(context.observed,true);
    vm.runInContext(`video.paused=true;applyTimeline({editorial_version:7,timeline:{sequence:{duration_ms:10000},captions:[]}});observed=seekCalls.at(-1)[1]`,context);
    assert.equal(context.observed,false);
});

test('an editorial response retains play intent while a transition is waiting for media',()=>{
    const context=timelineHarness();
    vm.runInContext(`sourcePlayer.target={autoplay:true};sourcePlayer.waitingForPreload='b:0';loading=true;applyTimeline({editorial_version:6,timeline:{sequence:{duration_ms:10000},captions:[]}});observed=seekCalls.at(-1)[1]`,context);
    assert.equal(context.observed,true);
});
