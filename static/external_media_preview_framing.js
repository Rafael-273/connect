/* Keep automatic framing on untouched takes when another take uses raw media. */
(function (root) {
    function decisionAt(decisions, sourceId, masterMs, includeDisabled = false) {
        const all = (decisions || []).filter(item => item.type === 'reframe'
            && String(item.source_id || item.related_source_id) === String(sourceId));
        const scoped = all.filter(item => {
            const meta = item.metadata || {};
            return meta.segment_start_ms !== undefined
                && Number(meta.segment_start_ms) <= masterMs && masterMs < Number(meta.segment_end_ms);
        });
        return [...scoped].reverse().find(item => item.enabled === false || Boolean(item.metadata?.manual_transform))
            || scoped.at(-1)
            || all.find(item => (includeDisabled || item.enabled !== false) && item.metadata?.segment_start_ms === undefined)
            || null;
    }

    function playback(timeline, clip, asset) {
        const raw = {url: asset?.url || '', inMs: Number(clip?.source_in_ms || 0), outMs: Number(clip?.source_out_ms || 0), automatic: false};
        if (!clip || !timeline.framing_master_url || !Number.isFinite(clip.master_in_ms) || !Number.isFinite(clip.master_out_ms)) return raw;
        const decision = decisionAt(timeline.decisions, clip.asset_id, clip.master_in_ms, true);
        if (!decision || decision.enabled === false || decision.metadata?.manual_transform
            || !decision.plan_reference?.plan || clip.color_adjustment) return raw;
        return {url: timeline.framing_master_url, inMs: clip.master_in_ms, outMs: clip.master_out_ms, automatic: true};
    }

    const api = {decisionAt, playback};
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.ConnectPreviewFraming = api;
})(typeof window === 'undefined' ? globalThis : window);
