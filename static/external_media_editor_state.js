/* One ordered persistence stream for the existing, framework-free editor. */
(function (root) {
    'use strict';
    class EditorPersistence {
        static operationId() {
            if (root.crypto.randomUUID) return root.crypto.randomUUID();
            // getRandomValues also works on HTTP LAN development hosts, where
            // randomUUID is unavailable outside a secure context.
            const bytes = root.crypto.getRandomValues(new Uint8Array(16));
            bytes[6] = (bytes[6] & 15) | 64;
            bytes[8] = (bytes[8] & 63) | 128;
            const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
            return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
        }
        static frameLatest(callback, requestFrame = root.requestAnimationFrame.bind(root), cancelFrame = root.cancelAnimationFrame.bind(root)) {
            let frame = null, latest = null;
            const flush = () => {
                if (frame !== null) cancelFrame(frame);
                frame = null;
                if (latest !== null) { const value = latest; latest = null; callback(value); }
            };
            const update = value => { latest = value; if (frame === null) frame = requestFrame(flush); };
            update.flush = flush;
            return update;
        }
        constructor({version = 0, send, onState = () => {}, onIdle = () => {}, onMetrics = () => {}}) {
            this.version = Number(version);
            this.send = send;
            this.onState = onState;
            this.onIdle = onIdle;
            this.onMetrics = onMetrics;
            this.queue = [];
            this.timers = new Map();
            this.gestures = 0;
            this.running = false;
            this.error = null;
            this.waiters = [];
            this.idleScheduled = false;
        }
        get busy() { return this.running || this.queue.length > 0 || this.timers.size > 0 || this.gestures > 0; }
        changed() {
            this.onState({busy: this.busy, error: this.error, pending: this.queue.length});
            // Let callers consume their response before committing the final snapshot.
            if (!this.busy && !this.idleScheduled) {
                this.idleScheduled = true;
                queueMicrotask(() => {
                    this.idleScheduled = false;
                    if (this.busy) return;
                    this.onIdle();
                    this.waiters.splice(0).forEach(resolve => resolve());
                });
            }
        }
        beginGesture() { this.gestures++; this.changed(); }
        endGesture() { this.gestures = Math.max(0, this.gestures - 1); this.changed(); }
        schedule(key, callback, delay) {
            const previous = this.timers.get(key);
            if (previous) clearTimeout(previous.timer);
            const run = () => {
                this.timers.delete(key);
                // callback must enqueue synchronously, before returning its promise.
                Promise.resolve(callback()).catch(() => {});
                this.changed();
            };
            this.timers.set(key, {timer: setTimeout(run, delay), run});
            this.changed();
        }
        flush() {
            this.flushScheduled();
            if (!this.busy) return Promise.resolve();
            return new Promise(resolve => this.waiters.push(resolve));
        }
        flushScheduled() {
            [...this.timers.values()].forEach(entry => { clearTimeout(entry.timer); entry.run(); });
        }
        enqueue(url, body, coalesceKey = null) {
            return new Promise((resolve, reject) => {
                const last = this.queue.at(-1);
                // Never alter an operation that may already have reached the server,
                // nor coalesce across a cut/delete/history/other ordering barrier.
                if (coalesceKey && last?.key === coalesceKey && last.base === null) {
                    for (const [key, value] of Object.entries(body)) {
                        last.body[key] = value && typeof value === 'object' && !Array.isArray(value)
                            ? {...(last.body[key] || {}), ...value} : value;
                    }
                    const previousResolve = last.resolve;
                    last.resolve = data => { previousResolve(data); resolve(data); };
                } else {
                    this.queue.push({url, body, key:coalesceKey, id: EditorPersistence.operationId(), resolve, reject, base: null, enqueuedAt:Date.now(), attempts:0});
                }
                this.changed();
                this.pump();
            });
        }
        async pump() {
            if (this.running || this.error || !this.queue.length) return;
            this.running = true;
            const operation = this.queue[0];
            if (operation.base === null) operation.base = this.version;
            const startedAt = Date.now();
            operation.attempts++;
            this.changed();
            try {
                const data = await this.send(operation, operation.base);
                this.version = Number(data.editorial_version ?? this.version);
                this.queue.shift();
                operation.resolve(data);
                // Telemetry must not turn a successful commit into a failed operation.
                try { this.onMetrics({operationId:operation.id, queueMs:startedAt-operation.enqueuedAt, requestMs:Date.now()-startedAt, totalMs:Date.now()-operation.enqueuedAt, attempts:operation.attempts}); } catch (_) {}
            } catch (error) {
                // Keep the same operation/id/base: the server may already have committed.
                this.error = error;
            } finally {
                this.running = false;
                this.changed();
                if (!this.error) this.pump();
            }
        }
        retry() { if (this.error?.status === 409) return; this.error = null; this.pump(); }
    }
    root.ConnectEditorPersistence = EditorPersistence;
    if (typeof module !== 'undefined') module.exports = EditorPersistence;
})(typeof window !== 'undefined' ? window : globalThis);
