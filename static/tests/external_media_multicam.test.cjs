const {test}=require('node:test');
const assert=require('node:assert/strict');
const {at,sourceTime}=require('../external_media_multicam.js');
test('camera intervals have exclusive end and primary fallback',()=>{
 const segments=[{start_ms:1000,end_ms:2000,source_in_ms:3500,rate:1.0005}];
 assert.equal(at(segments,999),undefined);
 assert.equal(at(segments,1000),segments[0]);
 assert.equal(at(segments,2000),undefined);
 assert.equal(sourceTime(segments[0],1500),4.00025);
});


test('secondary camera follows the replacement principal decoder and ignores pauses from the recycled one',()=>{
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 class Element extends EventTarget {
  constructor(){super();this.style={};this.children=[];this.paused=false;this.seeking=false;this.readyState=3;this.currentTime=0;this.playbackRate=1;this.duration=10;this.namespaceURI='svg'}
  append(...nodes){this.children.push(...nodes)}setAttribute(){}load(){}pause(){this.paused=true}play(){this.paused=false;return Promise.resolve()}
 }
 const module={exports:{}},document={createElement:()=>new Element(),createElementNS:()=>new Element()};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_multicam.js'),'utf8'),{module,document});
 const first=new Element(),next=new Element(),stage=new Element(),panel=new Element();next.playbackRate=2;
 const timeline={cameras:[{id:'cam',url:'/cam.mp4'}],camera_segments:[{id:'seg',start_ms:0,end_ms:1000,source_in_ms:0,rate:1,camera_source_id:'cam'}],decisions:[]};
 const camera=module.exports.create({panel,stage,mainVideo:first,getTimeline:()=>timeline,getTime:()=>0,editor:false});
 camera.sync();const image=stage.children[0];image.dispatchEvent(new Event('seeked'));assert.equal(image.paused,false);
 camera.setMainVideo(next);camera.sync();assert.equal(image.playbackRate,2);
 first.dispatchEvent(new Event('pause'));assert.equal(image.paused,false);
 next.paused=true;next.dispatchEvent(new Event('pause'));assert.equal(image.paused,true);
});

test('camera time fields preserve milliseconds and reject invalid clocks',()=>{
 const {formatTime,parseTime}=require('../external_media_multicam.js');
 for(const time of [0,173570,1387000,3600123])assert.equal(parseTime(formatTime(time)),time);
 assert.equal(formatTime(173570),'02:53.570');
 assert.equal(parseTime('23:07'),1387000);
 assert.equal(parseTime('02:53,57'),173570);
 for(const value of ['173.57','02:60','-01:20','abc'])assert.throws(()=>parseTime(value));
});

test('manual alignment confirmation uses source time after editorial cuts',()=>{
 const {needsManualAlignment}=require('../external_media_multicam.js');
 const timeline={cameras:[{id:'side',alignment:{status:'ALIGNED',validated_until_primary_ms:180000}}],clips:[{timeline_in_ms:0,timeline_out_ms:200000,source_in_ms:30000}]};
 assert.equal(needsManualAlignment(timeline,'side',150000),false);
 assert.equal(needsManualAlignment(timeline,'side',151000),true);
 assert.equal(needsManualAlignment(timeline,'missing',1000),true);
});

test('camera position adjustments obtain crop margin and retain an existing zoom',()=>{
 const {movableTransform}=require('../external_media_multicam.js');
 assert.deepEqual(movableTransform({scale:1,x:0,y:.4}),{scale:1.1,x:0,y:.4});
 assert.deepEqual(movableTransform({scale:1,x:0,y:0}),{scale:1,x:0,y:0});
 assert.deepEqual(movableTransform({scale:1.5,x:-.3,y:.2}),{scale:1.5,x:-.3,y:.2});
});

function cameraHarness(){
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 class Element extends EventTarget {
  constructor(){super();this.style={};this.children=[];this.paused=true;this.seeking=false;this.readyState=0;this._time=0;this.playbackRate=1;this.loads=0;this.namespaceURI='svg'}
  append(...nodes){this.children.push(...nodes)}setAttribute(){}
  load(){this.loads++;this.readyState=0;this._time=0;this.seeking=false}
  pause(){this.paused=true}play(){this.paused=false;return Promise.resolve()}
  get currentTime(){return this._time}set currentTime(value){this._time=value;this.seeking=true}
  frame(){this.readyState=2;this.seeking=false;this.dispatchEvent(new Event('seeked'))}
 }
 const module={exports:{}},document={createElement:()=>new Element(),createElementNS:()=>new Element()};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_multicam.js'),'utf8'),{module,document});
 let time=0;const main=new Element(),stage=new Element();
 const timeline={sequence:{fps:30},cameras:[{id:'cam',url:'/cam.mp4'}],camera_segments:[{id:'seg',start_ms:0,end_ms:1000,source_in_ms:0,rate:1,camera_source_id:'cam'}],decisions:[]};
 const camera=module.exports.create({panel:new Element(),stage,mainVideo:main,getTimeline:()=>timeline,getTime:()=>time,editor:false});
 camera.sync();return {camera,timeline,main,image:stage.children[0],setTime:value=>time=value};
}

test('a camera frame at zero is revealed on loadeddata without requiring seeked',()=>{
 const h=cameraHarness();h.image.readyState=2;h.image.dispatchEvent(new Event('loadeddata'));
 assert.equal(h.image.hidden,false);assert.equal(h.image.currentTime,0);assert.equal(h.image.paused,true);
});

test('a paused small camera scrub seeks accurately and a newer destination wins after seeked',()=>{
 const h=cameraHarness();h.image.readyState=2;h.image.dispatchEvent(new Event('loadeddata'));
 h.setTime(100);h.camera.sync();assert.equal(h.image.currentTime,.1);assert.equal(h.image.hidden,true);
 h.setTime(400);h.camera.sync();h.image.frame();assert.equal(h.image.currentTime,.4);assert.equal(h.image.hidden,true);
 h.image.frame();assert.equal(h.image.hidden,false);assert.equal(h.image.currentTime,.4);
});

test('adjacent camera segments from the same resource reuse the loaded decoder',()=>{
 const h=cameraHarness();h.image.readyState=2;h.image.dispatchEvent(new Event('loadeddata'));
 h.timeline.camera_segments.push({id:'next',start_ms:1000,end_ms:2000,source_in_ms:1000,rate:1,camera_source_id:'cam'});
 h.setTime(1000);h.camera.sync();assert.equal(h.image.loads,1);h.image.frame();assert.equal(h.image.hidden,false);
 h.setTime(2000);h.camera.sync();assert.equal(h.image.hidden,true);assert.equal(h.image.paused,true);
});

test('a stale seeked after switching camera resource cannot reveal an unready frame',()=>{
 const h=cameraHarness();h.image.readyState=2;h.image.dispatchEvent(new Event('loadeddata'));
 h.timeline.cameras[0].url='/replacement.mp4';h.camera.sync();h.image.dispatchEvent(new Event('seeked'));
 assert.equal(h.image.hidden,true);assert.equal(h.image.loads,2);
 h.image.readyState=2;h.image.dispatchEvent(new Event('loadeddata'));assert.equal(h.image.hidden,false);
});
