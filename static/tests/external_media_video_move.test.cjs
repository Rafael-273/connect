const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const template=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const start=template.indexOf('    function editedToMaster('),end=template.indexOf('    function framingPlayback(',start);
const clips=[
 {asset_id:'opening',timeline_in_ms:0,timeline_out_ms:3000,master_in_ms:0,master_out_ms:3000},
 {asset_id:'qr',timeline_in_ms:3000,timeline_out_ms:7000,master_in_ms:6000,master_out_ms:10000},
 {asset_id:'closing',timeline_in_ms:7000,timeline_out_ms:10000,master_in_ms:3000,master_out_ms:6000},
];
test('moving the closing take to the end keeps seeks, cuts and transform scopes on the original master clock',()=>{
 const context=vm.createContext({timeline:{video_reordered:true,clips},removedSegments:()=>[]});
 vm.runInContext(template.slice(start,end),context);
 for(const [edited,master] of [[0,0],[2000,2000],[3000,6000],[4000,7000],[7000,3000],[8000,4000],[9999,5999]]){
  assert.equal(vm.runInContext(`editedToMaster(${edited})`,context),master);
  assert.equal(vm.runInContext(`masterToEdited(${master})`,context),edited);
 }
 assert.equal(vm.runInContext("editedToMaster(7000,'before')",context),10000);
 assert.equal(vm.runInContext("editedToMaster(7000,'after')",context),3000);
 assert.equal(vm.runInContext("editedToMaster(10000,'before')",context),6000);
});
test('the continuous revision proxy uses edited time while raw take edits retain the master clock',()=>{
 const context=vm.createContext({timeline:{video_reordered:true,review_master_is_edited:true,clips},removedSegments:()=>[]});
 vm.runInContext(template.slice(start,end),context);
 assert.equal(vm.runInContext('editedToPlayback(8000)',context),8000);
 assert.equal(vm.runInContext('playbackToEdited(8000)',context),8000);
 assert.equal(vm.runInContext('editedToMaster(8000)',context),4000);
});
