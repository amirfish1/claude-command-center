# Sandboxed app marketplace (MVP)

Use **Applications → Browse apps** to install an example, or install a package
from a local folder, ZIP archive or an HTTPS Git repository:

```sh
ccc app install ./my-app
ccc app install ./my-app.zip
ccc app install https://example.com/owner/app.git
ccc app install ./my-app --yes
ccc app list
ccc app remove my-app
```

A ZIP archive or Git repository must have `app.json` and `index.html` at its root. CCC makes a
shallow clone; it does not run build commands, dependency installers, package
scripts, Git hooks, or submodules. HTTPS URLs with embedded credentials are
refused. SSH Git URLs are not supported in this first version.

## Threat model

Historically, a folder dropped into `~/.claude/command-center/apps/<id>/` was
served directly at `/view/<id>` on the dashboard origin. Its JavaScript could
read the dashboard DOM and storage and call **every** `/api/*` endpoint. In
particular, spawning an agent can execute code with the user's OS authority.
Installing an unreviewed folder that way therefore trusted it as much as CCC.

Marketplace code is untrusted, including its manifest, HTML, JavaScript, SVG,
filenames and index description. We protect the dashboard origin, unrestricted
APIs, local files outside the package, and data outside approved permissions.
The dashboard, browser implementation, OS account and manually created local
apps remain trusted. This is not a sandbox against other programs running as
the same OS user, nor a CPU/memory quota for hostile JavaScript.

A different loopback port alone would **not** work: CCC's existing origin check
accepts loopback on any port for sibling dashboards. This MVP changes no origin
allowlist, listener binding, CORS policy, or `/api/repo/switch` validation.

## Isolation boundary

For installed packages, `/view/<id>` serves a small trusted host. It loads the
app in an iframe with **only** `sandbox="allow-scripts"`. There is no
`allow-same-origin`, forms, popups, downloads or top-navigation permission. The
app has an opaque origin (`null`), cannot read parent DOM, cookies or dashboard
storage, and cannot register a service worker for the dashboard.

Package bytes are served at `/marketplace-content/<id>/<file>`. Every response,
including HTML and SVG, also carries a **CSP sandbox allow-scripts header**.
Opening the raw URL in a tab or via the old `/app-static/` route therefore does
not bypass isolation. CSP denies connections, frames, workers, objects and form
submissions; script/style/image/font sources are confined to the package's
content prefix. The sandbox header cannot be relaxed by a manifest or HTML meta
tag. Asset paths reject traversal and symlinks, and the legacy route cannot
address another package through `..` or the removal archive.

Direct fetches are blocked by CSP. Independently, cross-origin reads have no
CORS grant, and direct POSTs with `Origin: null` fail CCC's existing origin
check. Do not add `null` to trusted origins. Neither this bridge nor its app
content routes add CORS headers. Existing proxy-secret and phone PIN gates
still run before marketplace routes.

An app can display any data it is allowed to read. Do not treat CSP as a promise
that granted data can never leave the machine: self-navigation and user actions
can disclose it. Only grant data access to an app you trust with that data.

## Permissions and bridge

A marketplace manifest is deliberately small:

```json
{
  "id": "my-app",
  "label": "My app",
  "description": "Shows the live session snapshot.",
  "icon": "📋",
  "permissions": ["sessions.read"]
}
```

Unknown manifest fields, duplicate JSON keys and non-string icons are rejected.
IDs must match `[a-z0-9_-]{1,40}` and cannot shadow shipped apps. A manifest
cannot specify a navigation `url`. Missing `permissions` means no permissions.
Unknown or duplicate permissions are refused.

| Permission / bridge method | Returned data |
| --- | --- |
| `sessions.read` | Live session IDs, titles, engines and states; no transcripts or repository paths |
| `queues.read` | Queue item IDs, titles and statuses; no bodies or repository paths |

These values can still contain sensitive user-authored titles. The backend
uses the existing cached queue reader and live census, with no archive request
or per-item subprocess. There are no arbitrary query arguments or filters.
`spawn`, writes, filesystem access and arbitrary `/api/*` forwarding are
intentionally unsupported. Unknown methods fail closed.

Apps send messages to their parent:

```js
parent.postMessage({
  type: 'ccc.app.call', requestId: 'snapshot-1', method: 'sessions.read'
}, '*');
window.addEventListener('message', event => {
  if (event.source !== parent || event.data?.type !== 'ccc.app.result') return;
  // Match requestId; inspect ok, result or error. Render strings as text.
});
```

The host accepts messages only from **its exact iframe WindowProxy** with
`event.origin === 'null'`, validates the envelope, and checks its granted method
list. It never trusts an app-supplied ID, URL or HTTP verb. It limits calls to
one in flight and at most one per second. Replies use `'*'` because an opaque
origin cannot be named, but are sent only to that frame.

The host sends its own app ID and installation generation to
`POST /api/app-marketplace/bridge`. The server independently reloads the grant,
checks the generation and requested permission, and dispatches through a closed
table of two read callbacks. The generation invalidates open hosts after
remove/reinstall; it is not a bearer credential for bypassing the origin check.
A denied request does not call the underlying reader.

## Consent and package handling

Both CLI and Browse apps use the same preparation and installation functions.
Preparation copies, extracts or clones into private staging, rejects symlinks and special
files, validates the manifest, and limits packages to 500 entries / 10 MiB.
ZIP members are checked before extraction: absolute/traversing paths, backslashes,
duplicate paths, symlinks, special files and encrypted entries are rejected. Both
the compressed archive and expanded bytes are limited to 10 MiB.
The CLI shows the actual manifest's requested permissions and asks once; `--yes`
is explicit consent for scripts. The UI prepares first, then displays those
actual permissions in a confirmation dialog. Index metadata never grants access.

Confirmation installs the staged bytes, checked against the reviewed SHA-256
package digest. Previews expire after 15 minutes. Existing IDs are never
overwritten. Remove and reinstall to update, reviewing permissions again.
Git cloning has a 60-second timeout, but download size and temporary disk use
are not quota-limited before checkout validation. The checkout may resolve a mutable Git branch; the reviewed staged bytes are
fixed, but there is no publisher signature or curated-code guarantee.

State is under `~/.claude/command-center/app-marketplace/`: per-app grants are
written atomically with mode 0600, staging directories use mode 0700, and CLI /
server installs serialize using a filesystem lock. An isolation marker ships
inside the installed folder. Missing/corrupt grants fail closed; a marker or
grant tombstone keeps the app sandboxed. Removing an app revokes its grant and
moves its folder to `apps/.removed/`, which is not HTTP-addressable. Abandoned
preview directories are not automatically collected in this MVP.

Manually authored folders without marketplace markers/grants, built-ins and
existing user-configured local dashboards retain their existing trusted behavior.
**Do not manually copy third-party packages into the trusted apps directory.**
Use the installer to establish the isolation record.

## Catalog

The default `apps-index.json` ships three examples:

- **Effects kit:** the existing `static/fx-demo.html` playground packaged with
  its scripts, stylesheet and icon. No API permissions. Sound preferences are
  held in memory because sandboxed apps have no localStorage.
- **Session summary:** a small bridge client requesting `sessions.read`.
- **Queue summary:** a read-only queue snapshot requesting `queues.read`, adapted
  from the existing queue view and session bridge example.

Set `CCC_APPS_INDEX_URL` to an HTTPS JSON index to use another catalog. The JSON
shape is `{ "apps": [{ "id", "name", "description", "permissions", "source" }] }`.
Remote catalog sources must be HTTPS Git URLs; they cannot name local folders.
The built-in catalog may reference package directories under `examples/apps/`.
Index reads have a 10-second timeout and 256 KiB / 100-entry bounds.

Catalog availability or claims do not imply code review, signatures or publisher
verification. Package updates, dependency builds, network permissions, write
permissions, publisher signatures and automatic staging cleanup are future work.

For package authoring, schemas, offline validation and the PR submission flow,
see [Building and submitting CCC apps](building-ccc-apps.md).

## Verification

```sh
python3 -m pytest tests/test_app_marketplace.py
```

The tests cover ID/path validation, symlinks, consent, staged-byte tampering,
permission denial, revocation, local CLI install, legacy compatibility and
`Origin: null` rejection on the real HTTP handler. The host JavaScript is
executed with Node to verify source/origin/permission checks. When the optional
repository Puppeteer harness is installed, a disposable loopback test server
also verifies the actual browser sandbox, Browse apps consent, working example
assets, direct SVG navigation and mobile layout. No managed server is restarted
and the smoke suite is not invoked.
