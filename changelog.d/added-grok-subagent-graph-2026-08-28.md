**Grok subagents** now nest under their parent in the session family / lane
map. CCC reads `~/.grok/sessions/<parent>/subagents/<child>/meta.json` the
same way it already does for Kimi agents and Claude Task transcripts, and
Grok listing rows carry `parent_session_id`.
