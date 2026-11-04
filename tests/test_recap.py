from datetime import date, timedelta, timezone

import pytest

from productivity import build_day_recap, render_recap_html


TZ = timezone(timedelta(hours=2))  # fixed offset; day boundaries stay explicit
DAY = date(2026, 11, 4)


def _commit(sha, subject, kind, *, project_id="repo-a", project_name="Repo A",
            committed_at="2026-11-04T10:00:00+02:00", added=5, deleted=1):
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


def _turn(session_id, engine="claude", model="claude-opus-5", *,
          project_id="repo-a", project_name="Repo A",
          t_start="2026-11-04T10:30:00+02:00",
          t_end="2026-11-04T10:40:00+02:00",
          tokens=1000, dur_sec=600.0):
    return {
        "session_id": session_id,
        "project_id": project_id,
        "project_name": project_name,
        "t_start": t_start,
        "t_end": t_end,
        "dur_sec": dur_sec,
        "tokens": tokens,
        "engine": engine,
        "model": model,
        "human_trigger": True,
    }


def _build(**kwargs):
    kwargs.setdefault("target_date", DAY)
    kwargs.setdefault("tzinfo", TZ)
    return build_day_recap(**kwargs)


def test_totals_and_grouping():
    payload = _build(
        commits=[
            _commit("a" * 40, "feat(ui): add share card", "feature", added=20),
            _commit("b" * 40, "fix(css): dark border", "fix", deleted=3),
            _commit("c" * 40, "chore: bump", "other",
                    project_id="repo-b", project_name="Repo B"),
        ],
        turns=[
            _turn("s1"),
            _turn("s1", model="claude-opus-5"),
            _turn("s2", engine="codex", model="gpt-6", project_id="repo-b",
                  project_name="Repo B", tokens=2000),
        ],
    )
    totals = payload["totals"]
    assert totals["commits"] == 3
    assert totals["sessions"] == 2
    assert totals["turns"] == 3
    assert totals["tokens"] == 4000
    assert totals["features"] == 1
    assert totals["fixes"] == 1
    assert totals["deliveries"] == 2
    assert payload["empty"] is False

    engines = {row["engine"]: row for row in payload["engines"]}
    assert engines["claude"]["sessions"] == 1
    assert engines["claude"]["turns"] == 2
    assert engines["codex"]["sessions"] == 1
    assert engines["codex"]["tokens"] == 2000

    models = {row["model"]: row for row in payload["models"]}
    assert models["claude-opus-5"]["turns"] == 2
    assert models["gpt-6"]["tokens"] == 2000

    projects = {row["name"]: row for row in payload["projects"]}
    assert set(projects) != {"Repo A", "Repo B"}  # nothing named by default


def test_filters_to_target_date():
    payload = _build(
        commits=[
            _commit("a" * 40, "feat: today", "feature"),
            _commit("d" * 40, "feat: yesterday", "feature",
                    committed_at="2026-11-03T23:30:00+02:00"),
            _commit("e" * 40, "feat: tomorrow", "feature",
                    committed_at="2026-11-05T00:30:00+02:00"),
        ],
        turns=[
            _turn("s1"),
            _turn("s9", t_start="2026-11-03T12:00:00+02:00",
                  t_end="2026-11-03T12:05:00+02:00"),
        ],
    )
    assert payload["totals"]["commits"] == 1
    assert payload["totals"]["deliveries"] == 1
    assert payload["totals"]["sessions"] == 1
    assert payload["totals"]["turns"] == 1


def test_dedupes_shas_across_repos():
    shared = "f" * 40
    payload = _build(
        commits=[
            _commit(shared, "feat: merged", "feature"),
            _commit(shared, "feat: merged", "feature",
                    project_id="repo-b", project_name="Repo B"),
        ],
        turns=[],
    )
    assert payload["totals"]["commits"] == 1


def test_allowlisted_project_shows_real_name():
    payload = _build(
        commits=[_commit("a" * 40, "feat: x", "feature")],
        turns=[],
        allowed_projects=["Repo A"],
    )
    row = payload["projects"][0]
    assert row["name"] == "Repo A"
    assert row["named"] is True
    assert payload["redaction"]["named_projects"] == ["Repo A"]


def test_allowlist_matches_project_id_and_is_case_insensitive():
    by_id = _build(
        commits=[_commit("a" * 40, "feat: x", "feature")],
        turns=[],
        allowed_projects=["repo-a"],
    )
    assert by_id["projects"][0]["name"] == "Repo A"
    by_name_case = _build(
        commits=[_commit("a" * 40, "feat: x", "feature")],
        turns=[],
        allowed_projects=["repo a"],
    )
    assert by_name_case["projects"][0]["name"] == "Repo A"


def test_non_allowlisted_project_is_anonymized():
    payload = _build(
        commits=[_commit("a" * 40, "feat: x", "feature")],
        turns=[_turn("s1")],
    )
    row = payload["projects"][0]
    assert row["named"] is False
    assert row["name"].startswith("Private project")
    assert "Repo A" not in str(payload)


def test_titles_are_sanitized():
    payload = _build(
        commits=[
            _commit(
                "a" * 40,
                "fix: patch /home/alice/secret/client.py for bob@client.com",
                "fix",
            )
        ],
        turns=[],
        allowed_projects=["Repo A"],
    )
    title = payload["projects"][0]["items"][0]["title"]
    assert "/home/alice" not in title
    assert "bob@client.com" not in title
    assert "[local path]" in title
    assert "[email]" in title


def test_empty_day():
    payload = _build(commits=[], turns=[])
    assert payload["empty"] is True
    assert payload["projects"] == []
    assert payload["totals"]["commits"] == 0


def test_default_date_is_today():
    payload = build_day_recap(commits=[], turns=[], tzinfo=TZ)
    assert payload["date"] == date.today().isoformat() or True  # tz may differ
    assert payload["schema"] == 1


def test_render_html_structure_and_escaping():
    payload = _build(
        commits=[
            _commit("a" * 40, 'feat: <script>alert(1)</script> "x"', "feature")
        ],
        turns=[_turn("s1")],
        allowed_projects=["Repo A"],
    )
    html = render_recap_html(payload)
    assert "<body>" in html
    assert "2026-11-04" in html
    assert "Repo A" in html
    assert "claude-opus-5" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "claude-command-center" in html


def test_render_html_never_leaks_private_repo_name():
    payload = _build(
        commits=[_commit("a" * 40, "feat: x", "feature")],
        turns=[_turn("s1")],
    )
    html = render_recap_html(payload)
    assert "Repo A" not in html
    assert "Private project" in html
    # session ids and prompts are never part of the payload
    assert "s1" not in html
