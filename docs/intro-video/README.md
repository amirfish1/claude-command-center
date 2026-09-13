# CCC introduction video

Complete public-feature introduction. One master file:

`out/ccc-introduction.mp4`

The bound coverage transcript (narration plus scene map) is `transcript.md`.
A local player is `player.html`.

## What it covers

README public inventory: Features, Also in the box, Decision Inbox, engine
support (eight spawnable plus three read-only), the `ccc` CLI, WatchTower
queues and workers, search, mobile / Simple Mode, and install. Claims stay
inside shipped public-safe behavior. UI footage is the real dashboard on
seeded demo fixtures, not live personal sessions.

## Rebuild

Needs `video-claw`, `ffmpeg`, and an ElevenLabs key (or set `mode: "free"`
in `slides.py` for macOS `say`).

```bash
python3 docs/intro-video/write_transcript.py
cd docs/intro-video
video-claw preview --yes
video-claw render --yes --no-preview
python3 write_transcript.py --from-build
```

Coverage unittest: `python3 -m unittest tests.test_intro_video_coverage`.
