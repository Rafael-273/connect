const {test}=require('node:test'),assert=require('node:assert/strict');
const {removeVideoRange}=require('../external_media_editor_timeline.js');
function fixture(){
 const clips=[{id:'a',asset_id:'camera',timeline_in_ms:0,timeline_out_ms:10000,source_in_ms:500,source_out_ms:10500,master_in_ms:0,master_out_ms:10000,effects:[{type:'transform',scale:1.2}]}];
 return {sequence:{duration_ms:10000},clips,decisions:[],captions:[],video_splits_ms:[2000,4000,6000],markers:[{time_ms:0},{time_ms:6000}],video_tracks:[{role:'main',clips}],overlays:[{id:'text',start_ms:7000,end_ms:8000}],brolls:[{id:'b',start_ms:7000,end_ms:8000}],music:{tracks:[{id:1,start_ms:0,end_ms:10000}]}};
}
const cut=(timeline,start=2000,end=4000,masterStart=start,masterEnd=end)=>removeVideoRange(timeline,{start,end,masterStart,masterEnd,id:'pending-test'});
test('ripple deletion updates video and audio source ranges while preserving the input snapshot',()=>{
 const original=fixture(),before=JSON.stringify(original),{timeline,mapTime}=cut(original);
 assert.equal(JSON.stringify(original),before);assert.equal(timeline.sequence.duration_ms,8000);
 assert.deepEqual(timeline.clips.map(c=>[c.timeline_in_ms,c.timeline_out_ms,c.source_in_ms,c.source_out_ms,c.master_in_ms,c.master_out_ms]),[[0,2000,500,2500,0,2000],[2000,8000,4500,10500,4000,10000]]);
 assert.deepEqual([mapTime(1000),mapTime(3000),mapTime(8000)],[1000,2000,6000]);
 assert.equal(timeline.video_tracks[0].clips,timeline.clips);assert.notEqual(timeline.clips[0].effects[0],timeline.clips[1].effects[0]);
 assert.deepEqual(timeline.video_splits_ms,[2000,4000]);assert.deepEqual(timeline.markers.map(m=>m.time_ms),[0,4000]);
 assert.equal(timeline.overlays,original.overlays);assert.equal(timeline.brolls,original.brolls);assert.equal(timeline.music,original.music);
});
test('caption deletion and all overlap patterns match the server cut semantics',()=>{
 const original=fixture();original.captions=[
  {id:'before-short',start_ms:100,end_ms:110},{id:'inside',start_ms:2500,end_ms:3500},
  {id:'left',start_ms:1500,end_ms:2500},{id:'right',start_ms:3500,end_ms:4500},
  {id:'spanning',start_ms:1000,end_ms:5000},{id:'after',start_ms:5000,end_ms:6000},
  {id:'tiny-remnant',start_ms:1990,end_ms:3000},
 ];
 assert.deepEqual(cut(original).timeline.captions.map(c=>[c.id,c.start_ms,c.end_ms]),[
  ['before-short',100,110],['left',1500,2000],['right',2000,2500],['spanning',1000,3000],['after',3000,4000],
 ]);
});
test('successive pending deletions retain original master coordinates and source trims',()=>{
 const first=cut(fixture()).timeline,second=cut(first,2000,4000,4000,6000).timeline;
 assert.equal(second.sequence.duration_ms,6000);
 assert.deepEqual(second.decisions.map(d=>[d.source_in_ms,d.source_out_ms]),[[2000,4000],[4000,6000]]);
 assert.deepEqual(second.clips.map(c=>[c.timeline_in_ms,c.timeline_out_ms,c.source_in_ms,c.master_in_ms]),[[0,2000,500,0],[2000,6000,6500,6000]]);
});
test('legacy ranges can resolve master coordinates through earlier accepted cuts',()=>{
 const original=fixture();delete original.clips[0].master_in_ms;delete original.clips[0].master_out_ms;
 const result=removeVideoRange(original,{start:2000,end:4000,masterStart:3000,masterEnd:5000,id:'legacy',masterAt:value=>value+1000}).timeline;
 assert.deepEqual(result.clips.map(c=>[c.master_in_ms,c.master_out_ms]),[[1000,3000],[5000,11000]]);
});
test('deleting the entire sequence produces an empty canonical timeline and validates ranges',()=>{
 const result=cut(fixture(),0,10000).timeline;assert.equal(result.sequence.duration_ms,0);assert.deepEqual(result.clips,[]);assert.deepEqual(result.video_splits_ms,[]);
 for(const [start,end] of [[-1,1000],[0,10001],[50,60],[1000,500]])assert.throws(()=>cut(fixture(),start,end));
});
