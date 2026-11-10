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
    root.ConnectEditorTimeline={removeVideoRange};
    if(typeof module!=='undefined')module.exports=root.ConnectEditorTimeline;
})(typeof window!=='undefined'?window:globalThis);
