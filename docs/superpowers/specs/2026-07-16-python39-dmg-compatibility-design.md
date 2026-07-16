# Python 3.9 DMG Compatibility Design

**Date:** 2026-07-16  
**Target release:** v5.8.1

## Problem

The v5.8.0 macOS app now reports installation failures correctly, but a normal
Mac with Apple's `/usr/bin/python3` 3.9.6 cannot complete first launch. The
installer rejects every Python below 3.10 before cloning CCC.

The version floor is narrower than the implementation requires. `server.py`
compiles under Python 3.9.6 and executes successfully when annotations are
postponed. Its current import failure comes from evaluating annotations such as
`float | None` at module load. The adjacent annotated modules already use
`from __future__ import annotations` and import under Python 3.9.

## Approaches considered

### 1. Support Python 3.9 in v5.8.1 — selected

Postpone annotations in `server.py`, lower the declared and installer version
floor to Python 3.9, and prove the server imports and boots with Python 3.9.6.

- Small, auditable patch.
- Immediately fixes the reported Mac without increasing the DMG size.
- Preserves the stdlib-only runtime architecture.
- Still requires some Python installation on Macs where none exists.

### 2. Bundle a universal Python runtime

Ship Python inside the app and make the installed checkout use it.

- Provides a genuinely zero-prerequisite DMG.
- Substantially increases artifact size, signing surface, update complexity,
  vulnerability maintenance, and Intel/Apple Silicon release testing.
- Appropriate as a separate release project, not an emergency patch.

### 3. Install Python for the user

Drive Homebrew, `xcode-select`, or python.org installation from CCC.

- Keeps CCC's own artifact smaller.
- Depends on third-party installers, network state, privileges, and UI flows.
- Homebrew is not guaranteed to exist, while Command Line Tools may still
  provide Python 3.9.6 and therefore does not solve the reported failure.

## Selected design

### Runtime compatibility

Add `from __future__ import annotations` immediately after the module docstring
in `server.py`. No runtime behavior or API contract changes. Keep the rest of
the code unmodified unless a Python 3.9 test exposes another concrete
incompatibility.

Set `requires-python = ">=3.9"` in `pyproject.toml`. Update the public README to
state Python 3.9+.

### Installer behavior

Change `scripts/install.sh` to accept Python 3.9 or newer and report the same
clear version error only for 3.8 and older. The native app continues to use its
augmented `PATH`, so Homebrew/python.org Python remains preferred when present;
otherwise Apple's executable can satisfy the installer.

Do not add automatic downloads, privileged installation, or package-manager
mutations in v5.8.1.

### Tests

Use test-driven development:

1. Add an installer-function regression proving 3.9 is accepted and 3.8 is
   rejected without depending on the developer's default Python.
2. Add a Python 3.9 compatibility test that imports `server.py` with a real
   Python 3.9 interpreter when available. CI must add Python 3.9 to its matrix
   so this gate is authoritative rather than optional there.
3. Run the complete repository suite on the normal supported interpreter and
   the focused import/bootstrap checks on Python 3.9.
4. Build the signed/notarized v5.8.1 DMG and execute the packaged app under an
   isolated home with `PATH` constrained so `/usr/bin/python3` 3.9.6 is the
   interpreter used. Probe `/api/version`, the dashboard, attribution, clone
   integrity, and shutdown.

### Release and verification

Cut v5.8.1 as a patch release because this is a compatibility bug fix. Publish
both `ccc-v5.8.1.dmg` and stable `ccc.dmg`, update the Sparkle appcast and
Homebrew formula, then verify:

- Gatekeeper acceptance, Developer ID signature, notarization, and staple.
- Stable and versioned assets are byte-identical with the published digest.
- The public stable URL installs successfully using Python 3.9.6.
- GitHub CI, install-smoke, and Pages are green for the final `main` SHA.

## Scope boundary

This release does not bundle Python and does not remove Python as a runtime
dependency. A zero-prerequisite bundled runtime remains a separate product and
release decision. The public demo's Kanban removal is also separate and follows
after the installer patch ships.
