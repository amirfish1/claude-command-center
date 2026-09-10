#!/usr/bin/env python3
"""Write docs/intro-video/transcript.md from slides.py.

Pass --from-build to fill scene timestamps from video_build/slide_NN.mp4
durations after a render.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent


def _load_slides():
    spec = importlib.util.spec_from_file_location("ccc_intro_slides", HERE / "slides.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SLIDES


def _ffprobe_duration(path: Path) -> float | None:
    if not path.is_file():
        return None
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _fmt(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def write_transcript(*, from_build: bool) -> Path:
    slides = _load_slides()
    starts = [None] * len(slides)
    ends = [None] * len(slides)
    if from_build:
        t = 0.0
        for i in range(len(slides)):
            dur = _ffprobe_duration(HERE / "video_build" / f"slide_{i:02d}.mp4")
            if dur is None:
                starts[i] = ends[i] = None
                continue
            starts[i] = t
            t += dur
            ends[i] = t

    lines = [
        "# CCC introduction — bound coverage transcript",
        "",
        "This file is the narration spoken in `docs/intro-video/out/ccc-introduction.mp4`.",
        "Each scene is one slide. Visual sources are the real CCC UI on seeded demo",
        "fixtures (`docs/demo/`, `scripts/story-capture/`) or brand/HTML cards.",
        "Timestamps are filled from the rendered per-slide durations after compose.",
        "",
        "## Scene map",
        "",
        "| # | scene | start | end | visual source | required moment |",
        "|---|---|---|---|---|---|",
    ]
    for i, slide in enumerate(slides):
        vis = slide.get("visual") or slide.get("html") or ""
        moment = slide.get("moment") or ""
        scene = slide.get("scene") or f"s{i:02d}"
        start = _fmt(starts[i]) if starts[i] is not None else "TBD"
        end = _fmt(ends[i]) if ends[i] is not None else "TBD"
        lines.append(f"| {i:02d} | {scene} | {start} | {end} | {vis} | {moment} |")

    lines.extend(
        [
            "",
            "Required visual moments (criterion 3) map to these sources:",
            "",
            "- fleet/list with multiple engines: `V-01-fleet-scan.mp4`",
            "- needs-you / attention: `V-03-attention.mp4`",
            "- spawn or steer from the dashboard: `V-14-issue-to-session.mp4`",
            "- Flow canvas or project tree: `V-07-flow-canvas.mp4`",
            "- group chat or queues/workers: `V-16-group-chat.mp4`, `V-17-queues.mp4`, `V-18-queue-workers.mp4`",
            "- search: `V-09-search.mp4`",
            "- mobile or Simple Mode: `V-15-mobile.mp4`, `docs/simple-mode/assets/01-home.png`",
            "",
            "## Narration",
            "",
        ]
    )
    for i, slide in enumerate(slides):
        scene = slide.get("scene") or f"s{i:02d}"
        lines.append(f"### {i:02d} {scene}")
        lines.append("")
        lines.append(slide["narration"])
        lines.append("")

    out = HERE / "transcript.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-build", action="store_true")
    args = parser.parse_args()
    path = write_transcript(from_build=args.from_build)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
