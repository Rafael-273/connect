/* Touch presentation only: all edits and playback use the desktop operations. */
(function(root){
    'use strict';
    function bind({scroller,isMobile,isEditing,getZoom,setZoom,onTap,canHold,onHold}){
        const pointers=new Map();let pan=null,pinch=null,frame=null,blocked=false,cancellingShared=false;
        let holdTimer=null,reorder=null;
        const clearHold=()=>{clearTimeout(holdTimer);holdTimer=null};
        const cancelReorder=()=>{clearHold();if(reorder){reorder.finish(false);reorder=null}};
        const holdDelay=420, movementTolerance=8, pinchTolerance=14;
        const distance=()=>{const [a,b]=[...pointers.values()];return Math.hypot(a.x-b.x,a.y-b.y)};
        const center=()=>{const [a,b]=[...pointers.values()];return {x:(a.x+b.x)/2,y:(a.y+b.y)/2}};
        const paint=()=>{
            frame=null;
            if(pinch&&pointers.size===2){
                const span=distance(),middle=center(),change=Math.abs(span-pinch.distance);
                if(!pinch.active){
                    const drift=Math.hypot(middle.x-pinch.center.x,middle.y-pinch.center.y);
                    if(change<Math.max(pinchTolerance,pinch.distance*.08)||change<=drift*1.5)return;
                    pinch.active=true;
                }
                const zoom=Math.max(1,Math.min(16,pinch.zoom*span/pinch.distance));
                if(zoom!==getZoom())setZoom(zoom);
                scroller.scrollLeft=pinch.content*zoom/pinch.zoom-(middle.x-scroller.getBoundingClientRect().left);
            }else if(pan?.moved){
                const point=pointers.get(pan.pointerId);
                if(pan.axis==='horizontal')scroller.scrollLeft=pan.left+pan.x-point.x;
                if(pan.axis==='vertical')scroller.scrollTop=pan.top+pan.y-point.y;
            }
        };
        const schedule=()=>{if(frame===null)frame=requestAnimationFrame(paint)};
        const intercept=event=>{event.preventDefault();event.stopImmediatePropagation()};
        function takeOverClip(pointerId,target){
            scroller.setPointerCapture(pointerId);
            cancellingShared=true;
            target.dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerId,pointerType:'touch'}));
            cancellingShared=false;
        }
        scroller.addEventListener('pointerdown',event=>{
            if(!isMobile()||event.pointerType!=='touch')return;
            pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,target:event.target});
            // Keep a committed scroll with the finger that started it.
            if(blocked||pointers.size>2||(pan?.moved&&!pan.shared&&!reorder)){
                intercept(event);scroller.setPointerCapture(event.pointerId);return;
            }
            if(pointers.size===2){
                cancelReorder();
                // Discard a provisional clip/needle drag before zooming. Its
                // normal cancellation path releases the persistence barrier.
                for(const [id,point] of pointers)if(id!==event.pointerId)takeOverClip(id,point.target);
                const middle=center(),offset=middle.x-scroller.getBoundingClientRect().left;
                pinch={zoom:getZoom(),distance:Math.max(1,distance()),center:middle,content:scroller.scrollLeft+offset,active:false};
                pan=null;blocked=true;intercept(event);scroller.setPointerCapture(event.pointerId);return;
            }
            const direct=event.target.closest('.editor-ruler,.editor-playhead,button:not([data-clip-key]),input');
            const holdTarget=canHold?.(event.target);
            if(direct)return;
            if(!isEditing()&&event.target.closest('[data-clip-key]')){
                // Keep the existing clip handler available until the swipe
                // commits to scrolling or the long press starts reordering.
                event.preventDefault();
                const initial={x:event.clientX,y:event.clientY,target:event.target};
                pan={...initial,pointerId:event.pointerId,left:scroller.scrollLeft,top:scroller.scrollTop,moved:false,shared:true};
                if(holdTarget)holdTimer=setTimeout(()=>{
                    if(!pan||pan.moved||pointers.size!==1)return;
                    takeOverClip(event.pointerId,initial.target);
                    reorder=onHold(holdTarget,pointers.get(event.pointerId));
                    if(!reorder)pan.shared=false;
                },holdDelay);
                return;
            }
            pan={x:event.clientX,y:event.clientY,pointerId:event.pointerId,left:scroller.scrollLeft,top:scroller.scrollTop,target:event.target,moved:false};
            intercept(event);scroller.setPointerCapture(event.pointerId);
            if(holdTarget){
                holdTimer=setTimeout(()=>{
                    if(!pan||pan.moved||pointers.size!==1)return;
                    reorder=onHold(holdTarget,pointers.get(event.pointerId));
                },holdDelay);
            }
        },true);
        scroller.addEventListener('pointermove',event=>{
            if(!pointers.has(event.pointerId))return;
            pointers.set(event.pointerId,{...pointers.get(event.pointerId),x:event.clientX,y:event.clientY});
            if(!pan&&!blocked)return;
            if(pan&&pan.pointerId!==event.pointerId){intercept(event);return}
            if(reorder){intercept(event);reorder.move(event.clientX,event.clientY);return}
            if(pan&&!pan.moved){
                const dx=Math.abs(event.clientX-pan.x),dy=Math.abs(event.clientY-pan.y);
                if(Math.hypot(dx,dy)>movementTolerance){
                    pan.moved=true;clearHold();
                    pan.axis=dy>=dx?'vertical':'horizontal';
                    if(pan.shared&&pan.axis==='vertical'){
                        takeOverClip(event.pointerId,pan.target);
                        pan.shared=false;
                    }
                }
            }
            if(pan?.shared)return;
            intercept(event);schedule();
        },true);
        const finish=event=>{
            if(!pointers.has(event.pointerId))return;
            // Synthetic cancellation above belongs to the shared drag handler.
            if(cancellingShared||(event.type==='lostpointercapture'&&event.target!==scroller&&scroller.hasPointerCapture(event.pointerId)))return;
            if(pan&&event.pointerId!==pan.pointerId){
                intercept(event);pointers.delete(event.pointerId);
                if(scroller.hasPointerCapture(event.pointerId))scroller.releasePointerCapture(event.pointerId);
                return;
            }
            clearHold();
            if((pan&&!pan.shared)||blocked||reorder){
                intercept(event);if(frame!==null){cancelAnimationFrame(frame);paint()}
                if(reorder){
                    const gesture=reorder;reorder=null;if(pan)pan.moved=true;
                    if(event.type==='pointerup')gesture.move(event.clientX,event.clientY);
                    gesture.finish(event.type==='pointerup');
                }else if(pan&&!pan.moved&&event.type==='pointerup')onTap(pan.target,event);
            }
            if(pan?.shared&&!pan.moved&&!reorder&&!blocked&&event.type==='pointerup'){
                const target=pan.target;queueMicrotask(()=>onTap(target,event));
            }
            pointers.delete(event.pointerId);pan=null;pinch=null;blocked=pointers.size>0;
            if(scroller.hasPointerCapture(event.pointerId))scroller.releasePointerCapture(event.pointerId);
        };
        scroller.addEventListener('pointerup',finish,true);scroller.addEventListener('pointercancel',finish,true);
        scroller.addEventListener('lostpointercapture',finish,true);
        // Safari can also emit native viewport gesture events over the timeline.
        for(const type of ['gesturestart','gesturechange']){
            scroller.addEventListener(type,event=>{if(isMobile())event.preventDefault()},{passive:false});
        }
        scroller.addEventListener('contextmenu',event=>{
            if(isMobile()&&(event.target.closest('[data-clip-key]')||(pan&&canHold?.(pan.target))))event.preventDefault();
        });
        scroller.addEventListener('selectstart',event=>{
            if(isMobile()&&event.target.closest('[data-clip-key]'))event.preventDefault();
        });
        document.addEventListener('keydown',event=>{
            if(event.key==='Escape'&&reorder){cancelReorder();if(pan)pan.moved=true;event.preventDefault()}
        });
        // preventDefault on pointerdown suppresses compatibility mouse clicks.
        // Keep the browser-generated click from repeating a synthetic tap.
        scroller.addEventListener('click',event=>{
            if(isMobile()&&event.isTrusted&&(event.pointerType==='touch'||event.sourceCapabilities?.firesTouchEvents)
                &&!event.target.closest('button:not([data-clip-key]),input'))event.stopImmediatePropagation();
        },true);
    }
    function insertionIndex(midpoints, x) {
        const index = midpoints.findIndex(midpoint => x < midpoint);
        return index < 0 ? midpoints.length : index;
    }

    function edgeScroll(x, left, right, edge = 42) {
        if (x < left + edge) return -Math.min(24, Math.ceil((left + edge - x) / 2));
        if (x > right - edge) return Math.min(24, Math.ceil((x - right + edge) / 2));
        return 0;
    }

    function compactReorder({scroller, lane, items, index, point, onFinish}) {
        const strip = document.createElement('div');
        const viewport = document.createElement('div');
        const track = document.createElement('div');
        const guide = document.createElement('div');
        strip.className = 'editor-compact-reorder';
        strip.setAttribute('aria-hidden', 'true');
        viewport.className = 'editor-compact-reorder__scroll';
        track.className = 'editor-compact-reorder__track';
        guide.className = 'editor-compact-reorder__guide';
        const cards = items.map((item, position) => {
            const card = document.createElement('div');
            const label = document.createElement('span');
            card.className = 'editor-compact-reorder__take';
            card.classList.toggle('is-selected', position === index);
            if (item.background) card.style.backgroundImage = item.background;
            label.textContent = item.label;
            card.append(label);
            return card;
        });
        const ghost = cards[index].cloneNode(true);
        ghost.classList.remove('is-selected');
        ghost.classList.add('editor-compact-reorder__ghost');
        ghost.setAttribute('aria-hidden', 'true');
        track.append(...cards, guide);
        viewport.append(track);strip.append(viewport);
        const bounds = scroller.getBoundingClientRect(), laneBounds = lane.getBoundingClientRect();
        strip.style.left = `${bounds.left}px`;
        strip.style.top = `${Math.max(bounds.top, Math.min(laneBounds.top-7, bounds.bottom-65))}px`;
        strip.style.width = `${scroller.clientWidth}px`;
        document.body.append(strip, ghost);

        viewport.scrollLeft = cards[index].offsetLeft + cards[index].offsetWidth/2 - (point.x-bounds.left);
        const others = cards.filter((_, position) => position !== index);
        const midpoints = others.map(card => card.offsetLeft + card.offsetWidth / 2);
        let x = point.x, y = point.y, changed = false, before = index, frame;
        const step = cards.length > 1 ? cards[1].offsetLeft-cards[0].offsetLeft : cards[0].offsetWidth;
        function paint() {
            ghost.style.left = `${x}px`;ghost.style.top = `${y-8}px`;
            if (changed) {
                viewport.scrollLeft += edgeScroll(x, bounds.left, bounds.right);
                const localX = x - bounds.left + viewport.scrollLeft;
                before = insertionIndex(midpoints, localX);
            }
            cards.forEach((card, position) => {
                let shift = 0;
                if (position === index) shift = before-index;
                else if (before > index && position > index && position <= before) shift = -1;
                else if (before < index && position >= before && position < index) shift = 1;
                card.style.transform = `translateX(${shift*step}px)`;
            });
            guide.style.left = `${cards[index].offsetLeft+(before-index)*step+cards[index].offsetWidth/2}px`;
        }
        function tick() { paint();frame=requestAnimationFrame(tick); }
        tick();
        return {
            move(nextX, nextY) {
                x=nextX;y=nextY;
                changed ||= Math.abs(x-point.x)>5;
            },
            finish(commit) {
                cancelAnimationFrame(frame);
                paint();
                strip.remove();ghost.remove();
                onFinish(commit && changed ? before : null);
            },
        };
    }

    root.ConnectEditorMobile={bind,compactReorder,edgeScroll,insertionIndex};
    if(typeof module!=='undefined')module.exports=root.ConnectEditorMobile;
})(typeof window==='undefined'?globalThis:window);
