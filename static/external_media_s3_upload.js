/* Direct upload transport shared by takes, extra cameras and B-roll. */
(function (root) {
    'use strict';
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const retryable = status => !status || status === 408 || status === 429 || status >= 500;
    function error(message, status) { const result = new Error(message); result.status = status; return result; }

    async function post(url, body, csrfToken, signal) {
        const response = await fetch(url, {
            method:'POST', credentials:'same-origin', signal,
            headers:{'Content-Type':'application/json', 'X-CSRFToken':csrfToken, 'X-Requested-With':'XMLHttpRequest'},
            body:JSON.stringify(body),
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw error(data?.detail || 'Não foi possível preparar o upload.', response.status);
        if (response.redirected || !data || typeof data !== 'object') throw error('A sessão retornou uma resposta inesperada. Atualize a página e tente novamente.', 401);
        return data;
    }

    function put(url, blob, progress, requests) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest(); requests.add(xhr);
            const done = callback => { requests.delete(xhr); callback(); };
            xhr.open('PUT', url); xhr.timeout = 30 * 60 * 1000;
            xhr.upload.addEventListener('progress', event => { if (event.lengthComputable) progress(event.loaded); });
            xhr.addEventListener('load', () => done(() => {
                if (xhr.status >= 200 && xhr.status < 300) { progress(blob.size); resolve(); return; }
                // Never include signed URLs or the complete S3 response in UI errors.
                let message = `O S3 recusou o envio (HTTP ${xhr.status}).`;
                if (/EntityTooLarge/.test(xhr.responseText || '')) message = 'O arquivo excede o limite de um envio único ao S3. Recarregue a página e tente novamente.';
                if (xhr.status === 403) message = 'O acesso ao S3 expirou ou não tem permissão. Tente novamente.';
                reject(error(message, xhr.status));
            }));
            xhr.addEventListener('error', () => done(() => reject(error('A conexão com o S3 foi interrompida.'))));
            xhr.addEventListener('timeout', () => done(() => reject(error('O envio desta parte demorou demais.'))));
            xhr.addEventListener('abort', () => done(() => reject(error('Envio interrompido.', 499))));
            xhr.send(blob);
        });
    }

    async function upload(file, presign, {csrfToken='', onProgress=()=>{}}={}) {
        const requests = new Set(), controller = new AbortController();
        if (presign.upload_mode !== 'multipart') {
            if (file.size > 5 * 1024 ** 3) throw error('Este arquivo precisa de envio em partes. Recarregue a página para usar o novo upload.', 400);
            if (!presign.upload_url) throw error('Não foi possível obter o destino do upload. Atualize a página.', 400);
            await put(presign.upload_url, file, loaded => onProgress(Math.min(100, Math.floor(loaded / file.size * 100))), requests);
            return;
        }
        const partSize = presign.part_size, count = Math.ceil(file.size / partSize);
        if (!Number.isSafeInteger(partSize) || partSize <= 0 || count > 10000 || !presign.multipart_url) throw error('Configuração de upload em partes inválida.', 400);
        const auth = {allocation:presign.allocation, allocation_sig:presign.allocation_sig};
        const loaded = new Map(); let next = 1, stopped = false;
        const progress = (number, bytes) => {
            loaded.set(number, bytes);
            onProgress(Math.min(100, Math.floor([...loaded.values()].reduce((sum, value) => sum + value, 0) / file.size * 100)));
        };
        async function worker() {
            while (!stopped && next <= count) {
                const number = next++, start = (number - 1) * partSize;
                // Blob.slice retains a bounded view; never read the 8 GB file into JS memory.
                const blob = file.slice(start, Math.min(file.size, start + partSize));
                for (let attempt = 0; attempt < 3; attempt++) {
                    if (stopped) return;
                    try {
                        const signed = await post(presign.multipart_url, {...auth, action:'part', part_number:number}, csrfToken, controller.signal);
                        if (stopped) return;
                        if (!signed.upload_url) throw error('Não foi possível obter o destino desta parte. Atualize a página.', 400);
                        await put(signed.upload_url, blob, bytes => progress(number, bytes), requests);
                        break;
                    } catch (failure) {
                        if (stopped) return;
                        if (attempt === 2 || !(retryable(failure.status) || failure.status === 403)) throw failure;
                        progress(number, 0);
                        await sleep(500 * 2 ** attempt);
                    }
                }
            }
        }
        const workers = Array.from({length:Math.min(3,count)}, () => worker());
        try { await Promise.all(workers); }
        catch (failure) {
            stopped = true;
            controller.abort();
            for (const xhr of [...requests]) xhr.abort();
            await Promise.allSettled(workers);
            // Abort after in-flight PUTs stop; otherwise a late part could arrive
            // after cleanup. Bucket lifecycle rules cover browser/tab termination.
            await post(presign.multipart_url, {...auth, action:'abort'}, csrfToken).catch(() => {});
            throw failure;
        }
    }

    async function complete(presign, csrfToken='') {
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const result = await post(presign.complete_url, {allocation:presign.allocation, allocation_sig:presign.allocation_sig}, csrfToken);
                if (result.id == null) throw error('O servidor não confirmou o registro do arquivo. Atualize a página.', 400);
                return result;
            }
            catch (failure) {
                // Only multipart registration supports lost-response retries.
                if (presign.upload_mode !== 'multipart' || attempt === 2 || !retryable(failure.status)) throw failure;
                await sleep(500 * 2 ** attempt);
            }
        }
    }
    const api = {upload, complete};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.ConnectS3Upload = api;
})(typeof window !== 'undefined' ? window : globalThis);
