(function(root){
    function rectangle(first,last){return {left:Math.min(first.x,last.x),top:Math.min(first.y,last.y),right:Math.max(first.x,last.x),bottom:Math.max(first.y,last.y)}}
    function intersects(left,right){return left.left<right.right&&left.right>right.left&&left.top<right.bottom&&left.bottom>right.top}
    function select(rect,items,base=[]){return new Set([...base,...items.filter(item=>intersects(rect,item.rect)).map(item=>String(item.id))])}
    function bind({canvas,scroller,isEnabled,getSelection,onSelection,onFinish,onBegin,onEnd}){
        let gesture=null,frame=null;
        const point=event=>{const box=canvas.getBoundingClientRect();return {x:event.clientX-box.left,y:event.clientY-box.top}};
        const update=()=>{
            frame=null;if(!gesture)return;
            const event=gesture.latest;
            if(!gesture.moved&&Math.hypot(event.clientX-gesture.startClient.x,event.clientY-gesture.startClient.y)<4)return;
            gesture.moved=true;
            gesture.node.hidden=false;
            const viewport=scroller.getBoundingClientRect();
            if(event.clientX>viewport.right-24)scroller.scrollLeft+=12;
            else if(event.clientX<viewport.left+24)scroller.scrollLeft-=12;
            if(event.clientY>viewport.bottom-20)scroller.scrollTop+=10;
            else if(event.clientY<viewport.top+20)scroller.scrollTop-=10;
            const rect=rectangle(gesture.start,point(event)),box=canvas.getBoundingClientRect();
            Object.assign(gesture.node.style,{left:`${rect.left}px`,top:`${rect.top}px`,width:`${rect.right-rect.left}px`,height:`${rect.bottom-rect.top}px`});
            const items=[...canvas.querySelectorAll('.editor-track--caption [data-clip-key]')].map(node=>{
                const bounds=node.getBoundingClientRect();
                return {id:node._timeline.item.id,rect:{left:bounds.left-box.left,right:bounds.right-box.left,top:bounds.top-box.top,bottom:bounds.bottom-box.top}};
            });
            gesture.selection=select(rect,items,gesture.additive?gesture.original:[]);
            onSelection(gesture.selection);
            if(event.clientX>viewport.right-24||event.clientX<viewport.left+24||event.clientY>viewport.bottom-20||event.clientY<viewport.top+20)frame=requestAnimationFrame(update);
        };
        const finish=(event,cancel=false)=>{
            if(!gesture||event.pointerId!==undefined&&event.pointerId!==gesture.pointerId)return;
            if(frame!==null){cancelAnimationFrame(frame);frame=null}
            if(!cancel){gesture.latest=event;update();if(frame!==null){cancelAnimationFrame(frame);frame=null}}
            const completed=gesture;gesture=null;completed.node.remove();canvas.classList.remove('is-caption-selecting');
            if(canvas.hasPointerCapture?.(completed.pointerId))canvas.releasePointerCapture(completed.pointerId);
            onSelection(cancel?completed.original:completed.selection);
            if(!cancel)onFinish(completed.selection);
            onEnd();
        };
        canvas.addEventListener('pointerdown',event=>{
            if(gesture||event.button!==0||!isEnabled()||!event.target.closest('#editor-tracks')
                ||event.target.closest('button,input,a,.editor-track__label,.editor-playhead'))return;
            event.preventDefault();
            const node=document.createElement('div');node.className='editor-caption-marquee';node.hidden=true;node.setAttribute('aria-hidden','true');canvas.append(node);
            const original=new Set(getSelection());
            gesture={pointerId:event.pointerId,start:point(event),startClient:{x:event.clientX,y:event.clientY},latest:event,node,original,additive:event.shiftKey||event.ctrlKey||event.metaKey,selection:event.shiftKey||event.ctrlKey||event.metaKey?original:new Set(),moved:false};
            onBegin();canvas.classList.add('is-caption-selecting');canvas.setPointerCapture?.(event.pointerId);
        });
        canvas.addEventListener('pointermove',event=>{if(!gesture||event.pointerId!==gesture.pointerId)return;event.preventDefault();gesture.latest=event;if(frame===null)frame=requestAnimationFrame(update)});
        canvas.addEventListener('pointerup',event=>finish(event));
        canvas.addEventListener('pointercancel',event=>finish(event,true));
        canvas.addEventListener('lostpointercapture',event=>{if(gesture)finish(event,true)});
        document.addEventListener('keydown',event=>{if(gesture&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish({pointerId:gesture.pointerId},true)}},true);
    }
    const api={rectangle,intersects,select,bind};
    if(typeof module==='object'&&module.exports)module.exports=api;
    else root.ConnectCaptionSelection=api;
})(typeof window==='undefined'?globalThis:window);
