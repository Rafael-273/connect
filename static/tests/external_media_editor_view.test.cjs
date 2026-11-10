const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {IntervalIndex,reconcileChildren,waveformWindow}=require('../external_media_editor_view.js');

test('interval lookup preserves canonical precedence, overlaps and exclusive ends',()=>{
    const items=[{id:'top',start_ms:20,end_ms:40},{id:'long',start_ms:0,end_ms:100},{id:'other',start_ms:20,end_ms:30}];
    const index=new IntervalIndex(items);
    for(const time of [-1,0,19,20,29,30,39,40,99,100])assert.deepEqual(index.at(time),items.filter(item=>item.start_ms<=time&&time<item.end_ms));
});

test('derived interval index is rebuilt after a timing edit and keeps canonical object references',()=>{
    const item={start_ms:10,end_ms:20,text:'Antes'},index=new IntervalIndex([item]);
    item.text='Depois';assert.equal(index.at(15)[0].text,'Depois');
    item.start_ms=30;item.end_ms=40;
    const updated=new IntervalIndex([item]);assert.deepEqual(updated.at(15),[]);assert.equal(updated.at(35)[0],item);
});

test('index agrees with a full scan of a large timeline including long overlays',()=>{
    const items=Array.from({length:10000},(_,id)=>({id,start_ms:id*80,end_ms:id*80+(id%17===0?20000:60)}));
    const index=new IntervalIndex(items);
    for(let time=0;time<800000;time+=3741)assert.deepEqual(index.at(time),items.filter(item=>item.start_ms<=time&&time<item.end_ms));
});

class Parent {
    constructor(nodes){this.nodes=nodes;this.writes=0;this.sync()}
    sync(){this.firstChild=this.nodes[0]||null;this.nodes.forEach((node,index)=>node.nextSibling=this.nodes[index+1]||null)}
    insertBefore(node,cursor){this.writes++;const previous=this.nodes.indexOf(node);if(previous>=0)this.nodes.splice(previous,1);this.nodes.splice(cursor?this.nodes.indexOf(cursor):this.nodes.length,0,node);this.sync()}
    removeChild(node){this.writes++;this.nodes.splice(this.nodes.indexOf(node),1);this.sync()}
}
test('reconciliation preserves nodes and makes no structural writes for an unchanged selection',()=>{
    const a={},b={},c={},parent=new Parent([a,b,c]);
    reconcileChildren(parent,[a,b,c]);assert.equal(parent.writes,0);
    reconcileChildren(parent,[c,a,b]);assert.deepEqual(parent.nodes,[c,a,b]);assert.equal(parent.writes,1);
    reconcileChildren(parent,[b]);assert.equal(parent.nodes.length,1);assert.equal(parent.nodes[0],b);
});

test('waveforms allocate only the visible window even at extreme zoom',()=>{
    const clip={left:-15000,right:17000,width:32000,top:10,bottom:40,height:30};
    const viewport={left:0,right:1200,top:0,bottom:600};
    const view=waveformWindow(clip,viewport);
    assert.equal(view.width,1328);assert.equal(view.offset,14936);
    assert.ok(view.from>0&&view.to<1);
    assert.equal(waveformWindow({...clip,top:700,bottom:730},viewport),null);
    assert.equal(waveformWindow({...clip,left:1300,right:33300},viewport),null);
});

test('cached drag geometry includes horizontal scrolling without reading layout on every move',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const point=source.match(/    function timelinePoint\([^\n]+/)[0];
    const context=vm.createContext({editorScroller:{scrollLeft:200}});
    vm.runInContext(`${point};result=timelinePoint({clientX:300},null,10000,{left:100,width:1000,scrollLeft:100})`,context);
    assert.equal(context.result,3000);
});
