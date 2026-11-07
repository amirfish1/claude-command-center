# Try-it-now browser demo

The real CCC interface running on invented data, with no backend. It is a set
of static files, so any static host can serve it, from any path.

```bash
python3 scripts/build_demo.py                        # writes out/try-demo
python3 -m http.server 8878 --directory out/try-demo
node scripts/verify-try-demo.js --out out/try-demo-shots
```

- `dataset.js` holds the invented sessions of Fictional Tea Co.
- `backend.js` answers the UI's `/api/*` calls in the page and keeps state in
  memory, so a tapped decision changes the row, transcript and counts. A reload
  starts over.
- `tour.js` and `demo.css` add the ribbon and hide controls that need a machine.

`scripts/build_demo.py` copies the git-tracked `static/` files unchanged except
for path rewrites, so the demo follows the app with no fixture snapshot to
refresh. When the UI calls a new endpoint, `window.__demoMisses` lists it.

Jobs, Workers and Decisions show invented data (`panels.js` holds the decision
cards, queues, workers and tickets). Decisions is an embedded page; `frame.js`
sends its requests to the parent page's fake backend. Decisions 2 and the Queue
panel are still hidden: Decisions 2 needs a revisioned question feed and Queue
needs a full ticket model.
Voice is hidden too. Settings pages that configure a real machine show a short
install note instead of an error.

## The 60-second path

The ribbon walks a visitor through three things (tap a decision, approve the
blocked agent, type a reply) and then the Install CCC step. Steps reached are
counted in this browser only, under `localStorage["ccc-try-demo-steps"]`, and
mirrored on `window.__tryDemoSteps`. Nothing is sent anywhere. A future,
owner-approved analytics hook can subscribe with
`window.__tryDemoAnalyticsHook = (step, count) => {...}`; none exists today.

## Private-terms check

`build_demo.py` fails if the bundle contains any term from a list kept outside
the repo (`$CCC_PRIVATE_TERMS_FILE`, else `~/.config/ccc-private-terms/demo.txt`).
A line `term => replacement` also rewrites that term in the demo copy of shared
app code. A missing list fails the build; `--no-private-check` skips it for
local experiments only.
