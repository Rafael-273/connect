const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../external_media_timeline_presentation.js'),'utf8');
function harness(){
    const images=[],clips=[],timers=new Map(),frames=[];
    let observer, serial=0, now=0;
    class Image{set src(value){this.source=value;images.push(this)}removeAttribute(){delete this.source}}
    class IntersectionObserver{constructor(callback){observer=callback}observe(){}unobserve(){}}
    const target={addEventListener(){},querySelector:()=>({setAttribute(){}})};
    const row={...target,dataset:{trackKey:'video:Vídeo'},firstElementChild:{getBoundingClientRect:()=>({right:0})},classList:{toggle(){},contains:()=>false}};
    const tracks={...target,children:[row],querySelectorAll:()=>clips};
    const scroller={...target,classList:{toggle(){}},getBoundingClientRect:()=>({top:0,bottom:50,left:0,right:500})};
    const window={IntersectionObserver};
    vm.runInNewContext(source,{window,IntersectionObserver, Image,Date:{now:()=>now},localStorage:{getItem:()=>null},
        requestAnimationFrame:callback=>{frames.push(callback);return ++serial},
        setTimeout:(callback)=>{timers.set(++serial,callback);return serial},clearTimeout:id=>timers.delete(id)});
    const presentation=window.ConnectTimelinePresentation({scroller,tracks,dialog:target});
    const clip=(left,width=100,url='/source?thumbnail=1')=>({isConnected:true,closest:()=>null,
        parentElement:{parentElement:row},getBoundingClientRect:()=>({left,right:left+width,width,top:0,bottom:40}),
        dataset:{filmstripSource:url},style:{removeProperty(name){if(name==='background-image')delete this.backgroundImage}},classList:{add(){},remove(){}}});
    return {presentation,images,clips,timers,clip,
        show(...targets){observer(targets.map(target=>({target,isIntersecting:true})))},
        paint(){frames.splice(0).forEach(callback=>callback())},
        retry(){const callback=timers.values().next().value;timers.clear();now+=5000;callback()},
    };
}
test('only visible, readable cuts request thumbnails and a wide cut reuses the persisted image',()=>{
    const h=harness(),first=h.clip(0),duplicate=h.clip(100),narrow=h.clip(210,40,'/tiny'),offscreen=h.clip(700);
    h.clips.push(first,duplicate,narrow,offscreen);h.presentation.refresh();h.show(first,duplicate,narrow);h.paint();
    assert.equal(h.images.length,1);h.images[0].onload();h.paint();
    assert.ok(first.style.backgroundImage.includes('thumbnail=1'));
    assert.equal(duplicate.style.backgroundImage,undefined);
    assert.equal(narrow.style.backgroundImage,undefined);
    assert.equal(offscreen.style.backgroundImage,undefined);
    const wide=h.clip(300,200);h.clips.push(wide);h.presentation.refresh();h.show(wide);h.paint();
    assert.equal(h.images.length,1);assert.ok(wide.style.backgroundImage.includes('thumbnail=1'));
});
test('pending thumbnail retries are delayed and deduplicated across rerenders',()=>{
    const h=harness(),clip=h.clip(0);h.clips.push(clip);h.presentation.refresh();h.show(clip);h.paint();
    h.images[0].onerror();h.presentation.refresh();h.paint();
    assert.equal(h.images.length,1);assert.equal(h.timers.size,1);
    h.retry();h.paint();assert.equal(h.images.length,2);h.images[1].onload();h.paint();
    assert.ok(clip.style.backgroundImage.includes('thumbnail=1'));
});
