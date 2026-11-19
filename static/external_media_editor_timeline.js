/* Optimistic editorial operations on the canonical timeline, without a second store. */
(function(root){
    'use strict';
    function removeVideoRange(timeline, {start, end, masterStart, masterEnd, id, masterAt = value => value}) {
        const duration=Number(timeline.sequence.duration_ms),length=end-start;
        if(![start,end,masterStart,masterEnd].every(Number.isFinite)||start<0||end>duration||length<33||masterEnd-masterStart<33)throw new Error('Selecione um trecho de pelo menos um quadro.');
        const mapTime=value=>value<=start?value:value>=end?value-length:start;
        const clips=[];
        for(const clip of timeline.clips){
            const from=Number(clip.timeline_in_ms),to=Number(clip.timeline_out_ms);
            const originalMaster=Number(clip.master_in_ms??masterAt(from,'after'));
            const keep=(left,right)=>{
                if(right<=left)return;
                clips.push({...clip,timeline_in_ms:mapTime(left),timeline_out_ms:mapTime(right),
                    source_in_ms:Number(clip.source_in_ms||0)+left-from,source_out_ms:Number(clip.source_in_ms||0)+right-from,
                    master_in_ms:originalMaster+left-from,master_out_ms:originalMaster+right-from,
                    effects:(clip.effects||[]).map(effect=>({...effect}))});
            };
            if(to<=start||from>=end)keep(from,to);
            else{keep(from,Math.min(to,start));keep(Math.max(from,end),to)}
        }
        const captions=(timeline.captions||[]).flatMap(cue=>{
            // Match the server's subtitle trimming, including legacy short
            // cues before the edit and the minimum frame size after it.
            if(Number(cue.end_ms)<=start)return [cue];
            const from=mapTime(Number(cue.start_ms)),to=mapTime(Number(cue.end_ms));
            return to-from>=33?[{...cue,start_ms:from,end_ms:to}]:[];
        });
        const next={...timeline,sequence:{...timeline.sequence,duration_ms:duration-length},clips,captions,
            decisions:[...(timeline.decisions||[]),{id,type:'remove_segment',source_id:'project-master',source_in_ms:masterStart,source_out_ms:masterEnd,enabled:true,origin:'USER',_pending:true}],
            video_splits_ms:[...new Set((timeline.video_splits_ms||[]).map(mapTime))].filter(point=>point>0&&point<duration-length).sort((a,b)=>a-b),
            markers:(timeline.markers||[]).map(marker=>({...marker,time_ms:mapTime(Number(marker.time_ms))})),
            review_master_url:null,review_master_is_edited:false,review_rendered_layers:[],review_proxy_status:'SOURCE'};
        // Other editorial layers keep their existing absolute timings, just
        // as the server preserves them when creating a cut revision.
        if(timeline.video_tracks)next.video_tracks=timeline.video_tracks.map(track=>track.role==='main'?{...track,clips}:track);
        return {timeline:next,mapTime};
    }
    function moveVideo(timeline, selected, before) {
        const matches = (clip, range) => range && String(clip.asset_id) === range.source_id
            && Number(clip.master_in_ms) === range.start_ms && Number(clip.master_out_ms) === range.end_ms;
        const original = timeline.clips;
        const from = original.findIndex(clip => matches(clip, selected));
        if (from < 0 || matches(original[from], before)) return null;
        const order = original.filter((_, index) => index !== from);
        const to = before ? order.findIndex(clip => matches(clip, before)) : order.length;
        if (to < 0 || to === from) return null;
        order.splice(to, 0, original[from]);
        let cursor = 0;
        const clock = new Map();
        const clips = order.map(clip => {
            const start = Number(clip.timeline_in_ms), end = Number(clip.timeline_out_ms);
            const next = {...clip, timeline_in_ms: cursor, timeline_out_ms: cursor + end - start};
            clock.set(clip, {start, end, offset: cursor - start});
            cursor = next.timeline_out_ms;
            return next;
        });
        const mapping = [];
        for (const clip of original) {
            const span = clock.get(clip), previous = mapping.at(-1);
            if (previous && previous.end === span.start && previous.offset === span.offset) previous.end = span.end;
            else mapping.push({...span});
        }
        const remap = item => mapping.flatMap(({start, end, offset}) => {
            const left = Math.max(start, Number(item.start_ms)), right = Math.min(end, Number(item.end_ms));
            if (right <= left) return [];
            const next = {...item, start_ms: left + offset, end_ms: right + offset};
            if (item.source_in_ms != null) next.source_in_ms = Number(item.source_in_ms) + left - item.start_ms;
            return [next];
        }).map((item, index) => index ? {...item, id: `${item.id}-local-${index}`} : item);
        const mapPoint = point => {
            const span = mapping.find(item => item.start <= point && point < item.end);
            return span ? point + span.offset : point;
        };
        const mapLayer = item => timeline.project?.mode === 'free' || ['USER', 'MANUAL'].includes(item.source)
            ? [item] : remap(item);
        const brolls = (timeline.brolls || []).flatMap(mapLayer), overlays = (timeline.overlays || []).flatMap(mapLayer);
        const byRole = {main: clips, broll: brolls, overlay: overlays};
        const next = {...timeline, clips, brolls, overlays, video_reordered: true,
            captions: (timeline.captions || []).flatMap(remap),
            camera_segments: (timeline.camera_segments || []).flatMap(remap),
            video_splits_ms: (timeline.video_splits_ms || []).map(mapPoint).sort((a,b) => a-b),
            markers: clips.map(clip => ({time_ms: clip.timeline_in_ms, name: clip.block?.name || clip.asset_id})),
            video_tracks: (timeline.video_tracks || []).map(track => ({...track, clips: byRole[track.role] || track.clips})),
            broll_tracks: (timeline.broll_tracks || []).map(track => ({...track, clips: brolls})),
            overlay_tracks: (timeline.overlay_tracks || []).map(track => ({...track, clips: overlays})),
            review_master_url: null, review_master_is_edited: false, review_rendered_layers: [], review_proxy_status: 'SOURCE'};
        return {timeline: next, mapPoint};
    }

    root.ConnectEditorTimeline={removeVideoRange,moveVideo};
    if(typeof module!=='undefined')module.exports=root.ConnectEditorTimeline;
})(typeof window!=='undefined'?window:globalThis);
