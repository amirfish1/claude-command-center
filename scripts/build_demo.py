#!/usr/bin/env python3
"""Build the "try it in ten seconds" browser demo as plain static files.

The demo is the real UI: every tracked file under static/ is copied unchanged
except for one rewrite that turns root-absolute "/static/..." strings into
page-relative ones, so the bundle works from any base path (a GitHub Pages
project path included). demo/ holds the only demo-specific code: an in-browser
fake backend and an invented data set, loaded ahead of everything else.

    python3 scripts/build_demo.py [--out out/try-demo]
    python3 -m http.server 8878 --directory out/try-demo

Only git-tracked files are copied, so gitignored personal plugins can never
reach the bundle.
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO_SRC = ROOT / "demo"
TEXT_SUFFIXES = {".html", ".js", ".css", ".webmanifest"}
# The ribbon promises nothing leaves the browser, so third-party font requests
# go too; the app falls back to its system font stacks.
GOOGLE_FONTS = re.compile(r'<link[^>]+fonts\.(?:googleapis|gstatic)\.com[^>]*>\n?')
ABSOLUTE_STATIC = re.compile(r"""(["'`(])/static/""")
# Files the page never loads in the demo: agent-facing helpers and the
# old static fixtures' service worker have no role in a backend-free bundle.
SKIP_NAMES = {"sw.js"}
DEMO_HEAD = """<meta charset="utf-8">
<!-- try-demo: invented data and an in-browser fake backend. Nothing here talks to a server. -->
<script src="demo/dataset.js"></script>
<script src="demo/panels.js"></script>
<script src="demo/backend.js"></script>
<link rel="stylesheet" href="demo/demo.css">
<script src="demo/tour.js" defer></script>
"""


def tracked_static_files():
    out = subprocess.check_output(["git", "ls-files", "-z", "static"], cwd=ROOT)
    return [p for p in out.decode().split("\0") if p]


def relativize(text):
    # Repo avatars are <img> tags, which no fetch shim sees. A blank image keeps
    # the initials showing without a 404 per repo.
    text = text.replace('<img src="/api/repo/avatar?repo_path=', '<img src="demo/no-avatar.svg?repo_path=')
    text = text.replace("url:'/decision-inbox.html'", "url:'static/decision-inbox.html'")
    return ABSOLUTE_STATIC.sub(lambda m: m.group(1) + "static/", text)


# Pages the app embeds in an iframe. They sit in static/ next to their scripts, so
# "/static/x" becomes "x", and a shim sends their fetches to the parent's fake backend.
FRAME_PAGES = {"decision-inbox.html"}


def frame_page(text):
    text = ABSOLUTE_STATIC.sub(lambda m: m.group(1), text)
    return text.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<script src="../demo/frame.js"></script>', 1)


def build(out_dir):
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "static").mkdir(parents=True)
    copied = 0
    for rel in tracked_static_files():
        src = ROOT / rel
        if src.is_symlink() or not src.is_file() or src.name in SKIP_NAMES:
            continue
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.name in FRAME_PAGES:
            dest.write_text(frame_page(src.read_text(encoding="utf-8")), encoding="utf-8")
        elif src.suffix in TEXT_SUFFIXES:
            dest.write_text(relativize(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(src, dest)
        copied += 1

    index = (out_dir / "static" / "index.html").read_text(encoding="utf-8")
    if '<meta charset="utf-8">' not in index:
        sys.exit("static/index.html has no charset meta to anchor the demo scripts")
    index = index.replace('<meta charset="utf-8">', DEMO_HEAD, 1)
    index = GOOGLE_FONTS.sub("", index)
    index = index.replace('href="/manifest.webmanifest"', 'href="static/manifest.webmanifest"')
    index = index.replace("<title>Command Center for Claude, Codex, Cursor, and Anti-Gravity</title>",
                          "<title>Try Claude Command Center in your browser (demo, invented data)</title>")
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    (out_dir / "static" / "index.html").unlink()

    shutil.copytree(DEMO_SRC, out_dir / "demo", ignore=shutil.ignore_patterns("README.md"))
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "out" / "try-demo"))
    parser.add_argument("--no-private-check", action="store_true",
                        help="skip the private-terms check (never for a bundle you will publish)")
    args = parser.parse_args()
    out_dir = pathlib.Path(args.out).resolve()
    copied = build(out_dir)
    if not args.no_private_check:
        # The terms list lives outside the repo; see scripts/check_demo_private_terms.py.
        rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "check_demo_private_terms.py"),
                              "--rewrite", str(out_dir)])
        if rc:
            sys.exit(rc)
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print(f"built {out_dir}: {copied} static files, {size / 1e6:.1f} MB uncompressed")


if __name__ == "__main__":
    main()
