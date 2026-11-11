# Sample shop trial

This is one small fixture trial, not a detection-rate benchmark. The app has five
seeded problems. The testers did not receive the seed manifest or app source.

## First full run

Four `gpt-6.1-sol` sessions at medium effort ran serially. Two used a desktop
viewport and two used a phone viewport with touch. Their three goals were:

- Save a favorite item for later.
- Check whether the tote is available in your city.
- Decide whether the tote meets your needs using product details, color choices
  and help.

All four tester sessions finished. One completed its goal; three did not. The
report contained six findings. Three of the five seeded problems were correctly
identified. The other two symptoms were noticed but mischaracterized:

| Seed | Observation | Assessment |
| --- | --- | --- |
| Phone favorite button has no effect | No confirmation or saved indicator after tapping | Found |
| Form error does not identify a field | Only “Invalid input.” appeared | Found |
| Help is a dead end | Empty help page, no controls or return link | Found |
| Return instructions are clipped | Tester called the shortened text unfinished and missing details | Partial: visible symptom found, cause inferred incorrectly |
| Colors take 6.5 seconds without feedback | Tester called the button blocked before the delayed result appeared | Partial: delay noticed, severity overstated |

The sixth finding was a harness problem, not an app problem: a City input
inherited its form's “Email” label during fake-data checks. That prevented fake
city entry. It has been fixed and covered by a real browser regression check.
The form error was observed with City blank, not with both fields filled.

No second tester independently confirmed the same issue in this small run.
This trial therefore does not establish semantic duplicate-detection quality.
The unit suite covers normalized duplicate grouping and distinct-tester counts.

## Setup failures and limits

Earlier setup attempts reached no app pages. They exposed missing automatic
approval for the private browser tools and browser executable discovery in an
isolated Codex home. Both were fixed before the full run. A desktop preflight
then completed the favorite goal with no findings.

This Linux host required the explicit `--no-browser-sandbox` option because it
disallows Chromium's sandbox. The option was used only against the disposable
local fixture. Chromium's sandbox stays enabled by default.

After the full run, the harness added a bounded wait action and a direction to
check delayed feedback before calling an action blocked. This improves the
method but does not retroactively improve the first trial's results. Model
judgment still needs review. WatchTower filing was checked through the submission
boundary without filing any real tickets.

## Real replay

`ccc uxlab run --again DIR` was also run against the unchanged fixture at the
same address. It reused all four people, their goals, viewports, budgets and
safety settings. All four sessions finished; one completed its goal.

Four earlier findings were still present. Two were absent on explicit replay:

- The harness City restriction was gone. The tester entered Example City and
  captured the result.
- The claim that colors never appear was withdrawn after a seven-second wait.
  The tester recorded a new **slow** finding instead.

The replay still interpreted truncated product text as unfinished content.
The availability tester also submitted both fake fields and found that valid
input still returns the generic error. Six findings were recorded in this
round. They include separate observations about the form rejecting valid input
and its vague error wording, which the text-key merger leaves separate.

The app was not fixed between rounds. “Absent on explicit replay” is evidence
about the old observation, not proof that someone shipped a fix. In this case
one absence reflects a harness fix and the other a corrected tester judgment.

## Reproduce the checks

```sh
export CCC_ONCALL_STATE_DIR=$(mktemp -d)
nice -n 15 python3 -m pytest -q tests/test_uxlab.py tests/test_perf_budget.py
nice -n 15 node --test tests/uxlab-integration.test.js
```

On a host without a usable Chromium sandbox, use
`UXLAB_TEST_NO_SANDBOX=1 nice -n 15 node --test tests/uxlab-integration.test.js`
for the disposable local integration fixture only.

The integration test checks actual touch events, fake email and city entry,
a safe POST form, dangerous clicks, off-host frames, a popup attempt and an
off-host redirect. It asserts that the disallowed local server receives no
requests. The unit tests use a fake tester and make no model calls.
