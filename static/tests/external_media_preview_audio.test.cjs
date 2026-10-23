const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const window={};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../external_media_preview_audio.js'),'utf8'),{window});
const controller=()=>new window.ConnectPreviewAudio({});
const config={attack_ms:100,hold_ms:100,release_ms:200,speech_gap_hold_ms:100,duck_db:20};

test('cached ducking preserves attack, hold, release and the gap between speech beds',()=>{
    const audio=controller(),cues=[{start_ms:1000,end_ms:2000},{start_ms:2100,end_ms:2500},{start_ms:4000,end_ms:4500}];
    const cases=[[900,1],[1000,1],[1050,.55],[1100,.1],[2400,.1],[2600,.1],[2700,.55],[2800,1],[3500,1],[4050,.55]];
    for(const [time,expected]of cases)assert.ok(Math.abs(audio.duckingFactor(config,time,cues)-expected)<1e-8,`${time}`);
});

test('manual timing edits invalidate derived speech ranges without copying editorial state',()=>{
    const audio=controller(),cues=[{start_ms:1000,end_ms:2000}];
    assert.equal(audio.duckingFactor(config,1800,cues),.1);
    cues[0].end_ms=1200;audio.invalidateTiming();assert.equal(audio.duckingFactor(config,1800,cues),1);
});

test('changing the gap setting rebuilds speech beds while gain settings take effect immediately',()=>{
    const audio=controller(),cues=[{start_ms:1000,end_ms:1100},{start_ms:2000,end_ms:2100}];
    assert.equal(audio.duckingFactor({...config,speech_gap_hold_ms:1000},1600,cues),.1);
    assert.equal(audio.duckingFactor(config,1600,cues),1);
    assert.ok(Math.abs(audio.duckingFactor({...config,duck_db:6},1050,cues)-(1+Math.pow(10,-6/20))/2)<1e-8);
});

test('manual automation preserves interpolation, duplicate points and reverse seeking',()=>{
    const audio=controller(),points=[{time_ms:1000,gain_db:-20},{time_ms:0,gain_db:0},{time_ms:2000,gain_db:0}];
    for(const [time,expected]of [[500,.55],[1000,.1],[1500,.55],[3000,1],[0,1],[500,.55]])assert.ok(Math.abs(audio.manualAutomationFactor(points,time)-expected)<1e-8);
    const duplicates=[{time_ms:1000,gain_db:-20},{time_ms:1000,gain_db:0}];
    assert.equal(audio.manualAutomationFactor(duplicates,1000),.1);
    assert.equal(audio.manualAutomationFactor(duplicates,1001),1);
});


test('all music channels follow playback speed without seeking or pausing them',()=>{
 const audio=controller();audio.channels=[{currentTime:3,paused:false},{currentTime:1,paused:false}];
 audio.setPlaybackRate(2);assert.ok(audio.channels.every(channel=>channel.playbackRate===2&&!channel.paused));
 assert.deepEqual(audio.channels.map(channel=>channel.currentTime),[3,1]);
 audio.setPlaybackRate(.5);assert.ok(audio.channels.every(channel=>channel.playbackRate===.5));
});
