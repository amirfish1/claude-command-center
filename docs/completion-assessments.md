# Session completion assessments

CCC asks supported sessions to assess the **entire requested task** at the end
of each turn. The assessment is a self-report, not independent verification.
A score must cite evidence; CCC checks the schema and gates, not the truth of
that evidence. It never runs commands or archives a session from these scores.

Session rows use three status words: **Working**, **Next**, and **Close**.
Working takes precedence while a turn is active. Close recommends manual
archive. Next means the session has not met the close criteria; it does not
necessarily mean a human reply is required. Legacy sessions without an
assessment remain unassessed unless explicitly reviewed. Explicit reviews
expire when newer transcript activity is observed.

## Completion gates

All four axes require a numeric score from 0 to 1 and a nonempty evidence
string. Gates are independent; scores are never averaged.

| Axis | Close gate | Evidence expected |
| --- | --- | --- |
| Resolution | At least 0.9 | Original request handled, required work complete |
| Verification | At least 0.8 | Relevant behavior checks passed; limitations stated |
| Delivery | Exactly 1, or explicitly not applicable | Push, deployment, save, or reason delivery is unnecessary |
| Decision dependency | Exactly 0 | No consequential choice or required approval remains |

A blocking caveat or any remaining action prevents Close. Material caveats
must be disclosed. Optional actionable follow-ups should have tickets where
appropriate; moving essential unfinished work into another ticket does not
complete the current task. Malformed or missing assessment fields are
unassessed, never ready. Active work, a newer user event, or an outstanding
question/approval overrides an assessment. Archive is always manual.

## Wire format

Existing DID, INSIGHT, and NEXT_STEP_USER fields retain their meanings and API
shape. The optional COMPLETION field is single-line JSON inside the same footer:

```text
<session-state>
DID: Implemented and delivered the requested behavior.
INSIGHT: The original failure now passes its reproduction check.
NEXT_STEP_USER: None.
COMPLETION: {"version":1,"resolution":{"score":0.95,"evidence":"All requested behavior implemented."},"verification":{"score":0.9,"evidence":"Original reproduction and regression checks passed."},"delivery":{"score":1,"evidence":"Remote ref matches the delivered commit.","applicable":true},"decision_dependency":{"score":0,"evidence":"No consequential choice remains."},"caveats":[],"remaining_actions":[]}
</session-state>
```

Caveats use `{text, blocking}`. Remaining actions use
`{text, confidence, downside, reversible, within_scope}`; confidence and
downside are numbers from 0 to 1, and the last two fields are booleans.
Delivery may use `applicable:false` with evidence explaining why no delivery
is required. CCC ignores any model-supplied readiness verdict and derives
`session_state.completion.ready_to_close`, `valid`, and `reasons` itself.
Invalid assessments retain the human-readable summary and report
`valid:false, ready_to_close:false`.

The transcript renders assessment evidence in a collapsed, escaped details
section. Row tooltips show scores, caveats, and unmet gates without adding
another row to the list.

## Avoiding unnecessary permission requests

The completion instruction asks sessions to evaluate proposed actions during
work. An already authorized, in-scope action with confidence at least 0.9,
downside at most 0.2, and easy reversal should be performed and verified rather
than offered as a ritual “shall I?” question. A score never supplies permission.
Ask for a meaningful missing preference, consequential tradeoff, or genuinely
required approval; state the recommendation and downside.

The instruction travels through the existing CCC system/developer instruction
paths for Claude launches and Codex thread start/resume. Already running Claude
processes need a new launch to receive updated instructions. Other engines can
emit this footer and use the same parser, but CCC does not assume they received
the instruction. No additional model call is made at turn end.
