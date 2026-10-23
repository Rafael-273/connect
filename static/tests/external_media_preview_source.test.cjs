const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Video extends EventTarget {
    constructor(){super();this.dataset={};this.style={};this.classList={add(){}};this.parentNode={append(){}};this.readyState=0;this.paused=true;this.muted=false;this.volume=1;this.playbackRate=1;this.duration=10;this.seeking=false;this.error=null;this._time=0;this.loads=0;this.buffered={length:1,start:()=>0,end:()=>10}}
    set currentTime(value){this._time=value;this.seeking=true}
    get currentTime(){return this._time}
    play(){this.paused=false;return Promise.resolve()}
    pause(){this.paused=true}
    load(){this.loads++}
    metadata(){this.readyState=1;this.dispatchEvent(new Event('loadedmetadata'))}
    frame(){this.readyState=2;this.seeking=false;this.dispatchEvent(new Event('seeked'));this.dispatchEvent(new Event('loadeddata'))}
}
function harness(){
    const frames=[],loading=[];let plays=0,releases=0;
    const window={},video=new Video();
    const context={window,document:{createElement:()=>new Video()},HTMLMediaElement:{HAVE_METADATA:1,HAVE_CURRENT_DATA:2},AbortController,requestAnimationFrame:callback=>frames.push(callback)};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_preview_source.js'),'utf8'),context);
    const player=new window.ConnectPreviewSource(video,{onLoading:value=>loading.push(value),releaseFrame:()=>releases++,play:()=>{plays++;player.video.play()}});
    return {player,video,loading,flush(){frames.splice(0).forEach(callback=>callback())},get plays(){return plays},get releases(){return releases}};
}
test('metadata arriving after repeated same-source scrubs uses the latest time and playback intent',()=>{
    const h=harness();h.player.seek('a.mp4',1,{autoplay:true});h.player.seek('a.mp4',4,{autoplay:false});
    h.video.metadata();assert.equal(h.video.currentTime,4);h.video.frame();h.flush();
    assert.equal(h.plays,0);assert.equal(h.loading.at(-1),false);assert.equal(h.releases,1);
});
test('a pending earlier seek cannot complete or play over a newer same-source seek',()=>{
    const h=harness();h.player.seek('a.mp4',1,{autoplay:true});h.video.metadata();h.video.frame();
    h.player.seek('a.mp4',5,{autoplay:false});h.flush();assert.equal(h.plays,0);
    h.video.frame();h.flush();assert.equal(h.video.currentTime,5);assert.equal(h.loading.at(-1),false);assert.equal(h.plays,0);
});
test('preloader completion after scrubbing back cannot resume the old target',()=>{
    const h=harness();h.video.dataset.source='b.mp4';h.video.readyState=2;
    h.player.warm('a.mp4',2);const preload=h.player.preloader;h.player.seek('a.mp4',2,{autoplay:true});
    h.player.seek('b.mp4',4,{autoplay:false});preload.dispatchEvent(new Event('canplay'));preload.frame();h.flush();
    assert.equal(h.player.video,h.video);assert.equal(h.video.dataset.source,'b.mp4');assert.equal(h.video.currentTime,4);assert.equal(h.plays,0);
});
test('interrupting a buffer swap keeps distinct decoders and cannot reveal the previous target later',()=>{
    const h=harness(),outgoing=h.video,incoming=h.player.preloader;
    outgoing.dataset.source='b.mp4';incoming.dataset.source='a.mp4';incoming.readyState=3;incoming._time=1;h.player.preloadedSource='a.mp4';
    h.player.seek('a.mp4',1,{autoplay:true});assert.equal(h.player.video,incoming);
    h.player.seek('a.mp4',3,{autoplay:false});incoming.dispatchEvent(new Event('playing'));h.flush();
    assert.equal(h.player.switching,false);assert.equal(h.player.preloader,outgoing);assert.equal(h.player.video,incoming);assert.equal(incoming.currentTime,3);assert.equal(incoming.paused,true);
    h.player.warm('c.mp4');assert.equal(outgoing.src,'c.mp4');assert.equal(incoming.dataset.source,'a.mp4');
});
test('completion of a replaced source cannot release or play the new request',()=>{
    const h=harness();h.player.seek('a.mp4',1,{autoplay:true});h.video.metadata();h.video.frame();
    h.player.seek('b.mp4',2,{autoplay:false});h.flush();assert.equal(h.plays,0);assert.equal(h.loading.at(-1),true);
    h.video.metadata();h.video.frame();h.flush();assert.equal(h.plays,0);assert.equal(h.video.currentTime,2);assert.equal(h.loading.at(-1),false);
});
test('missing proxies do not load the current document as a video source',()=>{
    const h=harness();h.player.seek('',0);assert.equal(h.video.src,undefined);assert.equal(h.loading.at(-1),false);
});
test('a missing proxy cancels pending metadata from the previous source',()=>{
    const h=harness();h.player.seek('a.mp4',3,{autoplay:true});h.player.seek('',0);
    h.video.metadata();h.video.frame();h.flush();assert.equal(h.plays,0);assert.equal(h.video.currentTime,0);assert.equal(h.loading.at(-1),false);
});

test('a next take starting at zero becomes ready without a seeked event',()=>{
    const h=harness();h.video.dataset.source='a.mp4';
    h.player.warm('b.mp4',0);const incoming=h.player.preloader;
    incoming.metadata();assert.equal(incoming.seeking,false);
    incoming.readyState=3;incoming.dispatchEvent(new Event('loadeddata'));
    assert.equal(h.player.preloadedSource,'b.mp4');
    h.player.seek('b.mp4',0,{autoplay:true});
    assert.equal(h.player.video,incoming);assert.equal(h.player.waitingForPreload,null);
});

test('preloading a trimmed take waits for its target frame',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.player.warm('b.mp4',3);
    const incoming=h.player.preloader;incoming.metadata();
    incoming.readyState=2;incoming.dispatchEvent(new Event('canplay'));
    assert.equal(h.player.preloadedSource,'');
    incoming.frame();assert.equal(h.player.preloadedSource,'b.mp4');
});

test('a warmed take reveals and transfers audio immediately without a second frame wait',()=>{
    const h=harness(),outgoing=h.video,incoming=h.player.preloader;
    outgoing.dataset.source='a.mp4';outgoing.paused=false;outgoing.muted=false;
    incoming.dataset.source='b.mp4';incoming.readyState=3;incoming._time=1;
    h.player.preloadedSource='b.mp4';
    h.player.seek('b.mp4',1,{autoplay:true});
    assert.equal(incoming.style.opacity,'1');assert.equal(incoming.muted,false);
    assert.equal(outgoing.paused,true);assert.equal(outgoing.muted,true);
    assert.equal(h.player.switching,false);assert.equal(h.loading.includes(true),false);
    assert.equal(h.plays,1);
});

function readyWarm(h, source, time=0) {
    h.player.warm(source,time);
    const incoming=h.player.preloader;
    incoming.metadata();incoming.readyState=3;incoming.seeking=false;incoming._time=time;
    incoming.dispatchEvent(new Event('seeked'));
    return incoming;
}

test('a discontinuous take in the same resource consumes the other decoder without seeking the visible one',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;h.video._time=2;h.video.paused=false;
    const incoming=readyWarm(h,'a.mp4',5);
    h.player.seek('a.mp4',5,{autoplay:true});
    assert.equal(h.player.video,incoming);assert.equal(h.video.currentTime,2);assert.equal(h.video.seeking,false);
    assert.equal(incoming.currentTime,5);assert.equal(h.plays,1);
});

test('A to B to C reserves unfinished B until its exact frame and future data are available',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;h.video.paused=false;
    h.player.warm('b.mp4',3);const incoming=h.player.preloader;
    h.player.seek('b.mp4',3,{autoplay:true});h.player.warm('c.mp4',1);
    assert.equal(incoming.src,'b.mp4');assert.equal(h.video.paused,true);
    incoming.metadata();incoming.readyState=3;incoming.dispatchEvent(new Event('canplay'));
    assert.equal(h.player.video,h.video,'canplay while seeking must not consume the buffer');
    incoming._time=3;incoming.seeking=false;incoming.readyState=2;incoming.dispatchEvent(new Event('seeked'));
    assert.equal(h.player.video,h.video,'one decoded frame must not resume playback');
    incoming.readyState=3;incoming.buffered={length:1,start:()=>3,end:()=>3.02};incoming.dispatchEvent(new Event('progress'));
    assert.equal(h.player.video,h.video,'a short buffered window must remain reserved');
    incoming.buffered={length:1,start:()=>3,end:()=>4};incoming.dispatchEvent(new Event('progress'));
    assert.equal(h.player.video,incoming);assert.equal(h.player.preloader.src,'c.mp4');
    assert.equal(incoming.src,'b.mp4');assert.equal(h.plays,1);assert.equal(h.loading.at(-1),false);
});

test('a prepared frame 200ms away cannot be revealed as the requested edit point',()=>{
    const h=harness();h.video.dataset.source='a.mp4';
    const incoming=readyWarm(h,'b.mp4',1);incoming._time=1.2;
    h.player.seek('b.mp4',1,{autoplay:true});
    assert.equal(h.player.video,h.video);assert.ok(h.player.waitingForPreload);assert.equal(h.plays,0);
});

test('scrubbing cancels a reserved transition and its queued successor without leaving loading active',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;
    h.player.warm('b.mp4',0);const incoming=h.player.preloader;
    h.player.seek('b.mp4',0,{autoplay:true});h.player.warm('c.mp4',0);
    h.player.seek('a.mp4',1,{autoplay:false});
    assert.equal(h.player.queuedWarm,null);assert.equal(h.loading.at(-1),false);
    incoming.metadata();incoming.readyState=3;incoming.seeking=false;incoming.dispatchEvent(new Event('canplay'));
    assert.equal(h.player.video,h.video);assert.equal(h.video.currentTime,1);assert.equal(h.plays,0);
});

test('a preload error releases the reservation and falls back without an event retry loop',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;
    h.player.warm('b.mp4',0);const incoming=h.player.preloader;
    h.player.seek('b.mp4',0,{autoplay:true});incoming.error={code:3};incoming.dispatchEvent(new Event('error'));
    assert.equal(h.player.waitingForPreload,null);assert.equal(h.video.src,'b.mp4');
    h.video.metadata();h.video.frame();h.flush();assert.equal(h.plays,1);assert.equal(h.loading.at(-1),false);
});


test('pausing a pending transition revokes autoplay even after its decoder finishes',()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;
    h.player.warm('b.mp4',0);const incoming=h.player.preloader;h.player.seek('b.mp4',0,{autoplay:true});h.player.pause();
    incoming.metadata();incoming.readyState=3;incoming.seeking=false;incoming.dispatchEvent(new Event('canplay'));
    assert.equal(h.player.video,h.video);assert.equal(h.player.target.autoplay,false);assert.equal(h.player.waitingForPreload,null);assert.equal(h.plays,0);
    h.player.seek('b.mp4',0,{autoplay:true});assert.equal(h.player.video,incoming);assert.equal(h.plays,1);
});


test('a paused decoder whose native frame callback never fires still completes startup after paint',()=>{
    const h=harness();let cancellations=0;
    h.video.requestVideoFrameCallback=()=>1;h.video.cancelVideoFrameCallback=()=>cancellations++;
    h.player.seek('a.mp4',0,{autoplay:true});h.video.metadata();h.video.frame();
    h.flush();assert.equal(h.plays,0);h.flush();assert.equal(h.plays,1);assert.equal(h.loading.at(-1),false);assert.ok(cancellations>0);
});


test('muted decoder priming rewinds the opening frame before a reserved take is consumed',async()=>{
    const h=harness();h.video.dataset.source='a.mp4';h.video.readyState=3;
    const incoming=h.player.preloader;incoming.requestVideoFrameCallback=()=>1;
    let resolve;incoming.play=()=>{incoming.paused=false;return new Promise(done=>resolve=done)};
    h.player.warm('b.mp4',3);incoming.metadata();incoming.frame();
    assert.equal(h.player.preloadedSource,'');
    h.player.seek('b.mp4',3,{autoplay:true});incoming._time=3.12;resolve();await Promise.resolve();
    assert.equal(incoming.currentTime,3);assert.equal(incoming.paused,true);assert.equal(h.player.video,h.video);
    incoming.seeking=false;incoming.readyState=3;incoming.dispatchEvent(new Event('seeked'));
    assert.equal(h.player.video,incoming);assert.equal(h.plays,1);assert.equal(incoming.currentTime,3);
});
