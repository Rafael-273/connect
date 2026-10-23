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
