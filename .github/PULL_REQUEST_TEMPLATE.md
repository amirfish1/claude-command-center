<!-- Thanks for sending a PR. A few prompts — feel free to strip anything
     that doesn't apply. -->

## What this changes

<!-- One or two sentences. What and why. -->

## How I tested

<!-- What did you run, click, break? If there are no tests yet, manual repro
     is totally fine — just say what you did. -->

- [ ] `python3 -m unittest discover tests` passes
- [ ] Ran the server and exercised the touched surface by hand

## Checklist

- [ ] No new runtime dependencies (stdlib only, per project policy)
- [ ] Subprocess calls use list-form args (no `shell=True`)
- [ ] New endpoints that take paths from the body sandbox them
     (see `/api/open` and `/image-cache/` for the pattern)
- [ ] Docs / README updated if user-facing behavior changed
- [ ] `CHANGELOG.md` entry under `[Unreleased]` (for non-trivial changes)

## Linked issue

Closes #
