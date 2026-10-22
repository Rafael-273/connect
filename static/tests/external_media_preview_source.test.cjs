const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Video extends EventTarget {
    constructor(){super();this.dataset={};this.style={};this.classList={add(){}};this.parentNode={append(){}};this.readyState=0;this.paused=true;this.muted=false;this.volume=1;this.playbackRate=1;this.duration=10;this.seeking=false;this.error=null;this._time=0;this.loads=0}
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
    const preload=h.player.preloader;preload.dataset.source='a.mp4';h.player.seek('a.mp4',2,{autoplay:true});
    h.player.seek('b.mp4',4,{autoplay:false});preload.dispatchEvent(new Event('canplay'));preload.frame();h.flush();
    assert.equal(h.player.video,h.video);assert.equal(h.video.dataset.source,'b.mp4');assert.equal(h.video.currentTime,4);assert.equal(h.plays,0);
});
test('interrupting a buffer swap keeps distinct decoders and cannot reveal the previous target later',()=>{
    const h=harness(),outgoing=h.video,incoming=h.player.preloader;
    outgoing.dataset.source='b.mp4';incoming.dataset.source='a.mp4';incoming.readyState=2;incoming._time=1;h.player.preloadedSource='a.mp4';
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
