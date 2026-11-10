/* Derived view helpers. Editorial state continues to live in the timeline. */
(function(root) {
    'use strict';
    function reconcileChildren(parent, children) {
        let cursor = parent.firstChild;
        for (const child of children) {
            if (cursor === child) cursor = cursor.nextSibling;
            else parent.insertBefore(child, cursor);
        }
        while (cursor) { const next = cursor.nextSibling; parent.removeChild(cursor); cursor = next; }
    }

    class IntervalIndex {
        constructor(items, start = item => item.start_ms, end = item => item.end_ms) {
            this.entries = items.map((item, order) => ({item, order, start:Number(start(item)), end:Number(end(item))}))
                .filter(entry => Number.isFinite(entry.start) && Number.isFinite(entry.end))
                .sort((a,b) => a.start-b.start || a.order-b.order);
            let maximum = -Infinity;
            this.ends = this.entries.map(entry => maximum = Math.max(maximum, entry.end));
        }
        at(time) {
            let low=0, high=this.entries.length;
            while(low<high) { const middle=(low+high)>>>1; if(this.entries[middle].start<=time)low=middle+1;else high=middle; }
            const active=[];
            for(let index=low-1;index>=0&&this.ends[index]>time;index--) {
                const entry=this.entries[index];if(entry.end>time)active.push(entry);
            }
            // Preserve compositor/language precedence from the canonical array.
            return active.sort((a,b)=>a.order-b.order).map(entry=>entry.item);
        }
    }
    function preserveFocus(container, identity, render) {
        const document=container.ownerDocument, active=document.activeElement;
        const selector='input,select,textarea,button';
        const fields=[...container.querySelectorAll(selector)];
        const index=container.dataset.viewIdentity===identity?fields.indexOf(active):-1;
        const selection=index>=0&&typeof active.selectionStart==='number'
            ? [active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
        const signature=node=>JSON.stringify([node.tagName,node.getAttribute('type'),node.getAttribute('name'),node.dataset]);
        const before=index>=0?signature(active):null;
        const result=render();container.dataset.viewIdentity=identity;
        const target=index>=0?container.querySelectorAll(selector)[index]:null;
        if(target&&signature(target)===before&&!target.disabled){
            target.focus({preventScroll:true});
            if(selection)target.setSelectionRange(...selection);
        }
        return result;
    }
    function waveformWindow(clip, viewport, overscan=64) {
        if(clip.width<=0||clip.height<=0||clip.right<=viewport.left||clip.left>=viewport.right||clip.bottom<=viewport.top||clip.top>=viewport.bottom)return null;
        const offset=Math.max(0,viewport.left-clip.left-overscan);
        const end=Math.min(clip.width,viewport.right-clip.left+overscan);
        return {offset,width:Math.max(0,end-offset),from:offset/clip.width,to:end/clip.width};
    }
    root.ConnectEditorView = {reconcileChildren, IntervalIndex, preserveFocus, waveformWindow};
    if(typeof module!=='undefined')module.exports=root.ConnectEditorView;
})(typeof window!=='undefined'?window:globalThis);
