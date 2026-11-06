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

Workers, Jobs, Queues and the Decisions tabs are hidden: they need data the
demo does not invent yet. Voice is hidden too.
