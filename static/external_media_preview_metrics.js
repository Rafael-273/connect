/* Opt-in, bounded local diagnostics. No network requests or per-frame history. */
(function(root){
    class PreviewMetrics {
        constructor({getMedia=()=>[],limit=300,now=()=>performance.now(),requestFrame=callback=>requestAnimationFrame(callback),cancelFrame=id=>cancelAnimationFrame(id)}={}) {
            this.getMedia=getMedia;this.limit=limit;this.now=now;this.requestFrame=requestFrame;this.cancelFrame=cancelFrame;
            this.samples=[];this.pending=null;this.sequence=0;this.lastResources=-Infinity;
        }
        record(type,detail={}) {
            this.samples.push({type,at:this.now(),...detail});
            if(this.samples.length>this.limit)this.samples.splice(0,this.samples.length-this.limit);
        }
        finishSeek(outcome,signal) {
            const pending=this.pending;if(!pending)return;
            this.pending=null;this.cancelFrame(pending.frame);
            this.record('seek',{id:pending.id,source:pending.source,target:pending.time,elapsedMs:this.now()-pending.started,outcome,signal});
        }
        beginSeek(source,time,player) {
            this.finishSeek('cancelled','new-request');
            const pending={id:++this.sequence,source,time,player,started:this.now(),frame:null};
            this.pending=pending;
            const check=()=>{
                if(this.pending!==pending)return;
                if(player.failure){this.finishSeek('error','source-error');return}
                if(this.now()-pending.started>=10000){this.finishSeek('timeout','diagnostic-deadline');return}
                const video=player.video;
                if(this.matches(pending,video,video.currentTime)){
                    // A paused decoder may not submit another native callback.
                    // Keep this weaker observation distinct from a presented frame.
                    pending.frame=this.requestFrame(()=>{
                        if(this.pending!==pending)return;
                        if(this.matches(pending,player.video,player.video.currentTime))this.finishSeek('ready','ready-after-raf');
                        else check();
                    });
                }else pending.frame=this.requestFrame(check);
            };
            pending.frame=this.requestFrame(check);
        }
        matches(pending,video,time) {
            return video.dataset.source===pending.source&&!video.seeking&&!pending.player.pendingFrameCheck&&!pending.player.waitingForPreload
                &&video.readyState>=2&&Math.abs(time-pending.time)<=pending.player.frameTolerance;
        }
        frame(video,time) {
            if(this.pending&&video===this.pending.player.video&&this.matches(this.pending,video,time))this.finishSeek('presented','video-frame');
            this.resources();
        }
        resources(force=false) {
            if(!force&&this.now()-this.lastResources<1000)return;
            this.lastResources=this.now();
            const media=[...new Set(this.getMedia())];
            this.record('resources',{
                elements:media.length,videos:media.filter(node=>node.tagName==='VIDEO').length,
                withSource:media.filter(node=>Boolean(node.getAttribute('src'))).length,
                playing:media.filter(node=>!node.paused).length,
                hiddenVideosWithSource:media.filter(node=>node.tagName==='VIDEO'&&(node.hidden||node.style?.opacity==='0')&&node.getAttribute('src')).length
            });
        }
        export() {
            this.resources(true);
            return {schema:'connect.preview_metrics.v1',samples:this.samples.map(sample=>({...sample}))};
        }
    }
    root.ConnectPreviewMetrics=PreviewMetrics;
    if(typeof module!=='undefined')module.exports=PreviewMetrics;
})(typeof window!=='undefined'?window:globalThis);
