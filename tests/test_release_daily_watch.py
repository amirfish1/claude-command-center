"""Publication and natural service receipts must agree; fixtures never run a publisher."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("release_daily_watch", Path(__file__).resolve().parents[1] / "scripts/release_daily_watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def inputs():
    unit = {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0",
            "ExecMainStartTimestamp": "Wed 2026-11-18 07:00:00 UTC",
            "ExecMainExitTimestamp": "Wed 2026-11-18 07:30:00 UTC"}
    timer = {"LastTriggerUSec": unit["ExecMainStartTimestamp"]}
    receipt = {"tag_name": "v1.2.3", "draft": False, "prerelease": False,
               "published_at": "2026-11-18T07:29:00Z", "contains_required": True, "release_commit": "a" * 40}
    logs = {"2026-11-18": "07:29:01 RELEASED: v1.2.3\n"}
    return unit, timer, receipt, logs


def test_only_fresh_completed_failures_are_regressions(tmp_path, monkeypatch):
    unit, timer, _, logs = inputs()
    unit.update(Result="exit-code", ExecMainStatus="1")
    since = watch.timestamp("2026-11-17T09:50:00Z")
    def no_publication(_):
        raise AssertionError("Failed, stale or running jobs must not query a release")
    assert watch.evaluate(unit, timer, since, logs, no_publication)["status"] == "fail"
    for changes in ({"ExecMainStartTimestamp": "2026-11-17T07:00:00Z"}, {"ActiveState": "activating"},
                    {"LoadState": "not-found"}, {"ExecMainExitTimestamp": "n/a"}):
        candidate = {**unit, **changes}
        report = watch.evaluate(candidate, timer, since, logs, no_publication)
        assert report["status"] == "pending" and report["real_data"] is False
    # A damaged log must not hide an independently proven fresh unit failure.
    (tmp_path / "2026-11-18.log").mkdir()
    monkeypatch.setattr(watch, "properties", lambda name: timer if name.endswith(".timer") else unit)
    args = SimpleNamespace(unit="daily.service", since=since, log_dir=tmp_path)
    assert watch.collect(args)["status"] == "fail"


def test_pass_requires_timer_unit_log_and_published_containing_release():
    unit, timer, receipt, logs = inputs()
    since = watch.timestamp("2026-11-17T09:50:00Z")
    def check(u=unit, t=timer, r=receipt, l=logs):
        return watch.evaluate(u, t, since, l, lambda tag: r)
    assert check()["status"] == "pass" and check()["real_data"] is True
    for bad in ({"release_commit": None}, {"draft": True}, {"prerelease": True}, {"contains_required": False},
                {"published_at": "2026-11-17T07:29:00Z"}, {"tag_name": "v1.0.0"}):
        assert check(r={**receipt, **bad})["status"] == "pending"
    assert check(t={"LastTriggerUSec": "2026-11-17T07:00:00Z"})["status"] == "pending"
    assert check(l={"2026-11-18": "07:29:01 WAIVED: dirty tree\n"})["status"] == "pending"
    assert check(l={"2026-11-18": "06:59:00 RELEASED: v1.2.3\n"})["status"] == "pending"
    assert check(l={"2026-11-17": "07:29:01 RELEASED: v1.2.3\n"})["status"] == "pending"
