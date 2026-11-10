const {test}=require('node:test'),assert=require('node:assert/strict');
const {frameAt,frameMs,quantize,timecode,targets,align}=require('../external_media_editor_precision.js');
const base={start:1000,end:2000,mode:'move',duration:10000,fps:30,minimum:33};
test('thousands of repeated frame steps have no cumulative millisecond drift',()=>{
 for(const fps of [24,25,30,60,29.97]){let ms=0;for(let i=0;i<10000;i++)ms=frameMs(frameAt(ms,fps)+1,fps);assert.equal(ms,frameMs(10000,fps));assert.equal(frameAt(ms,fps),10000)}
 assert.equal(quantize(1020,30),1033);assert.equal(timecode(1033,30),'00:00:01:01');assert.equal(timecode(3661000,30),'01:01:01:00');
});
test('targets are quantized once, sorted and deduplicated, ignoring absent marker times',()=>{
 assert.deepEqual(targets([2000,1000,NaN,1001,Infinity,0],30),[0,1000,2000]);
});
test('either moving edge aligns; closest edge wins without accumulating two corrections',()=>{
 assert.deepEqual(align({...base,start:980,end:1980,points:[1000,2000],tolerance:50}),{start:1000,end:2000,guide:1000});
 assert.deepEqual(align({...base,start:980,end:1980,points:[1033,2000],tolerance:80}),{start:1000,end:2000,guide:2000});
 assert.deepEqual(align({...base,start:980,end:1880,points:[1900],tolerance:80}),{start:1000,end:1900,guide:1900});
});
test('resize changes only the moving edge and enforces sequence and minimum length',()=>{
 assert.deepEqual(align({...base,mode:'start',start:980,points:[1000],tolerance:50}),{start:1000,end:2000,guide:1000});
 assert.deepEqual(align({...base,mode:'end',end:1980,points:[2000],tolerance:50}),{start:1000,end:2000,guide:2000});
 assert.equal(align({...base,mode:'start',start:2100}).start,1967);
 assert.equal(align({...base,mode:'end',end:99999}).end,10000);
 assert.equal(align({...base,start:9900,end:10900}).start,9000);
});
test('pixel threshold follows zoom; disabled snapping still retains frame precision',()=>{
 const input={...base,start:1033,end:2033,points:[1000],tolerance:8/1000*10000};
 assert.equal(align(input).guide,1000);
 assert.equal(align({...input,tolerance:input.tolerance/16}).guide,null);
 assert.deepEqual(align({...input,enabled:false}),{start:1033,end:2033,guide:null});
});
test('moving preserves legacy duration and ignores out-of-bounds magnetic candidates',()=>{
 const result=align({...base,start:975,end:1982,points:[1000],tolerance:60});
 assert.equal(result.end-result.start,1007);
 assert.equal(align({...base,start:0,end:1000,points:[-33],tolerance:80}).guide,null);
 assert.equal(align({...base,start:9000,end:10000,points:[10033],tolerance:80}).guide,null);
});
