const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const transitions=require('../external_media_music_transitions.js');
const cases=require('../../website/external_media/fixtures/music_transitions.json');
for(const row of cases)test(`geometry: ${row.name}`,()=>assert.deepEqual(transitions.windowFor(row.left,row.right,row.source_duration_ms,row.incoming_duration_ms??null),row.expected));
function setup(){
    const window={ConnectMusicTransitions:transitions};
    class Audio{constructor(){this.duration=10;this.currentTime=0;this.paused=true;this.dataset={};this.loads=0}pause(){this.paused=true}play(){this.paused=false;return Promise.resolve()}load(){this.loads++}removeAttribute(){}}
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_preview_audio.js'),'utf8'),{window,Audio});
    const controller=new window.ConnectPreviewAudio(new Audio());
    const tracks=[{id:1,url:'a',start_ms:0,end_ms:4000},{id:2,url:'b',start_ms:4000,end_ms:8000,source_in_ms:1000,audio_transition:{type:'CONSTANT_POWER',from_track_id:1,duration_ms:2000}}];
    const config={tracks,volume:.5};controller.configure(config);return {controller,config};
}
test('scheduled preview uses real handles, equal power and source sync across seeks',()=>{
    const {controller:c,config}=setup();
    for(const time of [4000,3000,4999,5000,3999,0,9000]){
        c.sync(config,time,{force:true});
        const active=c.position.active;
        if(time>=3000&&time<5000){assert.equal(active.length,2);assert.ok(Math.abs(active[0].gain**2+active[1].gain**2-1)<1e-10);assert.equal(c.channels[0].currentTime,time/1000);assert.equal(c.channels[1].currentTime,(time-3000)/1000);}
        if(time===9000)assert.equal(active.length,0);
    }
    c.sync(config,4000);assert.ok(Math.abs(c.channels[0].volume-.5/Math.sqrt(2))<1e-10);
});
test('duration edits preserve loaded channels; removal restores the hard cut immediately',()=>{
    const {controller:c,config}=setup(),channels=[...c.channels];
    config.tracks[1].audio_transition.duration_ms=1000;c.configure(config);c.sync(config,4000);
    assert.equal(c.channels[0],channels[0]);assert.equal(c.channels[1],channels[1]);assert.equal(c.channels[1].loads,1);
    config.tracks[1].audio_transition=null;c.configure(config);c.sync(config,4000);
    assert.equal(c.position.active.length,1);assert.equal(c.position.active[0].index,1);assert.equal(c.channels[0].paused,true);
});
test('gain envelopes multiply the crossfade and removing all music stops every channel',()=>{
    const {controller:c,config}=setup();
    c.updateVolume({...config,fade_in_seconds:8},8000,4000,[]);
    assert.ok(Math.abs(c.channels[0].volume-.25/Math.sqrt(2))<1e-10);
    c.configure(null);assert.equal(c.tracks.length,0);assert.ok(c.channels.every(channel=>channel.paused));
});
test('legacy overlapping projects respect requested fade duration',()=>{
    const {controller:c,config}=setup();Object.assign(config.tracks[1],{start_ms:2000,audio_transition:null,transition:'CONSTANT_POWER',transition_duration_ms:500});
    c.configure(config);c.sync(config,2250);assert.ok(Math.abs(c.channels[0].volume-.5/Math.sqrt(2))<1e-10);
    c.sync(config,3000);assert.ok(c.channels[0].volume<1e-10);assert.equal(c.channels[1].volume,.5);
});
test('legacy unscheduled playlists honor NONE and expose their real crossfade interval',()=>{
    const {controller:c,config}=setup();
    config.tracks.forEach(track=>{delete track.start_ms;delete track.end_ms;track.audio_transition=null;track.transition='NONE'});
    c.configure(config);c.sync(config,9500);assert.equal(c.position.overlap,0);
    c.sync(config,10500);assert.equal(c.position.index,1);
    config.tracks[1].transition='CONSTANT_POWER';config.tracks[1].transition_duration_ms=2000;
    c.configure(config);c.sync(config,9000);assert.equal(c.position.overlap,2);assert.equal(c.position.overlapProgress,.5);assert.equal(c.position.nextLocal,1);
});
test('editor payload preserves legacy fades and explicit removals, including zero duration',()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.match(/    function currentMusicTracks\(\)\{[^\n]+\}/)[0];
    const context=vm.createContext({timeline:{music:{tracks:[{id:1,transition:'CONSTANT_POWER',transition_duration_ms:0,audio_transition:null},{id:2,transition:'NONE',audio_transition:null}]}}});
    vm.runInContext(source,context);const rows=JSON.parse(vm.runInContext('JSON.stringify(currentMusicTracks())',context));
    assert.equal(rows[0].transition_duration_ms,0);assert.equal('audio_transition'in rows[0],false);assert.equal(rows[1].audio_transition,null);
});

function observeVolumes(controller){
    return controller.channels.map(channel=>{
        let volume=channel.volume??0;
        const writes=[];
        Object.defineProperty(channel,'volume',{configurable:true,get:()=>volume,set:value=>{volume=value;writes.push(value)}});
        return writes;
    });
}

test('steady playback does not repeatedly mute and restore soundtrack volume',()=>{
    const {controller:c,config}=setup();
    c.updateVolume(config,8000,1000,[]);
    const writes=observeVolumes(c);
    for(let time=1016;time<2000;time+=16)c.updateVolume(config,8000,time,[]);
    assert.deepEqual(writes,[[],[]]);
});

test('ducking is applied once without intermediate unattenuated or muted writes',()=>{
    const {controller:c,config}=setup();
    const ducked={...config,ducking_enabled:true,duck_db:20,attack_ms:100};
    const captions=[{start_ms:500,end_ms:2000}];
    c.updateVolume(ducked,8000,1000,captions);
    const writes=observeVolumes(c);
    c.updateVolume(ducked,8000,1100,captions);
    c.sync(ducked,1100,{force:true});
    c.configure(ducked);
    assert.deepEqual(writes,[[],[]]);
    assert.ok(Math.abs(c.channels[0].volume-.05)<1e-10);
});

test('crossfade gains are written directly with fades and automation already applied',()=>{
    const {controller:c,config}=setup();
    const mixed={...config,fade_in_seconds:8,audio_mix:{music_keyframes:[{time_ms:0,gain_db:-6}]}};
    c.updateVolume(mixed,8000,3900,[]);
    const writes=observeVolumes(c);
    c.updateVolume(mixed,8000,4000,[]);
    const expected=.5*.5*Math.pow(10,-6/20)/Math.sqrt(2);
    for(const channelWrites of writes){
        assert.equal(channelWrites.length,1);
        assert.ok(Math.abs(channelWrites[0]-expected)<1e-10);
    }
});

test('a split music source uses clip identities for its constant-power join',()=>{
    const window=transitions.windowFor(
        {clip_id:'music-a',start_ms:0,end_ms:5000,source_in_ms:0},
        {clip_id:'music-b',start_ms:5000,end_ms:10000,source_in_ms:5000,audio_transition:{type:'CONSTANT_POWER',from_clip_id:'music-a',duration_ms:2000}},
        20000,20000,
    );
    assert.deepEqual(window,{start_ms:4000,end_ms:6000,duration_ms:2000});
});

test('the primary volume control saves only its captured clip, including rapid edits on both cuts',async()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.slice(html.indexOf('    function queueMusicVolume('),html.indexOf('    function resetMusicSample('));
    const timeline={music:{volume:.15,tracks:[{clip_id:'left',volume:1},{clip_id:'right',volume:1}]},music_volume:.15};
    const scheduled=new Map(),writes=[];
    const context=vm.createContext({timeline,musicPreviewUrl:'/music/',
        currentMusicTracks:()=>structuredClone(timeline.music.tracks),
        applyLocalMusicTracks:tracks=>{timeline.music.tracks=tracks},
        editorPersistence:{schedule:(key,callback)=>scheduled.set(key,callback)},
        api:async(url,body)=>{writes.push(structuredClone(body));return {}},applyTimeline:()=>{},
        updateMusicVolume:()=>{throw new Error('clip edits must not use global volume')},
    });
    vm.runInContext(source,context);
    const card=id=>({dataset:{musicClipId:id},querySelector:()=>({textContent:''})});
    context.queueMusicVolume(25,card('left'));
    assert.deepEqual(timeline.music.tracks.map(track=>track.volume),[.25,1]);
    context.queueMusicVolume(80,card('right'));
    assert.deepEqual(timeline.music.tracks.map(track=>track.volume),[.25,.8]);
    assert.equal(timeline.music.volume,.15);assert.equal(timeline.music_volume,.15);
    await scheduled.get('music-tracks')();
    assert.deepEqual(writes[0].tracks.map(track=>[track.clip_id,track.volume]),[['left',.25],['right',.8]]);
});

test('preview keeps different clip gains when both cuts refer to the same music file',()=>{
    const {controller:c,config}=setup();
    config.tracks=[{id:1,clip_id:'left',url:'a',volume:.25,start_ms:0,end_ms:4000},
        {id:1,clip_id:'right',url:'a',volume:.8,start_ms:4000,end_ms:8000,source_in_ms:4000,transition:'NONE'}];
    c.configure(config);c.sync(config,2000);
    assert.equal(c.channels[0].volume,.125);assert.equal(c.channels[1].volume,0);
    c.sync(config,5000);assert.equal(c.channels[0].volume,0);assert.equal(c.channels[1].volume,.4);
});

test('save payload preserves independent gains and source offsets for cuts of one music file',()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.match(/    function currentMusicTracks\(\)\{[^\n]+\}/)[0];
    const context=vm.createContext({timeline:{music:{tracks:[
        {id:7,clip_id:'left',start_ms:0,end_ms:4000,source_in_ms:0,volume:.25},
        {id:7,clip_id:'right',start_ms:4000,end_ms:8000,source_in_ms:4000,volume:.8},
    ]}}});
    vm.runInContext(source,context);
    const rows=JSON.parse(vm.runInContext('JSON.stringify(currentMusicTracks())',context));
    assert.deepEqual(rows.map(row=>[row.track_id,row.clip_id,row.source_in_ms,row.volume]),[[7,'left',0,.25],[7,'right',4000,.8]]);
});

test('keyboard cut targets the selected music clip instead of cutting the video',()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.slice(html.indexOf('    function splitSelectedAtPlayhead('),html.indexOf('    let magneticSnapping='));
    const calls=[],context=vm.createContext({
        timeline:{music:{tracks:[{id:7,clip_id:'left'},{id:7,clip_id:'right'}]}},
        inspectorSelection:{kind:'music',id:'music-right'},globalMs:6000,
        splitMusicClip:(id,point)=>calls.push(['music',id,point]),addBladeAt:point=>calls.push(['video',point]),
    });
    vm.runInContext(source,context);context.splitSelectedAtPlayhead();
    assert.deepEqual(calls,[['music','right',6000]]);
    context.inspectorSelection={kind:'music',id:'missing'};context.splitSelectedAtPlayhead();
    assert.equal(calls.length,1);
    context.inspectorSelection={kind:'video',id:'take'};context.splitSelectedAtPlayhead();
    assert.deepEqual(calls[1],['video',6000]);
    assert.match(html,/if\(modified&&key==='k'\)\{event.preventDefault\(\);splitSelectedAtPlayhead\(\);return\}/);
});

test('deleting one cut removes only its clip and dependent transition, preserving other music timing and gain',async()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const selection=html.slice(html.indexOf('    function selectedTimelineItemForDeletion('),html.indexOf('    function updateEditControls('));
    const deletion=html.slice(html.indexOf('    async function deleteMusicClip('),html.indexOf('    async function deleteSelectedSegment('));
    const tracks=[{track_id:7,clip_id:'left',start_ms:0,end_ms:4000,volume:.25},
        {track_id:7,clip_id:'right',start_ms:4000,end_ms:8000,source_in_ms:4000,volume:.8,audio_transition:{from_clip_id:'left',type:'CONSTANT_POWER'},transition:'NONE'}];
    const timeline={music:{tracks:tracks.map(track=>({...track,id:track.track_id}))}},writes=[],order=[];
    const context=vm.createContext({timeline,inspectorSelection:{kind:'music',id:'music-left'},musicPreviewUrl:'/music/',
        currentMusicTracks:()=>structuredClone(tracks),editorPersistence:{flushScheduled:()=>order.push('flush')},
        api:async(url,body)=>{order.push('enqueue');writes.push(structuredClone(body));return {}},
        applyLocalMusicTracks:rows=>{order.push('local');timeline.music.tracks=rows.map(track=>({...track,id:track.track_id}))},
        selectInspector:()=>{},renderTimelineSelection:()=>{},updateEditControls:()=>{},applyTimeline:()=>{},alert:message=>assert.fail(message),
    });
    vm.runInContext(selection+deletion,context);
    assert.equal(context.selectedTimelineItemForDeletion().id,'left');
    await context.deleteMusicClip('left');
    assert.deepEqual(order,['flush','enqueue','local']);
    assert.deepEqual(writes[0].tracks,[{track_id:7,clip_id:'right',start_ms:4000,end_ms:8000,source_in_ms:4000,volume:.8,audio_transition:null,transition:'NONE'}]);
    assert.equal(timeline.music.tracks.length,1);
    context.inspectorSelection={kind:'music',id:'music-right',transitionIndex:1};
    assert.equal(context.selectedTimelineItemForDeletion(),null);
});

test('deleting the final music clip persists an explicitly empty lane',async()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.slice(html.indexOf('    async function deleteMusicClip('),html.indexOf('    async function deleteSelectedSegment('));
    let saved,local;
    const context=vm.createContext({currentMusicTracks:()=>[{clip_id:'last',track_id:7}],musicPreviewUrl:'/music/',
        editorPersistence:{flushScheduled(){}},api:async(url,body)=>{saved=body.tracks;return {}},
        applyLocalMusicTracks:rows=>{local=rows},selectInspector(){},renderTimelineSelection(){},updateEditControls(){},applyTimeline(){},alert:message=>assert.fail(message)});
    vm.runInContext(source,context);await context.deleteMusicClip('last');
    assert.equal(saved.length,0);assert.equal(local.length,0);
});

test('deleting music refreshes the open sequence panel before the server responds, including an empty lane',async()=>{
    const html=fs.readFileSync(path.join(__dirname,'../../templates/member/external_media/preview.html'),'utf8');
    const source=html.slice(html.indexOf('    async function deleteMusicClip('),html.indexOf('    async function deleteSelectedSegment('));
    let tracks=[{clip_id:'left',track_id:7},{clip_id:'right',track_id:7}],resolve;
    const panels=[];
    const context=vm.createContext({currentMusicTracks:()=>structuredClone(tracks),musicPreviewUrl:'/music/',
        editorPersistence:{flushScheduled(){}},api:()=>new Promise(done=>{resolve=done}),
        applyLocalMusicTracks:rows=>{tracks=rows},selectInspector:(kind,id)=>panels.push({kind,id,count:tracks.length}),
        renderTimelineSelection(){},updateEditControls(){},applyTimeline(){},alert:message=>assert.fail(message)});
    vm.runInContext(source,context);
    const first=context.deleteMusicClip('left');
    assert.deepEqual(panels,[{kind:'music',id:'music-right',count:1}]);
    resolve({});await first;
    const last=context.deleteMusicClip('right');
    assert.deepEqual(panels[1],{kind:'music',id:null,count:0});
    resolve({});await last;
});
