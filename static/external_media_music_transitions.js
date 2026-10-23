/* Pure geometry shared by timeline controls and playback; mirrored in Python. */
(function(root){
    'use strict';
    function windowFor(left,right,sourceDurationMs,incomingDurationMs=null){
        const spec=right.audio_transition;
        if(!spec||spec.type!=='CONSTANT_POWER'||String(spec.from_track_id)!==String(left.track_id??left.id))return null;
        const cut=Number(right.start_ms);
        if(Number(left.end_ms)!==cut)return null;
        const requested=Math.max(0,Math.min(10000,Number(spec.duration_ms)||0));
        let head=Math.min(Math.max(0,Number(right.source_in_ms)||0),Math.floor((cut-Number(left.start_ms))/2));
        if(incomingDurationMs!==null&&(Number(right.source_in_ms)||0)>incomingDurationMs)head=0;
        const tail=Math.min(Math.max(0,sourceDurationMs-(Number(left.source_in_ms)||0)-(cut-Number(left.start_ms))),Math.floor((Number(right.end_ms)-cut)/2));
        let before=Math.min(Math.floor(requested/2),head),after=Math.min(requested-before,tail);
        before=Math.min(requested-after,head);
        return before+after>0?{start_ms:cut-before,end_ms:cut+after,duration_ms:before+after}:null;
    }
    root.ConnectMusicTransitions={windowFor};
    if(typeof module!=='undefined')module.exports=root.ConnectMusicTransitions;
})(typeof window!=='undefined'?window:globalThis);
