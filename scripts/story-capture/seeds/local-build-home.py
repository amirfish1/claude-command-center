#!/usr/bin/env python3
"""Build a synthetic $HOME for public-safe live-server CCC captures.

The product-story asset ledger needs screenshots of surfaces the static demo
bundle cannot render (plan-usage windows, the UX-fixes queue board, the queue
health strip, the cross-machine handoff UI, ...). Those read real server state
from files under ~/.claude, so we materialise a FAKE home directory populated
entirely with synthetic data (fake repo names, fake session titles, /home/demo
style paths) and boot server.py against it.

Nothing here touches the real user's ~/.claude. Every visible string is
synthetic. Run, then:

    HOME=<out> PORT=8091 CCC_EPHEMERAL=1 CCC_TELEMETRY_DISABLED=1 \
        CCC_SKIP_SKILL_INSTALL=1 python3 server.py

Usage:
    python3 scripts/story-capture/seeds/local-build-home.py [--out DIR]

Default --out is /private/tmp/ccc-demo-home (deliberately username-free so no
leaked path can reveal a real account). Idempotent: wipes and rebuilds --out.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_s(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(path):
    # Claude Code project-dir slug: non-alnum -> '-'.
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


# --- Synthetic fleet -------------------------------------------------------
# Fake repos, all /home/demo style so nothing on-disk leaks into the UI.
REPOS = {
    "widgets-api": "/home/demo/code/widgets-api",
    "checkout-web": "/home/demo/code/checkout-web",
    "billing-svc": "/home/demo/code/billing-svc",
}

MODELS = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
}


def usage_block(tin, tout, cache_read=0, cache_creation=0):
    return {
        "input_tokens": tin,
        "output_tokens": tout,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def assistant_ev(sid, cwd, model, uuid_, parent, ts, text, usage, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "id": tool["id"], "name": tool["name"],
                        "input": tool.get("input", {})})
    return {
        "parentUuid": parent, "isSidechain": False,
        "message": {"model": model, "id": "msg_" + uuid_, "type": "message",
                    "role": "assistant", "content": content,
                    "usage": usage, "stop_reason": "tool_use" if tool else "end_turn"},
        "requestId": "req_" + uuid_, "type": "assistant", "uuid": uuid_,
        "timestamp": iso(ts), "userType": "external", "entrypoint": "sdk-cli",
        "cwd": cwd, "sessionId": sid, "version": "2.1.140", "gitBranch": "main",
    }


def user_ev(sid, cwd, uuid_, parent, ts, text=None, tool_result=None):
    if tool_result is not None:
        content = [{"tool_use_id": tool_result["id"], "type": "tool_result",
                    "content": tool_result["content"]}]
    else:
        content = [{"type": "text", "text": text}]
    return {
        "parentUuid": parent, "isSidechain": False, "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": uuid_, "timestamp": iso(ts), "userType": "external",
        "entrypoint": "sdk-cli", "cwd": cwd, "sessionId": sid,
        "version": "2.1.140", "gitBranch": "main",
    }


def build_session(sid, repo_key, title, model_key, start, n_turns,
                  in_base, out_base, tail_state=None):
    """Return (project_slug, [jsonl lines]) for one plausible session."""
    cwd = REPOS[repo_key]
    model = MODELS[model_key]
    lines = [{"type": "custom-title", "customTitle": title, "sessionId": sid}]
    parent = None
    ts = start
    lines.append(user_ev(sid, cwd, "u-0", parent, ts,
                         text=f"Work on {title.lower()} in {repo_key}."))
    parent = "u-0"
    for i in range(1, n_turns + 1):
        ts = ts + timedelta(minutes=2 + i)
        au = f"a-{i:03d}-{sid[:6]}"
        # ramp token counts so throughput/pace look real
        usage = usage_block(
            tin=in_base + i * 900,
            tout=out_base + i * 320,
            cache_read=4200 + i * 5100,
            cache_creation=1800 + i * 260,
        )
        is_last = (i == n_turns)
        if is_last and tail_state:
            text = tail_state
        elif is_last:
            text = ("Done. Change is in and the smoke check passes.\n\n"
                    "<session-state>\nDID: Implemented the change and verified it.\n"
                    "NEXT_STEP_USER: Review and merge.\n</session-state>")
        else:
            text = f"Step {i}: editing files and running checks."
        tool = None if is_last else {"id": f"toolu_{au}", "name": "Edit",
                                     "input": {"file_path": f"{cwd}/src/mod_{i}.py"}}
        lines.append(assistant_ev(sid, cwd, model, au, parent, ts, text, usage, tool))
        parent = au
        if tool:
            ts = ts + timedelta(seconds=40)
            ru = f"u-{i:03d}-{sid[:6]}"
            lines.append(user_ev(sid, cwd, ru, parent, ts,
                                tool_result={"id": tool["id"],
                                             "content": "Applied. 1 file changed."}))
            parent = ru
    lines.append({"type": "result", "subtype": "success", "duration_ms": 5200,
                  "is_error": False, "num_turns": n_turns, "session_id": sid,
                  "timestamp": iso(ts + timedelta(seconds=5)),
                  "total_cost_usd": round(0.02 * n_turns, 4)})
    return slug(cwd), lines


# session_id, repo, title, model, days_ago(start), turns, in_base, out_base, tail
SESSIONS = [
    ("11111111-1111-4aaa-aaaa-000000000001", "widgets-api",
     "Add rate-limit headers to public API", "opus", 0.12, 7, 5200, 1600, None),
    ("22222222-2222-4aaa-aaaa-000000000002", "widgets-api",
     "Refactor pagination cursor helper", "sonnet", 0.9, 5, 3100, 900, None),
    ("33333333-3333-4aaa-aaaa-000000000003", "checkout-web",
     "Fix cart total rounding bug", "opus", 1.8, 6, 4800, 1400,
     ("Which currency should the rounding follow, the cart currency or the "
      "user locale? Let me know and I'll finish.\n\n<session-state>\n"
      "DID: Traced the rounding to formatMoney().\n"
      "NEXT_STEP_USER: Answer the currency question above.\n</session-state>")),
    ("44444444-4444-4aaa-aaaa-000000000004", "checkout-web",
     "Migrate checkout form to new validator", "sonnet", 2.6, 8, 3600, 1100, None),
    ("55555555-5555-4aaa-aaaa-000000000005", "billing-svc",
     "Add Stripe webhook retry backoff", "opus", 3.4, 5, 6100, 1900, None),
    ("66666666-6666-4aaa-aaaa-000000000006", "billing-svc",
     "Write invoice PDF generator", "sonnet", 4.7, 9, 2800, 850, None),
    ("77777777-7777-4aaa-aaaa-000000000007", "widgets-api",
     "Investigate slow /search endpoint", "opus", 5.6, 6, 5400, 1700, None),
]


def write_transcripts(home):
    proj_root = home / ".claude" / "projects"
    for (sid, repo, title, mk, days, turns, ib, ob, tail) in SESSIONS:
        start = NOW - timedelta(days=days)
        pslug, lines = build_session(sid, repo, title, mk, start, turns, ib, ob, tail)
        d = proj_root / pslug
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{sid}.jsonl").open("w", encoding="utf-8") as fh:
            for ln in lines:
                fh.write(json.dumps(ln) + "\n")
    return proj_root


def write_usage(state_dir):
    """usage-snapshots.jsonl: 48 native snapshots over 24h, ramping utilisation.
    Latest snapshot within the 15-min freshness window so pace renders."""
    udir = state_dir / "usage"
    udir.mkdir(parents=True, exist_ok=True)
    weekly_reset = NOW + timedelta(days=2, hours=6)      # a few days out
    five_reset = NOW + timedelta(hours=2, minutes=40)
    snaps = []
    n = 48
    for i in range(n):
        # oldest -> newest; newest is i=n-1 at ~now-3min
        ts = NOW - timedelta(minutes=3) - timedelta(minutes=30 * (n - 1 - i))
        frac = i / (n - 1)
        seven = round(18 + frac * 45, 1)            # 18 -> 63 %
        sonnet = round(9 + frac * 29, 1)            # 9 -> 38 %
        five = round(12 + (frac * 61) % 55, 1)      # sawtooth-ish session window
        snaps.append({
            "ts": iso_s(ts), "source": "native",
            "five_hour": {"utilization": five, "resets_at": iso_s(five_reset)},
            "seven_day": {"utilization": seven, "resets_at": iso_s(weekly_reset)},
            "seven_day_sonnet": {"utilization": sonnet, "resets_at": iso_s(weekly_reset)},
            "seven_day_fable": {"utilization": None, "resets_at": None},
            "codex": None,
        })
    with (udir / "usage-snapshots.jsonl").open("w", encoding="utf-8") as fh:
        for s in snaps:
            fh.write(json.dumps(s) + "\n")
    # A reset event a few hours ago (5-hour window reset).
    ev = {
        "kind": "five_hour",
        "detected_at": iso_s(NOW - timedelta(hours=5, minutes=12)),
        "prev_utilization": 96.0, "new_utilization": 7.0,
        "resets_at": iso_s(five_reset),
    }
    with (udir / "reset-events.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(ev) + "\n")


def qitem(number, project, seq, note, status, model="claude", lane="normal",
          claimed_by=None, claimed_session_id=None,
          minutes_ago_created=120, minutes_ago_closed=None, minutes_ago_claimed=None,
          readiness="ready", value="M", confidence="M", priority="p2",
          repo_path="", selector="", title=""):
    created = NOW - timedelta(minutes=minutes_ago_created)
    updated = created
    closed_at = None
    claimed_at = None
    if status in ("in_progress", "closed"):
        if minutes_ago_claimed is not None:
            claimed_at = NOW - timedelta(minutes=minutes_ago_claimed)
        else:
            claimed_at = created + timedelta(minutes=6)
        updated = claimed_at
    if status == "closed":
        closed_at = NOW - timedelta(minutes=minutes_ago_closed if minutes_ago_closed else 30)
        updated = closed_at
    ref = f"{project}-{seq}"
    return {
        "number": number, "project": project, "seq": seq, "ref": ref,
        "id": f"ann-{created.strftime('%Y%m%d-%H%M%S')}-{number:04d}",
        "status": status, "lane": lane, "source": "ccc",
        "note": note, "text": note,
        "url": f"http://127.0.0.1:8091/#{selector}" if selector else "",
        "title": title or note[:48], "selector": selector,
        "screenshot_path": "", "repo_path": repo_path,
        "readiness": readiness, "value": value, "confidence": confidence,
        "priority": priority,
        "claimed_by": claimed_by,
        "claimed_at": iso_s(claimed_at) if claimed_at else None,
        "claimed_session_id": claimed_session_id,
        "closed_at": iso_s(closed_at) if closed_at else None,
        "created_at": iso_s(created), "updated_at": iso_s(updated),
    }


def write_queue(state_dir):
    """ux-fixes-queue.json: several projects, mixed states.

    widgets-api: open + in_progress + closed, claimed by a NON-live label ->
    the health strip flags it stuck (depth>0, no resolvable live fixer).
    checkout-web: all closed -> a cleanly drained queue for contrast.
    billing-svc: one open needs-shaping ticket -> backlog.
    """
    items = []
    n = 1
    wpath = REPOS["widgets-api"]
    cpath = REPOS["checkout-web"]
    bpath = REPOS["billing-svc"]
    # widgets-api (WIDGETS)
    items.append(qitem(n, "WIDGETS", 12, "Rate-limit banner overlaps the header on mobile",
                       "open", minutes_ago_created=52, priority="p1", value="H",
                       repo_path=wpath, selector="#usageBar")); n += 1
    items.append(qitem(n, "WIDGETS", 13, "Pace tooltip shows raw percent, wants 1 decimal",
                       "open", minutes_ago_created=38, priority="p2",
                       repo_path=wpath, selector=".pace-chip")); n += 1
    # in_progress claimed by a worker whose session is NO LONGER live -> the
    # health strip flags WIDGETS as STUCK (resolvable fixer sid + no live worker).
    items.append(qitem(n, "WIDGETS", 14, "Throughput week toggle resets range on reload",
                       "in_progress", claimed_by="codex-widgets-drain",
                       claimed_session_id="a1b2c3d4-1234-4abc-8def-000000000014",
                       minutes_ago_created=95, minutes_ago_claimed=14,
                       priority="p2", repo_path=wpath)); n += 1
    items.append(qitem(n, "WIDGETS", 11, "Search box loses focus after results load",
                       "closed", claimed_by="CCC-241", minutes_ago_created=260,
                       minutes_ago_closed=180, repo_path=wpath)); n += 1
    items.append(qitem(n, "WIDGETS", 10, "Dark-mode contrast on secondary buttons",
                       "closed", claimed_by="CCC-238", minutes_ago_created=420,
                       minutes_ago_closed=300, repo_path=wpath)); n += 1
    # checkout-web (CHECKOUT) — fully drained
    items.append(qitem(n, "CHECKOUT", 6, "Cart drawer animation janky on Safari",
                       "closed", claimed_by="CCC-233", minutes_ago_created=520,
                       minutes_ago_closed=95, repo_path=cpath)); n += 1
    items.append(qitem(n, "CHECKOUT", 5, "Coupon field accepts trailing whitespace",
                       "closed", claimed_by="CCC-230", minutes_ago_created=680,
                       minutes_ago_closed=140, repo_path=cpath)); n += 1
    items.append(qitem(n, "CHECKOUT", 4, "Address autocomplete dropdown z-index",
                       "closed", claimed_by="CCC-228", minutes_ago_created=900,
                       minutes_ago_closed=210, repo_path=cpath)); n += 1
    # billing-svc (BILLING) — backlog
    items.append(qitem(n, "BILLING", 3, "Invoice PDF: totals column misaligned",
                       "open", minutes_ago_created=15, priority="p1", value="H",
                       readiness="needs-shaping", repo_path=bpath)); n += 1
    store = {"counter": n - 1, "items": items}
    (state_dir / "ux-fixes-queue.json").write_text(json.dumps(store, indent=2))


def write_watchtower(home):
    """queue-config.json: mark WIDGETS/CHECKOUT as auto-drain queues so the
    health strip can render STUCK (a stuck flag requires auto_drain=True; see
    compute_queues_health). BILLING stays a manual backlog."""
    wt = home / ".watchtower"
    wt.mkdir(parents=True, exist_ok=True)
    cfg = {
        "WIDGETS": {"auto_drain": True, "repo_path": REPOS["widgets-api"], "claim_types": []},
        "CHECKOUT": {"auto_drain": True, "repo_path": REPOS["checkout-web"], "claim_types": []},
        "BILLING": {"auto_drain": False, "repo_path": REPOS["billing-svc"], "claim_types": []},
    }
    (wt / "queue-config.json").write_text(json.dumps(cfg, indent=2) + "\n")


def write_federation(state_dir):
    """node.json (this node) + peers.json (one reachable-looking peer) so the
    Continue-on-another-machine handoff modal lists a destination."""
    node = {
        "node_id": "9f1c2d34-5678-4abc-8def-0123456789ab",
        "display_name": "laptop-demo",
        "created_at": iso_s(NOW - timedelta(days=40)),
    }
    (state_dir / "node.json").write_text(json.dumps(node, indent=2) + "\n")
    peers = [{
        "node_id": "b7e4f012-3456-4abc-8def-abcdef012345",
        "name": "studio-mac-mini",
        "transport": {"type": "ssh", "host": "studio.demo.internal", "user": "demo"},
        "added_at": iso_s(NOW - timedelta(days=12)),
        "last_seen": iso_s(NOW - timedelta(minutes=9)),
    }]
    (state_dir / "peers.json").write_text(json.dumps(peers, indent=2) + "\n")


def write_misc(home, state_dir):
    # Mark onboarding done so the first-run Welcome modal never covers captures.
    (state_dir / "onboarding.json").write_text(json.dumps(
        {"completed": True, "completed_at": iso_s(NOW - timedelta(days=30))}) + "\n")
    # Keep the fleet from auto-mapping real machine repos.
    (state_dir / "fleet.json").write_text(json.dumps({"automap": False,
                                                      "pinned": [], "hidden": []}) + "\n")
    # Repo picker: list clean fake repos (paths need not exist for the list).
    (state_dir / "custom-repos.txt").write_text("\n".join(REPOS.values()) + "\n")
    (state_dir / "recent-repos.txt").write_text(
        "\n".join([REPOS["widgets-api"], REPOS["checkout-web"]]) + "\n")
    # Give load_known_repos() a clean HOME-child hit so it never falls back to
    # the server's real cwd. Empty git repos, named like the fake fleet.
    for name in REPOS:
        rd = home / name
        rd.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(["git", "init", "-q", str(rd)], check=False,
                           capture_output=True, timeout=20)
        except Exception:
            (rd / ".git").mkdir(exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/private/tmp/ccc-demo-home")
    args = ap.parse_args()
    home = os.path.abspath(args.out)
    if os.path.exists(home):
        shutil.rmtree(home)
    from pathlib import Path
    home = Path(home)
    state_dir = home / ".claude" / "command-center"
    state_dir.mkdir(parents=True, exist_ok=True)
    write_transcripts(home)
    write_usage(state_dir)
    write_queue(state_dir)
    write_watchtower(home)
    write_federation(state_dir)
    write_misc(home, state_dir)
    print(f"[seed] synthetic HOME ready at {home}")
    print(f"[seed] sessions: {len(SESSIONS)}  repos: {list(REPOS)}")
    print(f"[seed] boot: HOME={home} PORT=8091 CCC_EPHEMERAL=1 "
          f"CCC_TELEMETRY_DISABLED=1 CCC_SKIP_SKILL_INSTALL=1 python3 server.py")


if __name__ == "__main__":
    main()
