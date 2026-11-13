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
