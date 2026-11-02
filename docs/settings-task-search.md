# Find a configuration task

Settings search keeps literal setting matches and adds curated task suggestions.
Examples include “stop sounds”, “use Codex for this queue”, “make text bigger”,
“worker defaults”, “undo changes” and “export preferences”. Suggestions are
navigation, never commands: typing or choosing a result does not save, stage an
engine selection, grant permission or launch work.

Task suggestions cover notifications, launch policy, queue exceptions, engine
connections, credentials, voice, appearance, contextual reader/list/repository
controls, history, profiles, browser resets, health and optional capabilities.
Matching uses a fixed vocabulary, not a model or an interpretation of arbitrary
instructions. Ambiguous queries can show several destinations. Unrecognized
queries retain ordinary literal search.

Browser summaries read their existing storage adapters. Host summaries use the
nonmigrating policy GET. Queue suggestions read the existing queue routing report;
choose the exact queue to open its canonical editor. Requested engine names in a
query never become draft values. Queue summaries describe saved configuration,
not authentication, quota or future dispatch readiness. Other results explicitly
leave current values to their canonical editors.

Requests are debounced and superseded responses are discarded. Failed reads say
unavailable instead of assuming defaults or an empty queue list. Summaries become
stale after thirty seconds; Refresh current state re-reads them. Each editor
retains its own validation and save workflow. Back to search restores the query
and originating result. Reader settings require an existing selected conversation.
No search query is persisted or transmitted to a separate search service.

This does not widen the profile, reset or undo allowlists. Browser reset remains
separate from operational configuration recovery. Scope guidance describes search
controls as navigation rather than configuration writers.
