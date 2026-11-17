(() => {
    const root = document.documentElement;
    const standalone = window.matchMedia('(display-mode: standalone)');
    const syncDisplay = () => root.classList.toggle('is-standalone', standalone.matches || navigator.standalone === true);
    syncDisplay();
    standalone.addEventListener?.('change', syncDisplay);

    const viewport = window.visualViewport;
    if (viewport) {
        let frame;
        const syncKeyboard = () => {
            const element = document.activeElement;
            const editable = element instanceof HTMLElement && (
                element.matches('input:not([readonly]):not([disabled]), textarea:not([readonly]):not([disabled]), select') || element.isContentEditable
            );
            const occluded = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
            const inset = editable && viewport.scale === 1 && occluded > 120 ? Math.round(occluded) : 0;
            root.style.setProperty('--app-keyboard-inset', `${inset}px`);
            root.classList.toggle('has-keyboard', inset > 0);
        };
        const schedule = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(syncKeyboard);
        };
        viewport.addEventListener('resize', schedule);
        viewport.addEventListener('scroll', schedule);
        window.addEventListener('resize', schedule);
        window.addEventListener('pageshow', schedule);
        document.addEventListener('focusin', schedule);
        document.addEventListener('focusout', schedule);
        syncKeyboard();
    }

    if ('serviceWorker' in navigator && window.isSecureContext) {
        navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' })
            .then(async () => {
                // Retire only the previous Connect worker, after root registration succeeds.
                const registrations = await navigator.serviceWorker.getRegistrations();
                for (const registration of registrations) {
                    if (registration.scope === new URL('/static/', location.origin).href &&
                        [registration.active, registration.waiting, registration.installing].some(worker =>
                            worker && new URL(worker.scriptURL).pathname === '/static/sw.js')) {
                        await registration.unregister();
                    }
                }
            })
            .catch(error => console.warn('Não foi possível registrar o PWA do Connect:', error));
    }
})();
