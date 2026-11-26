"""Exercise the daily gate against a real checkout and an isolated ticket store."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from textwrap import dedent

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gate(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    home = tmp_path / "home"
    log = tmp_path / "log"
    tickets = tmp_path / "tickets.json"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in ("release-daily.sh", "release_backfill.py", "repo-clean-check.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    def git(*args):
        subprocess.run(["git", *args], cwd=checkout, check=True, capture_output=True)

    git("init", "--bare", str(remote))
    git("init", "-b", "main")
    git("add", "scripts")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "commit", "-m", "Set up release gate")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "main")
    for index in range(6):
        (checkout / f"dirty-{index}").touch()

    fake_wt = home / ".local" / "bin" / "wt"
    fake_wt.parent.mkdir(parents=True)
    fake_wt.write_text(dedent("""\
        #!/usr/bin/env python3
        import json
        import os
        from pathlib import Path
        import sys

        args = sys.argv[1:]
        store = Path(os.environ["TEST_TICKETS"])
        tickets = json.loads(store.read_text())

        def flag(name):
            return args[args.index(name) + 1] if name in args else ""

        if args[0] == "ls":
            key = flag("--client-id")
            print(json.dumps([t for t in tickets if t["client_id"] == key]))
        elif args[0] == "add":
            dedupe = flag("--dedupe-key")
            if not any(t["dedupe"] == dedupe for t in tickets):
                seq = len(tickets) + 1
                tickets.append(dict(ref=f"REPO-FIXER-{seq}", seq=seq, status="open",
                                    client_id=flag("--client-id"), dedupe=dedupe,
                                    note=flag("--note")))
                store.write_text(json.dumps(tickets))
        else:
            sys.exit(1)
        """))
    fake_wt.chmod(0o755)
    tickets.write_text("[]")
    env = dict(os.environ, HOME=str(home), TMPDIR=str(tmp_path), TEST_TICKETS=str(tickets),
               CCC_RELEASE_LOG_DIR=str(log), CCC_RELEASE_MODE_FILE=str(home / "mode"),
               CCC_RELEASE_REPO_ROOT=str(checkout), CCC_RELEASE_REPOS=str(checkout))
    for name in ("CCC_RELEASE_DAILY_LOCKED", "SSH_ORIGINAL_COMMAND"):
        env.pop(name, None)

    def run(*args, driver=checkout):
        return subprocess.run(["bash", driver / "scripts/release-daily.sh", *args],
                              env=env, capture_output=True, text=True)

    return checkout, tickets, log, run


def test_pending_blocker_is_reused_and_recurrence_opens_a_ticket(gate):
    checkout, store, log, run = gate
    first = run()
    assert first.returncode == 0 and "WAIVED:" in first.stdout, first
    tickets = json.loads(store.read_text())
    assert len(tickets) == 1
    assert tickets[0]["client_id"]
    tickets[0]["status"] = "awaiting_answer"
    # Keep the first episode as if it had been filed on a previous day.
    tickets[0]["dedupe"] += "-previous-day"
    store.write_text(json.dumps(tickets))

    (checkout / "dirty-0").write_text("more edits on the same path")
    repeat = run()
    assert repeat.returncode == 1 and "FAILED: release blocked 2 days" in repeat.stdout, repeat
    assert json.loads(store.read_text()) == tickets

    tickets[0]["status"] = "closed"
    store.write_text(json.dumps(tickets))
    for _ in range(2):
        assert run().returncode == 1
    recurrence = json.loads(store.read_text())
    assert len(recurrence) == 2
    assert recurrence[1]["client_id"] == recurrence[0]["client_id"]

    tickets_before_dry_run = store.read_bytes()
    streak_before_dry_run = (log / "waive-streak").read_bytes()
    assert run("--dry-run").returncode == 1
    assert store.read_bytes() == tickets_before_dry_run
    assert (log / "waive-streak").read_bytes() == streak_before_dry_run


def test_different_blocker_hidden_by_display_limit_gets_its_own_ticket(gate):
    checkout, store, _, run = gate
    assert run().returncode == 0
    (checkout / "dirty-5").rename(checkout / "dirty-6")
    assert run().returncode == 1
    tickets = json.loads(store.read_text())
    assert len(tickets) == 2
    assert tickets[0]["note"] == tickets[1]["note"]
    assert tickets[0]["client_id"] != tickets[1]["client_id"]


def test_separate_driver_inspects_canonical_checkout(gate):
    checkout, store, _, run = gate
    release = run("--dry-run", driver=ROOT)
    assert release.returncode == 0, release
    assert f"BLOCK {checkout}" in release.stdout
    assert json.loads(store.read_text()) == []
