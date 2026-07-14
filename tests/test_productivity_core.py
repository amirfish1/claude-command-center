from datetime import date, datetime, timedelta, timezone

from productivity import (
    aggregate_productivity,
    classify_commit,
    estimate_work_intervals,
    parse_git_log,
    union_seconds,
)


UTC = timezone.utc


def _commit(
    sha,
    kind,
    subject,
    *,
    project_id="repo-a",
    project_name="Repo A",
    committed_at="2026-07-14T08:00:00+00:00",
    added=10,
    deleted=2,
):
    return {
        "sha": sha,
        "project_id": project_id,
        "project_name": project_name,
        "committed_at": committed_at,
        "subject": subject,
        "kind": kind,
        "lines_added": added,
        "lines_deleted": deleted,
        "lines_changed": added + deleted,
    }


def _ticket(
    ref,
    kind,
    status="closed",
    *,
    project_id="repo-a",
    project_name="Repo A",
    created_at="2026-07-14T07:00:00+00:00",
    closed_at="2026-07-14T09:00:00+00:00",
):
    return {
        "ref": ref,
        "project_id": project_id,
        "project_name": project_name,
        "kind": kind,
        "status": status,
        "title": "Add productivity trends",
        "created_at": created_at,
        "closed_at": closed_at if status == "closed" else None,
    }


def _turn(
    start,
    end,
    *,
    project_id="repo-a",
    project_name="Repo A",
    tokens=1_000,
    human=True,
):
    return {
        "project_id": project_id,
        "project_name": project_name,
        "t_start": start.isoformat(),
        "t_end": end.isoformat(),
        "dur_sec": (end - start).total_seconds(),
        "tokens": tokens,
        "human_trigger": human,
    }


def test_classifies_conventional_outcomes():
    assert classify_commit("feat(ui): add trends") == "feature"
    assert classify_commit("fix!: avoid duplicate ticket") == "fix"
    assert classify_commit("docs: explain cache") == "other"
    assert classify_commit("feature work without convention") == "other"


def test_git_parser_filters_identity_and_sums_numstat():
    raw = (
        "\x1eabc\x1f2026-07-14T08:00:00+00:00\x1fMe\x1fme@example.test"
        "\x1ffeat(ui): add trends\n10\t2\tstatic/productivity.html\n-\t-\timage.png\n"
        "\x1edef\x1f2026-07-14T09:00:00+00:00\x1fOther\x1fother@example.test"
        "\x1ffix: unrelated\n3\t1\tserver.py\n"
    )
    rows = parse_git_log(
        raw,
        {"me@example.test"},
        {"id": "repo-a", "name": "Repo A"},
    )
    assert rows == [
        {
            "sha": "abc",
            "project_id": "repo-a",
            "project_name": "Repo A",
            "committed_at": "2026-07-14T08:00:00+00:00",
            "subject": "feat(ui): add trends",
            "kind": "feature",
            "lines_added": 10,
            "lines_deleted": 2,
            "lines_changed": 12,
        }
    ]


def test_union_removes_parallel_agent_overlap():
    start = datetime(2026, 7, 14, 8, tzinfo=UTC)
    intervals = [
        (start, start + timedelta(minutes=20)),
        (start + timedelta(minutes=10), start + timedelta(minutes=30)),
    ]
    assert union_seconds(intervals) == 30 * 60


def test_prompt_sessions_use_thirty_minute_gap_and_five_minute_tail():
    start = datetime(2026, 7, 14, 8, tzinfo=UTC)
    intervals = estimate_work_intervals(
        [start, start + timedelta(minutes=20), start + timedelta(minutes=60)]
    )
    assert [(end - begin).total_seconds() for begin, end in intervals] == [
        25 * 60,
        5 * 60,
    ]


def test_linked_watchtower_ticket_and_commit_are_one_delivery():
    payload = aggregate_productivity(
        commits=[
            _commit(
                "abc",
                "feature",
                "feat: add productivity trends PRODUCTIVITY-7",
            )
        ],
        turns=[],
        tickets=[_ticket("PRODUCTIVITY-7", "feature")],
        presence=[],
        start_date=date(2026, 7, 14),
        end_date=date(2026, 7, 14),
    )
    assert payload["summary"]["features"] == 1
    assert len(payload["deliveries"]) == 1
    assert payload["deliveries"][0]["sources"] == ["git", "watchtower"]


def test_aggregation_keeps_project_and_time_evidence():
    start = datetime(2026, 7, 14, 8, tzinfo=UTC)
    payload = aggregate_productivity(
        commits=[_commit("abc", "feature", "feat: add trends")],
        turns=[
            _turn(start, start + timedelta(minutes=20), tokens=1_000),
            _turn(
                start + timedelta(minutes=10),
                start + timedelta(minutes=30),
                tokens=2_000,
            ),
        ],
        tickets=[_ticket("PRODUCTIVITY-8", "fix")],
        presence=[
            {
                "sampled_at": (start + timedelta(minutes=minute)).isoformat(),
                "active": True,
                "idle_seconds": 0,
            }
            for minute in range(45)
        ],
        start_date=date(2026, 7, 14),
        end_date=date(2026, 7, 14),
    )
    summary = payload["summary"]
    assert summary["features"] == 1
    assert summary["fixes"] == 1
    assert summary["commits"] == 1
    assert summary["lines_changed"] == 12
    assert summary["turns"] == 2
    assert summary["tokens"] == 3_000
    assert summary["agent_gross_seconds"] == 40 * 60
    assert summary["agent_net_seconds"] == 30 * 60
    assert summary["agent_parallel_seconds"] == 10 * 60
    assert summary["observed_work_seconds"] == 15 * 60
    assert summary["computer_active_minutes"] == 45
    assert summary["focus_hours"] == 1
    assert payload["projects"][0]["name"] == "Repo A"
    assert [item["title"] for item in payload["deliveries"]] == [
        "Add productivity trends",
        "feat: add trends",
    ]


def test_trend_compares_newest_and_oldest_halves():
    commits = []
    start = date(2026, 6, 1)
    for week in range(8):
        count = 1 if week < 4 else 3
        for item in range(count):
            committed = datetime.combine(
                start + timedelta(weeks=week), datetime.min.time(), tzinfo=UTC
            )
            commits.append(
                _commit(
                    f"{week}-{item}",
                    "feature",
                    f"feat: week {week} item {item}",
                    committed_at=committed.isoformat(),
                )
            )
    payload = aggregate_productivity(
        commits=commits,
        turns=[],
        tickets=[],
        presence=[],
        start_date=start,
        end_date=start + timedelta(weeks=8) - timedelta(days=1),
    )
    assert payload["trends"]["delivery_direction"] == "up"
    assert payload["trends"]["delivery_change_pct"] == 200.0
    assert payload["trends"]["delivery_slope_per_week"] > 0
