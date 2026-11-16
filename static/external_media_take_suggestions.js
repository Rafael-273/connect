/* Shared state guard: late responses cannot replace manual or approved ranges. */
(function (root) {
    class TakeSuggestionState {
        constructor(reviewed = false) { this.dirty = reviewed; this.loaded = false; this.latest = []; }
        edit() { this.dirty = true; }
        receive(analysis) {
            this.latest = analysis.suggestions || [];
            if (analysis.trim_reviewed) this.dirty = true;
            if (this.dirty || this.loaded || !this.latest.length) return false;
            this.loaded = true;
            return true;
        }
    }
    root.TakeSuggestionState = TakeSuggestionState;
    if (typeof module !== 'undefined') module.exports = TakeSuggestionState;
})(typeof window !== 'undefined' ? window : globalThis);
