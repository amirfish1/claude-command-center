# Plan to fleet: drop a document, get a working queue

Write a plan in Markdown. CCC turns it into Watchtower tickets, with the
dependency order the plan implies, and then optionally puts worker sessions on
that queue. The pipeline is `document -> tickets -> supervised execution`, and
you approve at every step: nothing is filed until you click File, and nothing
spawns until you click Drain.

## Prerequisite

This is an optional integration. CCC never hard-depends on Watchtower, so the
affordance simply does not appear unless `wt import` is installed:

```bash
wt import --help
```

If that fails, the Import doc button stays hidden and `POST
/api/queue/import-doc` refuses cleanly with `available: false`. Nothing else
in the dashboard changes. The probe runs once per page load, and the server
caches its own `wt import --help` check once per process, so if you install
Watchtower while CCC is running, restart CCC to pick it up.

## Using it from the dashboard

1. Open the queue panel and its overflow menu. Click **Import doc**.
2. Give it a path to a Markdown or text file and a queue name. The queue does
   not have to exist yet.
3. Click **Preview tickets**. This is a dry run. It reads the whole document
   in one reasoning pass and shows you what it would file: type, title, the
   `source: file.md#L8-L12` anchor it came from, and `after: <title>` when the
   plan implies one task must follow another.
4. Anything the queue already has comes back marked **exists** rather than
   new, so re-importing an edited plan does not duplicate work.
5. Click **File N tickets**. Only the new ones are filed.
6. After filing, an optional fleet step appears. Choose a worker count (1 to
   8) and click **Drain with workers**. If the queue has a working repo
   configured, that many repo-scoped worker sessions spawn on it. If it does
   not, CCC turns on auto-drain instead and lets the reconciler staff the
   queue. Nothing spawns before this click.

Preview and apply each cost one reasoning call, with a 180 second timeout.

## Using it from a script

```bash
curl -s -X POST http://127.0.0.1:8090/api/queue/import-doc \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8090' \
  -d '{"path":"/abs/path/to/plan.md","queue":"MYQUEUE","apply":false}'
```

Fields: `path` (required), `queue` (required, 1 to 64 characters of letters,
numbers, `_` or `-`), `apply` (default false), `type` (`bug` or `feature`, to
override the inferred type on every new ticket).

Set `"apply": true` to file. Adjust the port to match your CCC instance. The
`Origin` header is required because every CCC POST is same-origin checked.

A dry run comes back like this:

```json
{
  "ok": true, "available": true, "applied": false, "queue": "MYQUEUE",
  "tickets": [
    {"status": "new", "type": "feature",
     "title": "Audit existing content tooling",
     "source_ref": "/abs/path/to/plan.md#L8-L12"},
    {"status": "new", "type": "feature",
     "title": "Build the CLI",
     "source_ref": "/abs/path/to/plan.md#L14-L29",
     "depends_on": "Audit existing content tooling"}
  ],
  "counts": {"candidates": 2, "new": 2, "existing": 0}
}
```

## What makes a document import well

The extractor is a reasoning pass, not a heading splitter, so structure helps
but prose is fine. In practice:

- One outcome per section. A section that describes three separable outcomes
  usually comes back as one ticket with a fat body.
- Say the order out loud. "After the audit lands, build the CLI" produces a
  `depends_on` edge. Implicit ordering often does not.
- Keep constraints next to the task they constrain. They end up in the ticket
  body, which is what the worker actually reads.
- Preview first, always. It is free of consequences and it is the cheapest way
  to find out that your plan is vaguer than you thought.

## Safety posture

- The path is clamped the same way the Files panel clamps: symlinks resolved
  strictly, must be an existing regular file, and the extension must be one of
  `.md`, `.markdown`, `.mdx`, `.txt`, `.text`. Never a script or a binary.
  Repo containment is deliberately not required, because plan documents often
  live outside any repo.
- `wt` is invoked as an argv list, never a shell string, so neither the path
  nor the queue name can inject a shell.
- Preview never writes. Filing writes only new tickets. Spawning is a separate
  explicit click after that.
