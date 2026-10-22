/* Frame arithmetic and magnetic alignment. Times remain canonical milliseconds. */
(function(root){
    'use strict';
    const fpsValue=fps=>Math.max(1,Number(fps)||30);
    const frameAt=(ms,fps)=>Math.round(Number(ms)*fpsValue(fps)/1000);
    const frameMs=(frame,fps)=>Math.round(Number(frame)*1000/fpsValue(fps));
    const quantize=(ms,fps)=>frameMs(frameAt(ms,fps),fps);
    function timecode(ms,fps){
        const rate=Math.round(fpsValue(fps)),frame=Math.max(0,frameAt(ms,fps));
        return [Math.floor(frame/(rate*3600)),Math.floor(frame/(rate*60))%60,Math.floor(frame/rate)%60,frame%rate].map(value=>String(value).padStart(2,'0')).join(':');
    }
    function targets(values,fps){return [...new Set(values.filter(Number.isFinite).map(value=>quantize(value,fps)))].sort((a,b)=>a-b)}
    function nearest(sorted,value){
        let low=0,high=sorted.length;
        while(low<high){const mid=(low+high)>>>1;if(sorted[mid]<value)low=mid+1;else high=mid}
        return [sorted[low-1],sorted[low]].filter(Number.isFinite);
    }
    function align({start,end,mode,points=[],tolerance=0,fps=30,duration,minimum=1,enabled=true}){
        // Moving preserves duration, including legacy intervals off the frame grid.
        const length=end-start;
        if(mode==='end')end=quantize(end,fps);else{start=quantize(start,fps);if(mode==='move')end=start+length}
        let guide=null,best=Infinity,shift=0;
        if(enabled){
            const edges=mode==='start'?[start]:mode==='end'?[end]:[start,end];
            for(const edge of edges)for(const target of nearest(points,edge)){
                const delta=target-edge,distance=Math.abs(delta);
                const nextStart=mode==='end'?start:start+delta,nextEnd=mode==='start'?end:end+delta;
                if(distance<=tolerance&&distance<best&&nextStart>=0&&nextEnd<=duration&&nextEnd-nextStart>=minimum){shift=delta;guide=target;best=distance;}
            }
        }
        if(guide!==null){if(mode!=='end')start+=shift;if(mode!=='start')end+=shift}
        if(mode==='move'){start=Math.max(0,Math.min(duration-length,start));end=start+length}
        else if(mode==='start')start=Math.max(0,Math.min(end-minimum,start));
        else end=Math.max(start+minimum,Math.min(duration,end));
        return {start,end,guide};
    }
    root.ConnectEditorPrecision={frameAt,frameMs,quantize,timecode,targets,align};
    if(typeof module!=='undefined')module.exports=root.ConnectEditorPrecision;
})(typeof window!=='undefined'?window:globalThis);
