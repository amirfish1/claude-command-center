# Record a local On-call capture pack

These developer tools run the real product against local ntfy and GreenMail
containers. They need Linux, Docker, ffmpeg, Puppeteer, WatchTower, and a signed-in
Codex CLI. A run makes paid model requests. Only Codex authentication is copied
into a temporary home. No production On-call settings or services are used.

From the repository root:

```sh
nice -n 15 python3 scripts/oncall-launch-pack.py \
  --model gpt-6.1-sol --session-model gpt-6.1-terra \
  --output .claude/screenshots/launch-captures/my-run
```

Choose a new output directory for each run. The fixing proof supports Sol;
the CCC session catalogue in this checkout does not list that Sol model, so
this example uses Terra for the session proof.
Use `--resume PATH` with the same model options to retain successful parts of
an interrupted pack and rerun only failed parts. Keep the normal HOME for this
command so the existing proof runners can find Codex authentication and Chrome.
The runners create their own temporary homes, state stores, ports and containers.
They run sequentially and remove their processes and containers on exit.

The pack combines two real runs with visible cuts. The first receives a seeded
bug report, starts a fixing agent automatically, checks its commit against an
unchanged test, and approves a notice to a local example mailbox. The second
asks a scripted question through a real agent and sends a numbered reply from
the real ntfy web app. It verifies that the next transcript user turn contains
exactly `Owner answer: blue`, the product’s prefix followed by the phone answer,
after the baseline. The standard On-call service header is recognized separately;
the complete raw message remains in the receipt. An earlier mention of the answer
does not count as receipt.

The 40-second film labels the second run. Topic navigation sits between
recordings and is labelled as a cut. The film is not one unbroken take. The 12-second loop uses the question
and reply from that session run. Captions show the actual speed multiplier.
Both videos are silent, 1920 by 1080, at 30 frames per second. Source frames
retain their actual capture times and hashes. Puppeteer uses the repository’s
cached headless shell and one page CDP client for viewport settings and captures. The editor rejects missing or
changed frames, capture gaps over two seconds, and footage too short for the
requested duration. It does not turn still captures into a purported recording.

Desktop stills use a 1440 by 900 viewport at 2x density. Phone stills use
390 by 844 at 3x. Session captures crop the real transcript to exclude sidebar
paths. CLI and decoded email stills are labelled text renderings; they are not
terminal recordings or webmail and are excluded from the films.

To re-edit a successful pack without another model request:

```sh
nice -n 15 python3 scripts/oncall-launch-pack.py \
  --render-only .claude/screenshots/launch-captures/my-run
```

The opt-in second edit uses only existing recordings and never starts a proof:

```sh
nice -n 15 python3 scripts/oncall-launch-pack.py \
  --render-only .claude/screenshots/launch-captures/my-run --edit-version v2
```

This writes `oncall-launch-40s-v2.mp4` and `oncall-hero-12s-v2.mp4`, retaining
v1 media and its existing manifest entries. The v2 shot plan in the editor uses
explicit source-time intervals and native-pixel crop rectangles for this capture
layout. A different recording must satisfy those intervals and dimensions;
the editor rejects invalid geometry, short footage, slowing, and beats under
three seconds. It records each crop, native dimensions, uniform scale, output
placement, exact speed, and rounded speed label in the film manifests. Captions
are 56px and recording/cut/separate-run labels are 32px, outside the source UI.
The source is never rearranged: phone input and sent receipts use separate cuts.
`review-v2/` contains 960×540 decoded frames at eight evenly spaced times per
film plus a representative midpoint for every beat. Review these before sharing.

The light-theme session recording contains faded narration above its readable
question card. Inspection of the capture scripts finds theme selection and
native screenshot capture, without a dimming or recoloring step. In
`static/app.css`, the reader's `--kw-text: #c9cdd4` is independent of the light
theme's `--text: #1f2328`, and `.kimi-turn .assistant-text` uses that reader color.
The conversation background follows the light `paper` palette in `static/app.js`.
This is consistent with pale prose on a light background; without inspecting
the original DOM it is evidence of a likely style mismatch, not a computed-style
confirmation. The v2 transcript crop `[24, 200, 1380, 580]` from the
1436×1310 source excludes the lower faded “Got it” prose and preserves the
recorded question card and blue Owner answer. Uniform scaling by 1.35 yields
1863×783 pixels at placement `[28, 170]` in both films, without changing
styles or source pixels.

To rebuild the contact sheet and 1270 by 760 gallery layouts:

```sh
nice -n 15 python3 scripts/oncall-gallery.py \
  --output .claude/screenshots/launch-captures/my-run
```

Inspect the final videos, decoded `review/*.png` frames, and stills before
sharing. `launch-pack.json` records the edit boundaries and proof references.
`share-manifest.json` lists final films and stills only. Raw logs and execution
metadata can contain temporary paths and are not shareable assets. All outputs
are gitignored. These commands do not publish anything.

For Python checks, isolate both On-call and the other CCC state. Resolve Chrome
before changing HOME, since the browser test needs the host cache:

```sh
export CCC_ONCALL_STATE_DIR=$(mktemp -d)
CCC_TEST_HOME=$(mktemp -d)
CCC_TEST_PYTHONPATH=$(python3 -c 'import sys,os;print(os.pathsep.join(sys.path))')
CCC_TEST_CHROME=$(node -e "console.log(require('./puppeteer-browser-config.js').findChromePath())")
HOME="$CCC_TEST_HOME" CCC_STATE_DIR="$CCC_TEST_HOME/state" \
  PYTHONPATH="$CCC_TEST_PYTHONPATH" SNAPSHOT_CHROME="$CCC_TEST_CHROME" \
  nice -n 15 python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py
python3 - "$CCC_TEST_HOME" <<'PYTHON'
import os, shutil, sys
shutil.rmtree(os.environ['CCC_ONCALL_STATE_DIR'])
shutil.rmtree(sys.argv[1])
PYTHON
unset CCC_ONCALL_STATE_DIR
```

Run the suite after the capture finishes, since it includes a browser check.
The independent landing-page overflow check is `nice -n 15 node
scripts/test-hero-overflow.cjs`.
