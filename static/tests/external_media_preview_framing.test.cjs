const {test}=require('node:test');
const assert=require('node:assert/strict');
const {decisionAt,playback}=require('../external_media_preview_framing.js');
const auto={id:'auto',type:'reframe',source_id:'take',plan_reference:{plan:{crop_width:300,crop_height:500}},metadata:{}};
const left={asset_id:'take',source_in_ms:1000,source_out_ms:6000,master_in_ms:12000,master_out_ms:17000};
const right={asset_id:'take',source_in_ms:6000,source_out_ms:11000,master_in_ms:17000,master_out_ms:22000};
const asset={url:'/raw-take/'};
const timeline=operations=>({framing_master_url:'/normalized-master/',decisions:[auto,...operations]});
const scoped=(metadata={},enabled=true)=>({type:'reframe',source_id:'take',enabled,plan_reference:auto.plan_reference,metadata:{segment_start_ms:12000,segment_end_ms:17000,...metadata}});

test('a manual blade take uses raw media while the next take retains the baked automatic framing',()=>{
 const data=timeline([scoped({manual_transform:{scale:1.4,x:.3,y:0}})]);
 assert.deepEqual(playback(data,left,asset),{url:'/raw-take/',inMs:1000,outMs:6000,automatic:false});
 assert.deepEqual(playback(data,right,asset),{url:'/normalized-master/',inMs:17000,outMs:22000,automatic:true});
 assert.equal(decisionAt(data.decisions,'take',17000).id,'auto','half-open blade boundary belongs to the following take');
});
test('restoring original framing affects only the selected range, and restoring AI returns to the normalized master',()=>{
 const disabled=scoped({},false),data=timeline([disabled]);
 assert.equal(playback(data,left,asset).automatic,false);
 assert.equal(playback(data,right,asset).automatic,true);
 disabled.enabled=true;
 assert.equal(playback(data,left,asset).automatic,true);
});
test('manual framing on another source never suppresses the current automatic source',()=>{
 const data=timeline([{...scoped({manual_transform:{scale:1.8}}),source_id:'other'}]);
 assert.equal(playback(data,left,asset).automatic,true);
});
test('a missing master, absent automatic plan, or source color edit uses the raw source clock',()=>{
 for(const data of [{...timeline([]),framing_master_url:null},{...timeline([]),decisions:[]}]){
  assert.equal(playback(data,left,asset).url,asset.url);
  assert.equal(playback(data,left,asset).inMs,1000);
 }
 assert.equal(playback(timeline([]),{...left,color_adjustment:{brightness:10}},asset).automatic,false);
});
test('source-wide manual choices and latest scoped overrides take precedence over automatic plans',()=>{
 const manual={...auto,metadata:{manual_transform:{scale:1.5}}};
 assert.equal(playback({...timeline([]),decisions:[manual]},left,asset).automatic,false);
 const data=timeline([scoped({manual_transform:{scale:1.6}}),scoped({},false)]);
 assert.equal(decisionAt(data.decisions,'take',12000).enabled,false);
 assert.equal(playback(data,left,asset).automatic,false);
 assert.equal(playback(data,right,asset).automatic,true);
});
