# Objects API — `/api/objects/*`

Server-side persistence + mutation surface for the Flow **objects** day-view
tracker (GOAL-3 + GOAL-4 of `objects-day-view-tracker.md`). Mirrors the browser
`localStorage` organization to one durable JSON file so the state survives a
cache clear, crosses machines, and is readable by server-side automation.

## Storage

- **File:** `~/.claude/command-center/objects.json` (override with the
  `CCC_OBJECTS_FILE` env var, or relocate the whole state dir with
  `CCC_STATE_DIR`).
- **Writer:** `objects_store.py` — atomic temp-file + `os.replace`; an `fcntl`
  lock serialises cross-process writers. Reads are cached by `(mtime, size)`.
- **Degradation:** a missing or corrupt/wrong-shape file reads as empty state
  (`{objects: [], parents: {}, order: {}, drafts: []}`) and never throws. The
  next write overwrites the garbage.

### Schema

```json
{
  "objects": [
    {
      "id":         "<stable id>",
      "title":      "Ship the billing fix",
      "created_at": "2026-06-23T12:00:00Z",
      "updated_at": "2026-06-23T12:34:00Z",
      "status":     "in progress",
      "objective":  "land the patch"
    }
  ],
  "parents": { "<sessionNodeId>": "object:<objectId>" },
  "order":   { "<nodeId>": 3 },
  "drafts": [
    {
      "id":             "<stable id>",
      "title":          "Draft the release notes",
      "repo_path":      "/path/to/repo",
      "parent_node_id": "object:<objectId>",
      "prompt":         "write the notes",
      "created_at":     "2026-06-23T12:00:00Z",
      "updated_at":     "2026-06-23T12:34:00Z"
    }
  ]
}
```

`drafts` are **lightweight not-yet-started tasks** (Flow's "draft-session"
nodes). `repo_path` may be `""` for a pure reminder; `prompt` is optional.
`parent_node_id` links a draft to its object (`"object:<id>"`). The client owns
draft creation/editing and pushes them via `import` — the server only
**merges** drafts in (additive upsert by id) and **deletes** one by id; there are
no create/update draft endpoints.

`status` and `objective` are **optional** and owned by a different client
session. This server never sets them but stores and returns them losslessly —
a title-only edit never clobbers a status someone else wrote.

The object **node id** used as a value in `parents` is `"object:" + id` (Flow's
node-id convention). `assign` builds that prefix for you; callers pass the bare
`object_id`.

## Security

Every POST passes the existing `_check_same_origin` CSRF guard (loopback /
allow-listed origins only). No new CSRF surface, no bind-host change, no path
validation touched. GET is read-only under the same loopback trust as every
other CCC GET.

## Endpoints

### `GET /api/objects`
Returns the full state.

```json
{ "objects": [ ... ], "parents": { ... }, "order": { ... }, "drafts": [ ... ] }
```

### `POST /api/objects/create`
Create (or upsert by `id`) an object.

- Request: `{ "title": "Ship billing", "id"?: "obj-1", "status"?: "...", "objective"?: "..." }`
  - `title` required (non-empty string). If `id` is given and already exists,
    this is an idempotent upsert (patches title + any supplied optional fields)
    rather than a duplicate.
- Response: `{ "ok": true, "object": { id, title, created_at, updated_at, status?, objective? } }`
- Errors: `400` if `title` missing/blank.

### `POST /api/objects/update`
Patch an existing object. Only fields present in the body are changed.

- Request: `{ "id": "obj-1", "title"?: "...", "status"?: "...", "objective"?: "..." }`
- Response: `{ "ok": true, "object": { ... } }`
- Errors: `400` if `id` missing; `404` if no object has that id.

### `POST /api/objects/delete`
Remove an object plus every parent link pointing at it and its own order rank.

- Request: `{ "id": "obj-1" }`
- Response: `{ "ok": true, "removed": true|false }` (`false` if id was absent)
- Errors: `400` if `id` missing.

### `POST /api/objects/assign`
Parent a session under an object.

- Request: `{ "session_node_id": "session:abc", "object_id": "obj-1" }`
- Response: `{ "ok": true, "objects": [...], "parents": { "session:abc": "object:obj-1", ... }, "order": {...} }`
- Errors: `400` if either id missing.

### `POST /api/objects/unassign`
Remove a session's parent link (no-op if absent).

- Request: `{ "session_node_id": "session:abc" }`
- Response: full state, `parents` no longer containing the key.
- Errors: `400` if `session_node_id` missing.

### `POST /api/objects/import`
Sync the browser's existing localStorage organization up to the server.

- Request: `{ "objects": [...], "parents": {...}, "order": {...}, "drafts": [...] }` (all optional)
- Response: the merged full state.

**Merge, not replace (deliberate):** the import is additive so a browser load
can never silently wipe state another surface created — which is the whole point
of moving off per-browser localStorage.

- `objects`: **upsert by `id`** — incoming fields win per-field for an existing
  id (keeping fields the incoming object omitted), new ids are appended, and no
  server-side object is ever deleted by an import.
- `parents`: incoming links **overwrite the same key** (a session has one
  parent); keys absent from the import are left untouched.
- `order`: incoming ranks **overwrite the same key**; others untouched.
- `drafts`: **upsert by `id`** (same policy as objects) — incoming fields win
  per-field for an existing id, new ids are appended, and no server-side draft is
  ever deleted by an import.

Re-importing the same browser is idempotent; a second browser's import augments
rather than clobbers. To intentionally drop an object, call `delete`; to drop a
draft, call `draft-delete`.

### `POST /api/objects/draft-delete`
Remove a draft-session by id (so the client can propagate a deleted task).

- Request: `{ "id": "draft-1" }`
- Response: `{ "ok": true, "removed": true|false }` (`false` if id was absent)
- Errors: `400` if `id` missing.

Drafts are created and edited client-side and synced up through `import`
(additive upsert by id). There are deliberately **no** create/update draft
endpoints — the server only needs import-merge plus `draft-delete`.

## Errors (all endpoints)

- `400` — malformed JSON, non-object body, or a missing required field
  (message in `{"error": "..."}`).
- `403` — cross-origin POST rejected by the same-origin guard.
- `404` — `update` against an unknown id, or an unknown `/api/objects/*` path.
- `500` — unexpected store failure (e.g. disk write error); body carries the
  message and the server keeps running.
