const {test}=require('node:test'),assert=require('node:assert/strict');
const {rectangle,select}=require('../external_media_caption_selection.js');
const items=[
 {id:1,rect:{left:100,top:10,right:180,bottom:40}},
 {id:2,rect:{left:200,top:10,right:280,bottom:40}},
 {id:3,rect:{left:100,top:60,right:180,bottom:90}},
];
test('dragging in either direction selects intersecting captions without requiring complete containment',()=>{
 for(const [first,last] of [[{x:140,y:0},{x:220,y:50}],[{x:220,y:50},{x:140,y:0}]]){
  assert.deepEqual([...select(rectangle(first,last),items)],['1','2']);
 }
});
test('a rectangle can include caption tracks in different languages',()=>{
 assert.deepEqual([...select(rectangle({x:80,y:0},{x:190,y:100}),items)],['1','3']);
});
test('normal selection replaces prior IDs while modifier selection adds to them',()=>{
 const rect=rectangle({x:210,y:0},{x:270,y:50});
 assert.deepEqual([...select(rect,items)],['2']);
 assert.deepEqual([...select(rect,items,['1'])],['1','2']);
 assert.deepEqual([...select(rect,items,['2'])],['2']);
});
test('touching a caption edge or dragging an empty region does not select it',()=>{
 assert.equal(select(rectangle({x:0,y:0},{x:100,y:100}),items).size,0);
 assert.equal(select(rectangle({x:300,y:0},{x:350,y:100}),items).size,0);
});
