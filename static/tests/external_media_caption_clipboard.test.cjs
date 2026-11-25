const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const template=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const start=template.indexOf('    function selectedCaption('),end=template.indexOf('    async function splitMusicClip(',start);
function harness(){
 const requests=[],stored=new Map();
 const cue={id:7,track_id:3,start_ms:1000,end_ms:2500,text:'Texto copiado'};
 const context=vm.createContext({captionClipboard:null,captionClipboardKey:'project',inspectorSelection:{kind:'caption',id:7},timeline:{captions:[cue]},globalMs:5000,
  sessionStorage:{setItem:(key,value)=>stored.set(key,value)},updateEditControls(){},document:{getElementById:()=>({title:''})},
  snapToFrame:value=>value,api:async(url,body)=>{requests.push({url,body});return {timeline:{captions:[]}}},
  applyTimeline(){},selectInspector(){},seek(){},renderTimelineSelection(){},alert:error=>{throw Error(error)},
 });
 vm.runInContext(template.slice(start,end),context);
 return {context,requests,stored,cue,run:code=>vm.runInContext(code,context)};
}
test('copy is a snapshot; paste creates a new caption at the cursor even after the original changes or is deleted',async()=>{
 const h=harness();h.run('copyCaption()');h.cue.text='Mudou';h.run('timeline.captions=[]');
 await h.run('pasteCaption()');
 assert.deepEqual({...h.requests[0].body},{track_id:3,text:'Texto copiado',duration_ms:1500,start_ms:5000});
 assert.equal(h.requests[0].body.id,undefined);
 assert.equal(h.stored.size,1);
});
test('repeated pastes use the current cursor and keep the clipboard contents unchanged',async()=>{
 const h=harness();h.run('copyCaption()');await h.run('pasteCaption()');h.run('globalMs=7000');await h.run('pasteCaption()');
 assert.deepEqual(h.requests.map(item=>item.body.start_ms),[5000,7000]);
 assert.equal(h.requests[0].body.text,h.requests[1].body.text);
});
test('splitting a caption sends the edited-time cut to the caption endpoint',async()=>{
 const h=harness();await h.run('splitCaption(7,1500)');
 assert.ok(h.requests[0].url.endsWith('subtitles/7/'));
 assert.deepEqual({...h.requests[0].body},{split_at_ms:1500});
});
