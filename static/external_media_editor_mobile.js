/* Touch presentation only: all edits and playback use the desktop operations. */
(function(root){
    'use strict';
    function bind({scroller,isMobile,isEditing,getZoom,setZoom,onTap}){
        const pointers=new Map();let pan=null,pinch=null,frame=null,blocked=false,cancellingShared=false;
        const distance=()=>{const [a,b]=[...pointers.values()];return Math.hypot(a.x-b.x,a.y-b.y)};
        const center=()=>{const [a,b]=[...pointers.values()];return (a.x+b.x)/2-scroller.getBoundingClientRect().left};
        const paint=()=>{
            frame=null;
            if(pinch&&pointers.size===2){const zoom=Math.max(1,Math.min(16,pinch.zoom*distance()/pinch.distance));setZoom(zoom);scroller.scrollLeft=pinch.content*zoom/pinch.zoom-center()}
            else if(pan&&pointers.size===1){const point=[...pointers.values()][0];scroller.scrollLeft=pan.left+pan.x-point.x;scroller.scrollTop=pan.top+pan.y-point.y}
        };
        const schedule=()=>{if(frame===null)frame=requestAnimationFrame(paint)};
        const intercept=event=>{event.preventDefault();event.stopImmediatePropagation()};
        scroller.addEventListener('pointerdown',event=>{
            if(!isMobile()||event.pointerType!=='touch')return;
            pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,target:event.target});
            if(pointers.size===2){
                // Discard a provisional clip/needle drag before zooming. Its
                // normal cancellation path releases the persistence barrier.
                cancellingShared=true;
                for(const [id,point] of pointers)if(id!==event.pointerId)point.target.dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId:id,pointerType:'touch'}));
                cancellingShared=false;
                pinch={zoom:getZoom(),distance:Math.max(1,distance()),content:scroller.scrollLeft+center()};pan=null;blocked=true;intercept(event);scroller.setPointerCapture(event.pointerId);return;
            }
            if(blocked||pointers.size>2){intercept(event);return}
            const direct=event.target.closest('.editor-ruler,.editor-playhead,button:not([data-clip-key]),input');
            if(isEditing()||direct)return;
            pan={x:event.clientX,y:event.clientY,left:scroller.scrollLeft,top:scroller.scrollTop,target:event.target,moved:false};
            intercept(event);scroller.setPointerCapture(event.pointerId);
        },true);
        scroller.addEventListener('pointermove',event=>{
            if(!pointers.has(event.pointerId))return;
            pointers.set(event.pointerId,{...pointers.get(event.pointerId),x:event.clientX,y:event.clientY});
            if(!pan&&!blocked)return;
            if(pan&&Math.hypot(event.clientX-pan.x,event.clientY-pan.y)>7)pan.moved=true;
            intercept(event);schedule();
        },true);
        const finish=event=>{
            if(!pointers.has(event.pointerId))return;
            // Synthetic cancellation above belongs to the shared drag handler.
            if(cancellingShared)return;
            if(pan||blocked){
                intercept(event);if(frame!==null){cancelAnimationFrame(frame);paint()}
                if(pan&&!pan.moved&&event.type==='pointerup')onTap(pan.target,event);
                if(scroller.hasPointerCapture(event.pointerId))scroller.releasePointerCapture(event.pointerId);
            }
            pointers.delete(event.pointerId);pan=null;pinch=null;if(!pointers.size)blocked=false;
        };
        scroller.addEventListener('pointerup',finish,true);scroller.addEventListener('pointercancel',finish,true);
        // preventDefault on pointerdown suppresses compatibility mouse clicks.
        // Keep the browser-generated click from repeating a synthetic tap.
        scroller.addEventListener('click',event=>{if(isMobile()&&event.isTrusted&&event.pointerType==='touch'&&!isEditing()&&!event.target.closest('button:not([data-clip-key])'))event.stopImmediatePropagation()},true);
    }
    root.ConnectEditorMobile={bind};
})(typeof window==='undefined'?globalThis:window);
