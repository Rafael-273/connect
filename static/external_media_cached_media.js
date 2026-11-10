/* Poll lightweight cache status; never download the source media to draw UI. */
(function(root){
    async function waitFor(url,{attempts=60,signal,isCurrent=()=>true,fetcher=fetch,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
        for(let attempt=0;attempt<attempts;attempt++){
            if(signal?.aborted||!isCurrent())throw new DOMException('Operação cancelada.','AbortError');
            const response=await fetcher(url,{credentials:'same-origin',signal});
            if(!response.ok)throw new Error(`Não foi possível preparar a mídia (${response.status}).`);
            const data=await response.json();
            if(data.status==='ERROR')throw new Error('Não foi possível preparar esta prévia. Tente novamente mais tarde.');
            if(response.status!==202)return data;
            await sleep(Math.max(1000,Math.min(10000,Number(data.retry_after||5)*1000)));
        }
        throw new Error('A prévia ainda está sendo preparada. Tente novamente em instantes.');
    }
    root.ConnectCachedMedia={waitFor};
    if(typeof module!=='undefined')module.exports=root.ConnectCachedMedia;
})(typeof window!=='undefined'?window:globalThis);
