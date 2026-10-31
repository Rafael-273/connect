const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const template=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const start=template.indexOf('const queueTransformSave='),end=template.indexOf(";card.querySelectorAll('[data-transform]')",start);
const handler=template.slice(start,end)+';queueTransformSave(event);';

test('changing one framing slider preserves the latest position and zoom even when its card is stale',()=>{
 for(const [field,value] of [['scale',1.8],['x',-.4],['y',.2]]){
  const latest={metadata:{manual_transform:{scale:1.4,x:.604607,y:-.504607}}},scope={segment_start_ms:5000,segment_end_ms:10000};
  let preview,saved;
  const context={
   // This card predates moving the video directly in the preview.
   item:{id:'parent',metadata:{manual_transform:{scale:1,x:0,y:0}}},
   card:{querySelector:()=>({textContent:'Salvo'}),querySelectorAll:()=>[
    {dataset:{transform:'scale'},value:'1'},{dataset:{transform:'x'},value:'0'},{dataset:{transform:'y'},value:'0'},
   ]},event:{target:{dataset:{transform:field},value:String(value)}},
   selectedReframeDecision:()=>latest,focusSelectedTakeForTransform:()=>scope,
   applyDirectTransform:(_target,values)=>{preview=values},
   editorPersistence:{schedule:(_key,callback)=>callback()},
   saveTransform:(_id,values,_card,_version,range)=>{saved={...values,...range}},
  };
  vm.runInNewContext(handler,context);
  const expected={...latest.metadata.manual_transform,[field]:value};
  assert.deepEqual({...preview},expected);
  assert.deepEqual(saved,{...expected,...scope});
 }
});

test('framing controls follow a direct move and a subsequent server snapshot',()=>{
 const start=template.indexOf('    function syncFramingControls('),end=template.indexOf('    function applyDirectTransform(',start);
 const inputs=['scale','x','y'].map(key=>({dataset:{transform:key},value:'0'}));
 let decision={metadata:{manual_transform:{scale:1.5,x:.4,y:-.3}}};
 const context=vm.createContext({selectedReframeDecision:()=>decision,list:{querySelectorAll:()=>inputs}});
 vm.runInContext(template.slice(start,end)+';syncFramingControls();',context);
 assert.deepEqual(inputs.map(input=>Number(input.value)),[1.5,.4,-.3]);
 decision={metadata:{manual_transform:{scale:1.8,x:.4,y:-.3}}};
 vm.runInContext('syncFramingControls()',context);
 assert.deepEqual(inputs.map(input=>Number(input.value)),[1.8,.4,-.3]);
});
