// App-wide state, as one mutable object rather than exported `let`s: an ES
// module export is a read-only binding for importers, so `import {STATE}` would
// give every view a value it could read but never update. Views do `S.STATE`.
//
// Only genuinely cross-view state belongs here. Anything one view owns (the
// model list's selection, the download poller's cursor) stays in that module.
export const S = {
  STATE: null,        // latest /api/state payload
  SCHEMA: null,       // llama.cpp knob schema
};

/** Model rows from the last poll, or [] before the first one. */
export const models = () => (S.STATE && S.STATE.models) || [];

/** config.json as the backend reports it. */
export const config = () => (S.STATE && S.STATE.config) || {};
