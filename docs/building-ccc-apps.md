# Building and submitting CCC apps

CCC apps are static HTML, CSS and JavaScript packages. Start with
[`examples/apps/session-summary`](../examples/apps/session-summary) for session
reads, [`queue-summary`](../examples/apps/queue-summary) for queue reads, or
[`effects-kit`](../examples/apps/effects-kit) for a self-contained playground.
These examples require no build step or external service.

## Package layout

Put these files at the package root, including inside a ZIP:

```text
app.json
index.html
styles.css        (optional)
app.js            (optional)
```

Use relative asset URLs, such as `./app.js`, rather than `/static/app.js`.
Dependencies must be shipped inside the package; CDN scripts, remote fonts,
network requests, workers and nested frames are blocked. Use classic script tags
or inline scripts; module imports and fetch-based loaders are unsupported. Even
`fetch('./data.json')` is blocked, so embed static data in your JavaScript. The installer never
runs build scripts, package managers, Git hooks or submodules.

```json
{
  "id": "my-summary",
  "label": "My summary",
  "description": "A read-only overview of live sessions.",
  "icon": "📋",
  "permissions": ["sessions.read"]
}
```

The manifest is limited to 16 KiB and follows [`app-manifest.schema.json`](../schemas/app-manifest.schema.json).
IDs use 1–40 lowercase letters, digits, `_` or `-`, and cannot shadow built-ins.
`label` and `description` are required, with limits of 80 and 1000 characters.
`icon` is an optional string of up to eight characters. `permissions` defaults
to `[]`. Unknown fields and permissions, duplicate fields and duplicate
permissions are rejected. There are no `url`, `scripts`, `sandbox` or networking
options. Metadata must be one line, without control characters, directional overrides or
invalid Unicode surrogates. Ordinary international text and emoji are supported.
HTML in metadata is displayed as text, never evaluated.

Packages are limited to 500 filesystem entries and 10 MiB, including CCC's tiny
isolation marker. Paths are limited to 16 components and 1024 UTF-8 bytes; ZIP
directories count even when omitted from the archive directory records. Symlinks, special files and path escapes are rejected. ZIPs
also have a 10 MiB compressed-size limit and must contain the manifest directly
at the root, without a wrapping directory. Only stored or deflated ZIP entries are supported; encrypted ZIPs are refused.

## Read permissions

| Permission | Fields returned per row |
| --- | --- |
| `sessions.read` | `session_id`, `title`, `engine`, `state` |
| `queues.read` | `id`, `title`, `status` |

Request only the permissions the app uses. There are no write, spawn, filesystem
or arbitrary API permissions. Titles can still be sensitive: explain what you
show and why. Catalog permissions are a description; consent uses the manifest
in the actual staged package, and the server independently enforces the grant.

Apps run in an opaque-origin iframe. `localStorage`, parent DOM access, cookies
and direct `fetch('/api/...')` are unavailable. Use the narrow message bridge:

```js
const requestId = 'snapshot-' + Date.now();
window.addEventListener('message', event => {
  if (event.source !== parent || event.data?.type !== 'ccc.app.result' ||
      event.data.requestId !== requestId) return;
  const reply = event.data;
  document.querySelector('#status').textContent = reply.ok
    ? `${reply.result.length} sessions`
    : reply.error;
});
parent.postMessage({
  type: 'ccc.app.call', requestId, method: 'sessions.read'
}, '*');
```

The host accepts only its own frame's messages, supplies its own app ID and
installation generation, and ignores app-supplied URLs, verbs or identity.
Use unique request IDs of at most 80 characters and at most one request per
second, with one request in flight. Handle denied/unavailable responses and
empty arrays. Render returned titles with `textContent`; never use `innerHTML`
for user-authored data. Removing/reinstalling revokes old open hosts; reopen the
app after an update.

## Validate and try locally

From a CCC checkout:

```sh
python3 scripts/validate-apps-index.py --package ./my-summary
ccc app install ./my-summary
ccc app list
ccc app remove my-summary
```

The validator is stdlib-only and offline. It checks the catalog, bundled
packages, and optional package by staging in a disposable temporary directory,
without installing or executing anything.
It does not fetch HTTPS sources or certify their safety. A generic JSON Schema
validator catches structural errors; the Python validator also checks duplicate
IDs, URL rules, actual package files and listing/manifest agreement.

To distribute a ZIP, archive the package's contents, not its parent directory:

```sh
(cd my-summary && zip -r ../my-summary.zip app.json index.html styles.css app.js)
ccc app install ./my-summary.zip
```

Omit optional files from the command if you do not use them. For Git installs,
publish a public repository with `app.json` and `index.html` at its root, then use an
HTTPS clone URL without credentials or a fragment. Use the final canonical URL;
Git redirects are disabled. Clones have no working files until the tree passes
the package limits; symlinks and submodules are rejected. Git commands have a
60-second timeout and 16 MiB per-file limit (tree metadata output: 1 MiB). CCC stages the checkout and
shows actual permissions before installation. Existing IDs are never replaced;
remove and reinstall to update. Never manually drop third-party packages into
the trusted local apps directory.

## Submit an app

1. Fork the CCC repository and add a listing to `apps-index.json` in a branch.
2. Use an HTTPS Git source for an independently maintained app. Bundled public
   examples may use `examples/apps/<id>` and include their package in the PR.
3. Run `python3 scripts/validate-apps-index.py` and the targeted tests below.
4. Open a pull request describing the app, requested permissions, license,
   source provenance, and how a reviewer can exercise it. Include desktop and
   narrow-screen screenshots, plus an empty/error-state check for data readers.
5. Reviewers inspect source and permissions before merging the listing. A
   listing is not a publisher signature or a guarantee that a mutable upstream
   Git branch remains unchanged.

A listing follows [`apps-index.schema.json`](../schemas/apps-index.schema.json):

```json
{
  "id": "my-summary",
  "name": "My summary",
  "description": "A read-only overview of live sessions.",
  "permissions": ["sessions.read"],
  "source": "https://example.com/contributor/my-summary.git"
}
```

Add that object to the existing `apps` array. IDs must be unique. For bundled
examples, `id`, `name` and `permissions` must match the package's `id`, `label`
and `permissions`. Avoid private URLs, credentials, client data, personal apps
and fixtures copied from real sessions. Use synthetic demonstration content.

```sh
python3 scripts/validate-apps-index.py
python3 -m pytest tests/test_app_marketplace.py
```

The App marketplace CI workflow validates contributions and requires its browser
checks to run (missing Puppeteer fails that job). It also checks the published
JSON schemas and uploads screenshots from synthetic fixtures.

The browser test uses Puppeteer and a disposable loopback server; it never
restarts a managed dashboard. Test install, consent, open, remove and reinstall,
as well as blocked direct API access. Review the
[marketplace threat model](app-marketplace.md) before changing permissions or
isolation. Approved data can be disclosed by an app; this is not an
exfiltration-proof, CPU or memory sandbox.
