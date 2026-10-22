const {test}=require('node:test'),assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),window={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_preview_audio.js'),'utf8'),{window});
class Video extends EventTarget {
 constructor(){super();this.paused=false;this.seeking=false;this.error=null;this.readyState=4}
 emit(name){this.dispatchEvent(new Event(name))}
}
function setup(){
 const controller={isPlaying:false,starts:0,pauses:0,play(){this.isPlaying=true;this.starts++;return Promise.resolve()},pause(){this.isPlaying=false;this.pauses++}};
 let syncs=0,id=0;const timers=new Map();
 const clock=new window.ConnectPreviewSoundtrackClock(controller,{sync:()=>syncs++,setTimer:callback=>{timers.set(++id,callback);return id},clearTimer:id=>timers.delete(id)});
 const video=new Video();clock.setVideo(video);
 return {clock,video,controller,get syncs(){return syncs},expire(){for(const callback of [...timers.values()])callback()}};
}
test('play intent and canplay cannot start soundtrack before playing and a decoded frame',()=>{
 const h=setup();h.video.emit('play');h.clock.frame(h.video,0);h.video.emit('canplay');h.clock.frame(h.video,.1);assert.equal(h.controller.starts,0);
 h.video.emit('playing');assert.equal(h.controller.starts,0);h.clock.frame(h.video,.2);assert.equal(h.controller.starts,1);assert.equal(h.syncs,1);
});
test('waiting pauses all soundtrack playback and a new frame resynchronizes on recovery',()=>{
 const h=setup();h.video.emit('playing');h.clock.frame(h.video,1);h.video.emit('waiting');assert.equal(h.controller.isPlaying,false);
 h.clock.frame(h.video,1.1);assert.equal(h.controller.starts,1);
 h.video.emit('playing');h.clock.frame(h.video,1.2);assert.equal(h.controller.starts,2);assert.equal(h.syncs,2);
});
test('a decoder freeze without waiting pauses music; repeated identical frame times cannot restart it',()=>{
 const h=setup();h.video.emit('playing');h.clock.frame(h.video,2);h.expire();assert.equal(h.controller.isPlaying,false);
 h.clock.frame(h.video,2);h.clock.frame(h.video,2);assert.equal(h.controller.starts,1);
 h.clock.frame(h.video,2.04);assert.equal(h.controller.starts,2);assert.equal(h.syncs,2);
});
test('loading, seeking, errors and a user pause prevent restart while frames or stale events arrive',()=>{
 const h=setup();h.video.emit('playing');h.clock.setLoading(true);h.clock.frame(h.video,0);assert.equal(h.controller.starts,0);
 h.clock.setLoading(false);h.clock.frame(h.video,.1);assert.equal(h.controller.starts,1);
 for(const event of ['seeking','error','pause','ended','emptied','loadstart']){
  h.video.emit(event);h.clock.frame(h.video,2);assert.equal(h.controller.isPlaying,false);
  h.video.emit('playing');h.clock.frame(h.video,3);assert.equal(h.controller.isPlaying,true);
 }
});
test('source swaps ignore old decoder events and buffered network stalls do not stop healthy video',()=>{
 const h=setup();h.video.emit('playing');h.clock.frame(h.video,1);const next=new Video();h.clock.setVideo(next);
 h.video.emit('playing');h.clock.frame(h.video,2);assert.equal(h.controller.isPlaying,false);
 next.emit('playing');h.clock.frame(next,0);h.video.emit('waiting');assert.equal(h.controller.isPlaying,true);
 next.emit('stalled');assert.equal(h.controller.isPlaying,true);
 next.readyState=2;next.emit('stalled');assert.equal(h.controller.isPlaying,false);
});
test('resume corrects even sub-550ms drift and seeks overlapping channels to the video clock',()=>{
 const channels=[{currentTime:3.3,duration:10,paused:true,volume:0,pause(){},play(){return Promise.resolve()}},{currentTime:1.3,duration:10,paused:true,volume:0,pause(){},play(){return Promise.resolve()}}];
 const audio=new window.ConnectPreviewAudio(channels[0]);audio.channels=channels;audio.tracks=[{start_ms:0,end_ms:5000},{start_ms:2000,end_ms:8000}];
 audio.sync({},3000);assert.equal(channels[0].currentTime,3.3);assert.equal(channels[1].currentTime,1.3);
 audio.sync({},3000,{force:true});assert.equal(channels[0].currentTime,3);assert.equal(channels[1].currentTime,1);
});
test('a late music play completion after waiting leaves soundtrack paused',async()=>{
 const h=setup();let resolve;h.controller.play=function(){this.isPlaying=true;return new Promise(done=>resolve=done)};
 h.video.emit('playing');h.clock.frame(h.video,0);h.video.emit('waiting');resolve();await Promise.resolve();await Promise.resolve();assert.equal(h.controller.isPlaying,false);
});
