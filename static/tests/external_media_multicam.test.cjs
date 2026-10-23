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
