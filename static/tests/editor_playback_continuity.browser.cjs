// Standalone real-decoder check; no Django account, database or project required.
// FILADELFIA_PLAYWRIGHT_MODULE=/path/to/playwright-core node static/tests/editor_playback_continuity.browser.cjs
const {chromium}=require(process.env.FILADELFIA_PLAYWRIGHT_MODULE||'playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'../..'),template=fs.readFileSync(path.join(root,'templates/member/external_media/preview.html'),'utf8');
const slice=(start,end)=>template.slice(template.indexOf(start),template.indexOf(end,template.indexOf(start)));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.FILADELFIA_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--autoplay-policy=no-user-gesture-required']});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('about:blank');
  const encoded=await page.evaluate(async()=>{
   const canvas=document.createElement('canvas');canvas.width=160;canvas.height=90;
   const drawing=canvas.getContext('2d'),stream=canvas.captureStream(30),audio=new AudioContext(),destination=audio.createMediaStreamDestination(),oscillator=audio.createOscillator();
   oscillator.connect(destination);oscillator.start();await audio.resume();destination.stream.getAudioTracks().forEach(track=>stream.addTrack(track));
   const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8,opus'}),chunks=[];recorder.ondataavailable=e=>chunks.push(e.data);
   let frame=0;const interval=setInterval(()=>{drawing.fillStyle=`hsl(${frame++*5} 70% 40%)`;drawing.fillRect(0,0,160,90)},1000/30);
   const ended=new Promise(resolve=>recorder.onstop=resolve);recorder.start();await new Promise(resolve=>setTimeout(resolve,3500));recorder.stop();await ended;clearInterval(interval);oscillator.stop();stream.getTracks().forEach(t=>t.stop());await audio.close();
   const bytes=new Uint8Array(await new Blob(chunks).arrayBuffer());let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);
  });
  const bytes=Buffer.from(encoded,'base64');
  const wav=Buffer.alloc(44+8000*6*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);for(let i=0;i<48000;i++)wav.writeInt16LE(Math.round(1000*Math.sin(i*2*Math.PI*240/8000)),44+i*2);
  let releaseSlow;const slowGate=new Promise(resolve=>releaseSlow=resolve);
  await page.route('http://playback.test/**',async route=>{
   const url=route.request().url();if(url.endsWith('/'))return route.fulfill({contentType:'text/html',body:'<div><video id="main" playsinline></video></div><audio id="music"></audio>'});
   if(url.endsWith('/music.wav'))return route.fulfill({contentType:'audio/wav',body:wav});
   if(url.includes('slow'))await slowGate;
   const match=/bytes=(\d+)-(\d*)/.exec(route.request().headers().range||'');
   if(match){const start=Number(match[1]),end=match[2]?Math.min(bytes.length-1,Number(match[2])):bytes.length-1;return route.fulfill({status:206,headers:{'Content-Type':'video/webm','Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${bytes.length}`},body:bytes.subarray(start,end+1)})}
   return route.fulfill({contentType:'video/webm',body:bytes});
  });
  await page.goto('http://playback.test/');
  for(const file of ['external_media_preview_source.js','external_media_preview_audio.js'])await page.addScriptTag({content:fs.readFileSync(path.join(root,'static',file),'utf8')});
  await page.addScriptTag({content:`
   let video=document.getElementById('main'),activeClip=null,globalMs=0,loading=false,draggingTimelineNeedle=false;
   const clip=(id,source,start,end,local)=>({asset_id:source,id,timeline_in_ms:start,timeline_out_ms:end,source_in_ms:local,source_out_ms:local+end-start});
   let timeline={sequence:{duration_ms:2400,fps:30},clips:[clip('a','a',0,600,0),clip('a-cut','a',600,1200,1000),clip('b','b',1200,1800,0),clip('c','c',1800,2400,0)]};
   const sourceFor=clip=>clip?'/'+clip.asset_id+'.webm':'',clipAt=ms=>timeline.clips.find(c=>ms>=c.timeline_in_ms&&ms<c.timeline_out_ms)||timeline.clips.at(-1),nextClipAfter=c=>timeline.clips[timeline.clips.indexOf(c)+1],useReviewMaster=()=>false,frameDurationMs=()=>1000/30;
   const renderPosition=()=>{},syncMusicPosition=()=>{};
   const musicController=new ConnectPreviewAudio(document.getElementById('music'));
   musicController.configure({volume:.1,tracks:[{url:'/music.wav',start_ms:0,end_ms:10000}]});
   const soundtrackClock=new ConnectPreviewSoundtrackClock(musicController,{sync:()=>musicController.sync({},globalMs,{force:true})});soundtrackClock.setVideo(video);
   window.transitions=[];window.presentedFrames=[];window.musicPauses=0;const pauseMusic=musicController.pause.bind(musicController);musicController.pause=()=>{window.musicPauses++;pauseMusic()};
   function requestVideoPlay(){video.play().catch(e=>{if(e.name!=='AbortError')throw e})}
   const bound=new WeakSet();function bind(v){if(bound.has(v))return;bound.add(v);v.addEventListener('play',()=>{if(v===video)startPlaybackClock(v)});v.addEventListener('pause',()=>{if(v===video)stopPlaybackClock()});v.addEventListener('ended',()=>{if(v!==video)return;const next=nextClipAfter(activeClip);if(next)seek(next.timeline_in_ms,true)});for(const name of ['waiting','seeking'])v.addEventListener(name,()=>{if(v===video)cancelPlaybackBoundary()})}
   const sourcePlayer=new ConnectPreviewSource(video,{onLoading:value=>{loading=value;soundtrackClock.setLoading(value)},onVideoChange:(next,old,options)=>{stopPlaybackClock();video=next;soundtrackClock.setVideo(next,options);bind(next)},play:requestVideoPlay,onTransition:detail=>transitions.push({...detail,at:performance.now(),musicPlaying:musicController.isPlaying,musicPauses})});
   ${slice('    function warmNextClip(', '    function renderPosition(')}
   ${slice('    let playbackClock=', '    const sourceEventBound=')}
   function reportPreviewFrame(playerVideo,time,displayTime){if(playerVideo===video&&!loading)presentedFrames.push({decoder:playerVideo.id||'secondary',source:playerVideo.dataset.source,time,at:displayTime??performance.now()})};bind(video);
   window.qa={start:()=>seek(0,true),status:()=>({position:globalMs,loading,source:video.dataset.source,time:video.currentTime,paused:video.paused,transitions,musicPauses}),slow:()=>{sourcePlayer.pause();stopPlaybackClock();timeline={sequence:{duration_ms:1800,fps:30},clips:[clip('a','a',0,600,0),clip('slow','slow',600,1200,0),clip('c','c',1200,1800,0)]};seek(0,true)}};
  `});
  await page.evaluate(()=>qa.start());
  await page.waitForFunction(()=>qa.status().position>=2399,null,{timeout:12000}).catch(async e=>{throw new Error(e.message+JSON.stringify({errors,state:await page.evaluate(()=>qa.status()),videos:await page.locator('video').evaluateAll(nodes=>nodes.map(v=>({src:v.src,time:v.currentTime,ready:v.readyState,seeking:v.seeking,paused:v.paused,error:v.error?.message,buffer:Array.from({length:v.buffered.length},(_,i)=>[v.buffered.start(i),v.buffered.end(i)])})))}))});
  let result=await page.evaluate(()=>qa.status());
  assert.equal(result.transitions.filter(t=>t.phase==='cold').length,1,JSON.stringify(result));
  assert.equal(result.transitions.filter(t=>t.phase==='swap').length,3,JSON.stringify(result));
  assert.ok(result.transitions.every(t=>t.phase!=='swap'||Math.abs(t.actualTime-t.time)<=1/30));
  const frameGaps=await page.evaluate(()=>presentedFrames.slice(1).flatMap((frame,index)=>{const previous=presentedFrames[index];return frame.decoder!==previous.decoder||frame.source!==previous.source?[Math.max(0,frame.at-previous.at-1000/30)]:[]}));console.log('Prepared handoff excess callback gaps (ms; excludes the already revealed still):',JSON.stringify(frameGaps.map(ms=>Math.round(ms))));
  const swaps=result.transitions.filter(t=>t.phase==='swap');assert.ok(swaps.every(t=>t.musicPlaying),JSON.stringify(result));assert.equal(new Set(swaps.map(t=>t.musicPauses)).size,1,'healthy handoffs must not pause music');
  console.log('PASS continuous music through prepared swaps');
  console.log('PASS real decoders: same-file cut + two resource changes use three prepared swaps');
  await page.evaluate(()=>qa.slow());
  await page.waitForFunction(()=>qa.status().transitions.some(t=>t.phase==='waiting'&&t.source.includes('slow')),null,{timeout:12000});
  const held=await page.evaluate(()=>qa.status());assert.equal(held.loading,true);assert.equal(held.paused,true);
  releaseSlow();await page.waitForFunction(()=>qa.status().position>=1799,null,{timeout:12000});
  result=await page.evaluate(()=>qa.status());
  assert.equal(result.transitions.filter(t=>t.phase==='cold'&&t.source.includes('slow')).length,0,JSON.stringify(result));
  assert.ok(result.transitions.some(t=>t.phase==='swap'&&t.source.includes('slow')));
  assert.deepEqual(errors,[]);console.log('PASS delayed B stays reserved in A → B → C and resumes through the prepared decoder');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
