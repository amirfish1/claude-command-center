#!/usr/bin/env python3
"""Durable DMG backfills. State lives outside the checkout; runtime is stdlib-only."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")


def log_dir():
    default = (Path.home() / "Library/Logs/ccc-release" if sys.platform == "darwin"
               else Path.home() / ".local/state/ccc-release")
    return Path(os.environ.get("CCC_RELEASE_LOG_DIR", default))


def run(*args, cwd=None, **kwargs):
    return subprocess.run(list(map(str, args)), cwd=cwd or ROOT, check=True, text=True,
                          stdout=subprocess.PIPE, **kwargs).stdout.strip()


def atomic_json(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def lock(filename):
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def enqueue(tag, directory):
    with lock(directory / "dmg-pending.lock"):
        target = directory / "dmg-pending"
        queue = json.loads(target.read_text()) if target.exists() else {}
        queue.setdefault(tag, {"since": int(time.time())})
        atomic_json(target, queue)


def pending(directory, dry=False):
    print("BACKFILL: checking pending DMGs", flush=True)
    target = directory / "dmg-pending"
    # Dry runs do not touch the queue, SSH, tickets, or lock files.
    if dry:
        queue = json.loads(target.read_text()) if target.exists() else {}
        for tag in queue:
            print(f"[dry-run] would backfill {tag} on the signing Mac")
        return
    host = os.environ.get("CCC_RELEASE_SIGNING_HOST", "")
    # One consumer, while enqueue can still append during a long remote build.
    with lock(directory / "dmg-drain.lock"):
        with lock(directory / "dmg-pending.lock"):
            queue = json.loads(target.read_text()) if target.exists() else {}
        for tag, entry in queue.items():
            if not TAG.fullmatch(tag):
                raise ValueError(f"invalid pending tag: {tag!r}")
            token = uuid.uuid4().hex
            try:
                if not host or host.startswith("-"):
                    raise ValueError("set CCC_RELEASE_SIGNING_HOST to the signing host SSH alias")
                response = run("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                               host, f"backfill {tag} {token}", timeout=2800)
                if f"BACKFILL-OK {tag} {token}" not in response.splitlines():
                    raise ValueError("remote trigger did not return this request's success receipt")
            except (subprocess.SubprocessError, ValueError) as exc:
                print(f"BACKFILL: {tag} still pending: {exc}", flush=True)
                if time.time() - entry["since"] >= 48 * 3600:
                    # The stable key also makes retries safe after a local crash.
                    try:
                        run("wt", "add", "-q", "CCC", "--no-dispatch", "--title",
                            f"DMG backfill pending for {tag}", "--note",
                            f"Pending since Unix {entry['since']}; run cut-release.sh --dmg-only {tag} "
                            "on the signing Mac, then retry the daily backfill.",
                            "--dedupe-key", f"dmg-backfill-{tag}")
                    except (subprocess.SubprocessError, FileNotFoundError) as error:
                        print(f"BACKFILL: could not file escalation: {error}", file=sys.stderr)
                continue
            with lock(directory / "dmg-pending.lock"):
                current = json.loads(target.read_text())
                current.pop(tag, None)
                atomic_json(target, current)
            print(f"BACKFILL: completed {tag}", flush=True)


def version(item):
    value = item.findtext(f"{{{SPARKLE}}}version", "")
    if not TAG.fullmatch("v" + value):
        raise ValueError("appcast item has an invalid version")
    return tuple(map(int, value.split(".")))


def item_for(feed, tag):
    channel = ET.fromstring(feed).find("channel")
    for item in channel.findall("item"):
        if version(item) == tuple(map(int, tag[1:].split("."))):
            return item
    return None


def merge_feed(current, built, tag):
    """Keep existing signed entries intact and insert by version, never downgrade."""
    ET.fromstring(current)
    for match in re.finditer(r"<item>.*?</item>", built, re.S):
        item_xml = match.group()
        item = ET.fromstring(f'<rss xmlns:sparkle="{SPARKLE}">{item_xml}</rss>')[0]
        if version(item) != tuple(map(int, tag[1:].split("."))):
            continue
        for old in re.finditer(r"<item>.*?</item>", current, re.S):
            old_item = ET.fromstring(f'<rss xmlns:sparkle="{SPARKLE}">{old.group()}</rss>')[0]
            if version(old_item) == version(item):
                if old_item.find("enclosure").attrib != item.find("enclosure").attrib:
                    raise ValueError("appcast already contains a different signed item for this version")
                return current
            if version(old_item) < version(item):
                return current[:old.start()] + item_xml + "\n" + current[old.start():]
        offset = current.index("</channel>")
        return current[:offset] + item_xml + "\n" + current[offset:]
    raise ValueError("build did not produce the requested signed appcast item")


def assets(tag):
    data = json.loads(run("gh", "release", "view", tag, "--json", "assets"))
    return {asset["name"]: asset for asset in data["assets"]}


def sha256(filename):
    digest = hashlib.sha256()
    with filename.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def worktree(destination, ref):
    if not destination.exists():
        run("git", "worktree", "add", "--detach", destination, ref)
    if run("git", "rev-parse", "HEAD", cwd=destination) != run("git", "rev-parse", ref + "^{commit}"):
        raise ValueError(f"backfill workspace does not match {ref}: {destination}")


def publish(tag, directory, dry=False):
    if dry:
        print(f"[dry-run] isolated tag build, signed assets and appcast for {tag}; no publication")
        return
    if sys.platform != "darwin":
        raise ValueError("DMG backfill requires the signing Mac (macOS)")
    with lock(directory / "dmg-publish.lock"):
        run("git", "fetch", "origin", "main", f"refs/tags/{tag}:refs/tags/{tag}")
        # Workspaces survive failures, so uploads never force a different rebuild.
        build = directory / "dmg-builds" / tag
        build.parent.mkdir(parents=True, exist_ok=True)
        worktree(build, tag)
        name = f"ccc-{tag}.dmg"
        dmg = build / name
        ready = build / "backfill-ready.json"
        existing = assets(tag)
        if name in existing:
            # Published versioned bytes are immutable and authoritative on recovery.
            with tempfile.TemporaryDirectory(dir=directory) as temp:
                run("gh", "release", "download", tag, "--pattern", name, "--dir", temp)
                shutil.copyfile(Path(temp) / name, dmg)
        cached = json.loads(ready.read_text()) if ready.exists() else {}
        if name not in existing and (not dmg.exists() or cached.get("sha256") != sha256(dmg)):
            # A failed build can leave a partial DMG. Only a validated build is reusable.
            if dmg.exists():
                dmg.unlink()
            print(run("bash", "scripts/release-dmg.sh", tag[1:], cwd=build), flush=True)
        else:
            # Re-sign the actual published/cached bytes, without rebuilding them.
            print(run("bash", "scripts/release-dmg.sh", tag[1:], cwd=build,
                      env=os.environ | {"SKIP_BUILD": "1"}), flush=True)
        built = (build / "docs/appcast.xml").read_text()
        item = item_for(built, tag)
        enclosure = item.find("enclosure")
        if (not enclosure.get(f"{{{SPARKLE}}}edSignature")
                or int(enclosure.get("length", "0")) != dmg.stat().st_size
                or not enclosure.get("url", "").endswith(f"/{tag}/{name}")):
            raise ValueError("invalid signed enclosure")
        atomic_json(ready, {"tag": tag, "sha256": sha256(dmg)})
        if name in existing and "ccc.dmg" in existing:
            with tempfile.TemporaryDirectory(dir=directory) as temp:
                run("gh", "release", "download", tag, "--pattern", "ccc.dmg", "--dir", temp)
                remote_item = item_for(run("git", "show", "origin/main:docs/appcast.xml"), tag)
                if (sha256(Path(temp) / "ccc.dmg") == sha256(dmg) and remote_item is not None
                        and remote_item.find("enclosure").attrib == enclosure.attrib):
                    print(f"BACKFILL: {tag} already complete; refusing duplicate publication", flush=True)
                    return
        if name not in existing:
            run("gh", "release", "upload", tag, dmg)
        stable = build / "ccc.dmg"
        shutil.copyfile(dmg, stable)
        run("gh", "release", "upload", tag, stable, "--clobber")
        # Confirm both uploaded assets, including platforms without asset digest metadata.
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            run("gh", "release", "download", tag, "--pattern", name,
                "--pattern", "ccc.dmg", "--dir", temp)
            if any(sha256(Path(temp) / n) != sha256(dmg) for n in (name, "ccc.dmg")):
                raise ValueError("published DMG bytes do not match signed bytes")
        # Publish only the one new XML item, from a fresh checkout of remote main.
        # A failed push leaves its commit only in this disposable worktree; retry
        # merges the signed item onto the latest remote instead of changing main.
        with tempfile.TemporaryDirectory(prefix="ccc-appcast-", dir=directory) as temp:
            checkout = Path(temp) / "repo"
            run("git", "worktree", "add", "--detach", checkout, "origin/main")
            try:
                feed = checkout / "docs/appcast.xml"
                before = feed.read_text()
                after = merge_feed(before, built, tag)
                if after != before:
                    feed.write_text(after)
                    run("git", "commit", "--only", "docs/appcast.xml", "-m",
                        f"chore(release): backfill {tag} appcast", cwd=checkout)
                    run("git", "push", "origin", "HEAD:refs/heads/main", cwd=checkout)
                run("git", "fetch", "origin", "main")
                remote = run("git", "show", "origin/main:docs/appcast.xml")
                remote_item = item_for(remote, tag)
                if (remote_item is None or remote_item.find("enclosure").attrib != enclosure.attrib):
                    raise ValueError("remote main does not contain the backfill item")
            finally:
                # Never force-clean a publication checkout.
                if run("git", "status", "--porcelain", cwd=checkout) == "":
                    run("git", "worktree", "remove", checkout)
        print(f"BACKFILL: verified signed assets and remote appcast for {tag}", flush=True)


def locked_shell(kind, dry=False):
    """Hold an OS lock across a shell job; crashes cannot leave it wedged."""
    filename = (Path.home() / ".claude/command-center/release-trigger.lock" if kind == "trigger"
                else Path(tempfile.gettempdir()) / "ccc-release-daily.lockfile")
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if kind == "daily":
                print("WAIVED: another daily release run holds the lock")
                return
            raise ValueError("release trigger is busy")
        variable = "CCC_RELEASE_TRIGGER_LOCKED" if kind == "trigger" else "CCC_RELEASE_DAILY_LOCKED"
        command = ["bash", str(ROOT / f"scripts/release-{kind}.sh")]
        if dry:
            command.append("--dry-run")
        result = subprocess.run(command, env=os.environ | {variable: "1"})
        if result.returncode:
            raise ValueError(f"release {kind} exited {result.returncode}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("enqueue", "pending", "publish", "trigger", "daily"))
    parser.add_argument("tag", nargs="?")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command in ("trigger", "daily"):
        locked_shell(args.command, args.dry_run)
        return
    if args.command != "pending" and not TAG.fullmatch(args.tag or ""):
        parser.error("tag must be vX.Y.Z")
    if args.command == "enqueue":
        if not args.dry_run:
            enqueue(args.tag, log_dir())
    elif args.command == "pending":
        pending(log_dir(), args.dry_run)
    else:
        publish(args.tag, log_dir(), args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"BACKFILL FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
