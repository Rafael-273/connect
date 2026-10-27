const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
const source=html.slice(html.indexOf('    const filmstripCache='),html.indexOf('    function rgba('));
function harness(){
    const images=[],clips=[],timers=new Map();let serial=0,videoWorkers=0;
    class Image{set src(value){this.source=value;images.push(this)}}
    const context=vm.createContext({URL,location:{href:'https://editor.test/preview/'},Image,
        setTimeout:(callback)=>{timers.set(++serial,callback);return serial},clearTimeout:id=>timers.delete(id),
        document:{createElement:()=>{videoWorkers++;throw new Error('take thumbnails must not decode video in the browser')},querySelectorAll:()=>clips},
    });
    vm.runInContext(source,context);
    const clip=()=>({dataset:{filmstripKey:'take-1',filmstripSource:'/source?thumbnail=1',filmstripImage:'true'},style:{},classList:{add(){}}});
    return {context,images,clips,timers,clip,get videoWorkers(){return videoWorkers}};
}
test('cuts sharing a take load one persisted image and never open a video decoder',()=>{
    const h=harness();h.clips.push(h.clip(),h.clip());h.context.queueFilmstripThumbnails();
    assert.equal(h.images.length,1);h.images[0].onload();h.context.queueFilmstripThumbnails();
    assert.equal(h.images.length,1);assert.equal(h.videoWorkers,0);
    assert.ok(h.clips.every(clip=>clip.style.backgroundImage.includes('thumbnail=1')));
});
test('pending thumbnail retries are delayed and deduplicated across rerenders',()=>{
    const h=harness();h.clips.push(h.clip());h.context.queueFilmstripThumbnails();h.images[0].onerror();
    h.context.queueFilmstripThumbnails();assert.equal(h.images.length,1);assert.equal(h.timers.size,1);
    const retry=h.timers.values().next().value;h.timers.clear();retry();
    assert.equal(h.images.length,2);h.images[1].onload();assert.equal(h.videoWorkers,0);
});
