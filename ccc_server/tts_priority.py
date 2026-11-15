"""Opt-in, expiring Linux CPU-weight trial for active neural read-aloud.

Run in an independent user service with --watch MINUTES and --restore as
ExecStopPost. The dashboard only reports playback leases; it cannot enable
a trial. No quotas, process priorities or persistent systemd settings change.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import threading
import time

LEASE_SECONDS = 15
MAX_TRIAL_SECONDS = 60 * 60
TRIAL_WEIGHT = 30
_LOCK = threading.Lock()


def state_dir():
    return Path(os.environ.get("CCC_TTS_PRIORITY_DIR", str(Path.home() / ".ccc" / "tts-priority")))


def _read(file):
    try:
        value = json.loads(file.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(file, value):
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(file)


def status(directory=None, now=None):
    directory = directory or state_dir()
    now = time.time() if now is None else now
    trial = _read(directory / "trial.json")
    return {"enabled": bool(trial.get("until", 0) > now and not (directory / "disabled").exists()),
            "until": trial.get("until", 0), "boosted": bool(trial.get("boosted")),
            "original_weight": trial.get("original_weight"), "weight": trial.get("weight"),
            "error": trial.get("error", "")}


def playback(payload, directory=None, now=None):
    """Renew one tab's bounded lease. Out-of-order requests cannot undo Stop."""
    directory = directory or state_dir()
    now = time.time() if now is None else now
    client = payload.get("client")
    seq = payload.get("seq")
    if (not isinstance(client, str) or not re.fullmatch(r"[a-f0-9]{16,64}", client)
            or type(seq) is not int or not 0 <= seq <= 2**53
            or type(payload.get("playing")) is not bool):
        raise ValueError("invalid playback lease")
    with _LOCK:
        current = status(directory, now)
        if current["enabled"]:
            file = directory / ("lease-" + client + ".json")
            old = _read(file)
            if seq > old.get("seq", -1):
                _write(file, {"seq": seq, "playing": payload["playing"], "at": now})
    return current


def agents_weight_file():
    """Resolve only our own delegated user manager's agents.slice."""
    relative = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
                    if line.startswith("0::"))
    current = Path("/sys/fs/cgroup") / relative.lstrip("/")
    for parent in (current, *current.parents):
        if re.fullmatch(r"user@\d+\.service", parent.name):
            file = parent / "agents.slice" / "cpu.weight"
            if file.is_file():
                return file
    raise RuntimeError("delegated agents.slice CPU weight unavailable")


def _log(directory, event, **details):
    with (directory / "events.jsonl").open("a") as out:
        out.write(json.dumps({"at": time.time(), "event": event, **details}) + "\n")


def restore(directory=None, weight_file=None):
    """Recover after watchdog termination, including ExecStopPost after SIGKILL.

    Never overwrite an operator's newer weight. The saved baseline is written
    BEFORE lowering the weight, so an interrupted write is also recoverable.
    """
    directory = directory or state_dir()
    trial = _read(directory / "trial.json")
    if not trial:
        return
    weight_file = weight_file or agents_weight_file()
    baseline = trial.get("original_weight")
    actual = int(weight_file.read_text().strip())
    if trial.get("restore_pending") and type(baseline) is int and actual == trial.get("weight"):
        weight_file.write_text(str(baseline))
        _log(directory, "restore", weight=baseline)
    trial.update(until=0, boosted=False, restore_pending=False)
    _write(directory / "trial.json", trial)


class Trial:
    def __init__(self, directory, weight_file, minutes, now=None):
        self.directory = directory
        self.weight_file = weight_file
        now = time.time() if now is None else now
        baseline = int(weight_file.read_text().strip())
        self.state = {"until": now + min(MAX_TRIAL_SECONDS, max(1, minutes * 60)),
                      "started": now, "original_weight": baseline,
                      "weight": min(TRIAL_WEIGHT, baseline), "boosted": False, "restore_pending": False}
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Leases from a prior trial never authorize this one.
        for file in directory.glob("lease-*.json"):
            file.unlink(missing_ok=True)
        (directory / "disabled").unlink(missing_ok=True)
        _write(directory / "trial.json", self.state)
        _log(directory, "start", **self.state)

    def tick(self, now=None):
        now = time.time() if now is None else now
        enabled = now < self.state["until"] and not (self.directory / "disabled").exists()
        playing = False
        for file in self.directory.glob("lease-*.json"):
            lease = _read(file)
            age = now - lease.get("at", 0)
            # Keep sequence tombstones until the next trial. A delayed request
            # must not revive playback after its newer Stop lease expires.
            if 0 <= age <= LEASE_SECONDS and lease.get("playing") is True:
                playing = True
        boosted = enabled and playing
        current = int(self.weight_file.read_text().strip())
        expected = self.state["weight"] if self.state["boosted"] else self.state["original_weight"]
        if current != expected:
            self.state["error"] = "CPU weight changed externally; trial stopped"
            enabled = boosted = False
            self.state.update(until=0, boosted=False, restore_pending=False)
            _write(self.directory / "trial.json", self.state)
            _log(self.directory, "external-change", weight=current)
            return False
        if boosted != self.state["boosted"]:
            # Persist recovery information before touching the cgroup.
            self.state["boosted"] = boosted
            self.state["restore_pending"] = True
            _write(self.directory / "trial.json", self.state)
            target = self.state["weight"] if boosted else self.state["original_weight"]
            self.weight_file.write_text(str(target))
            if not boosted:
                self.state["restore_pending"] = False
                _write(self.directory / "trial.json", self.state)
            _log(self.directory, "boost" if boosted else "release", weight=target)
        return enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--watch", type=int, metavar="MINUTES")
    action.add_argument("--restore", action="store_true")
    action.add_argument("--disable", action="store_true")
    action.add_argument("--status", action="store_true")
    args = parser.parse_args()
    directory = state_dir()
    if args.status:
        print(json.dumps(status()))
        return
    if args.disable:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (directory / "disabled").touch()
        return
    # Serialize independently supervised watchers and ExecStopPost recovery.
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "watch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        restore(directory)
        if args.restore:
            return
        trial = Trial(directory, agents_weight_file(), args.watch)
        done = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: done.set())
        try:
            while not done.is_set() and trial.tick():
                done.wait(0.5)
        finally:
            restore(directory)


if __name__ == "__main__":
    main()
