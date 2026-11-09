"""GitHub issue auto-fix wizard: ccc_server/issue_flow.py and its wiring.

The pure helpers are tested directly. Nothing here talks to GitHub: where an
operation shells out to ``gh``, the module's one ``_gh`` seam is replaced so
the queue and label logic around the call can be checked. The real ``gh``
path is exercised end to end against a live repo, not here.
"""
import pathlib

import pytest

from ccc_server import issue_flow

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent


# -- slug parsing ---------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("acme/storefront", "acme/storefront"),
    ("  acme/storefront  ", "acme/storefront"),
    ("https://github.com/acme/storefront", "acme/storefront"),
    ("https://github.com/acme/storefront.git", "acme/storefront"),
    ("https://github.com/acme/storefront/issues/12", "acme/storefront"),
    ("git@github.com:acme/storefront.git", "acme/storefront"),
    ("ssh://git@github.com/acme/my.web_app.git", "acme/my.web_app"),
])
def test_parse_github_slug_accepts_slugs_and_urls(value, expected):
    assert issue_flow.parse_github_slug(value) == expected


@pytest.mark.parametrize("value", [
    "", None, "storefront", "owner/repo/extra words", "acme/store front",
    "https://gitlab.com/acme/storefront", "acme/$(rm -rf)", "--repo/x y",
])
def test_parse_github_slug_rejects_everything_else(value):
    assert issue_flow.parse_github_slug(value) == ""


# -- queue name suggestion ------------------------------------------------

def test_suggest_queue_name_is_a_valid_unused_queue_name():
    assert issue_flow.suggest_queue_name("acme/storefront") == "STOREFRONT"
    assert issue_flow.suggest_queue_name("acme/my.web_app") == "MY-WEB-APP"


def test_suggest_queue_name_never_returns_a_taken_name():
    taken = ["STOREFRONT"]
    first = issue_flow.suggest_queue_name("acme/storefront", taken)
    assert first != "STOREFRONT"
    assert issue_flow._QUEUE_RE.fullmatch(first)
    # Case-insensitive: queue names are compared upper-cased everywhere.
    second = issue_flow.suggest_queue_name("acme/storefront", ["storefront", first.lower()])
    assert second not in ("STOREFRONT", first)
    assert issue_flow._QUEUE_RE.fullmatch(second)


# -- mode recommendation --------------------------------------------------

def test_every_issue_mode_is_recommended_only_when_it_is_safe():
    # Private and empty: nothing existing to trample, nobody outside can file.
    assert issue_flow.recommended_mode(True, 0) == "all"
    # A public repo lets anyone hand an agent instructions.
    assert issue_flow.recommended_mode(False, 0) == "labeled"
    # Existing open issues would all start at once.
    assert issue_flow.recommended_mode(True, 3) == "labeled"


# -- labels ---------------------------------------------------------------

class _Core:
    """Stands in for the server module: just the WatchTower config."""

    def __init__(self, cfg):
        self._cfg = cfg

    def _wt_read_config(self):
        return self._cfg


def _gh(repo, **extra):
    conf = {"backend": "github", "github_repo": repo}
    conf.update(extra)
    return conf


def test_issue_labels_for_a_tag_only_queue_is_the_run_label():
    conf = _gh("acme/storefront", auto_drain=False)
    assert issue_flow.issue_labels("STOREFRONT", conf, {"STOREFRONT": conf}) == [issue_flow.RUN_LABEL]


def test_issue_labels_for_an_auto_draining_queue_is_empty():
    conf = _gh("acme/storefront", auto_drain=True)
    assert issue_flow.issue_labels("STOREFRONT", conf, {"STOREFRONT": conf}) == []


def test_issue_labels_adds_the_queue_label_when_the_repo_is_shared():
    a = _gh("acme/storefront", auto_drain=True)
    b = _gh("Acme/Storefront", auto_drain=True, queue_label="team:growth")
    c = _gh("acme/storefront", auto_drain=True, queue_label="*")
    cfg = {"A": a, "B": b, "C": c, "LOCAL": {"backend": "local"}}
    assert issue_flow.issue_labels("A", a, cfg) == ["watchtower:A"]
    assert issue_flow.issue_labels("B", b, cfg) == ["team:growth"]
    # A catch-all queue takes whatever no sibling claimed: no label to add.
    assert issue_flow.issue_labels("C", c, cfg) == []


def test_an_archived_sibling_still_makes_the_repo_shared():
    # WatchTower counts archived queues when it decides a repo is shared
    # (config.github_queues_for_repo), and then divides issues by label. A
    # wizard that ignored the archived sibling opened a test issue no queue
    # owned; it sat on GitHub forever.
    a = _gh("acme/storefront", auto_drain=True)
    old = _gh("acme/storefront", archived=True)
    cfg = {"A": a, "OLD": old}
    assert issue_flow.repo_queues(cfg, "Acme/Storefront") == ["A", "OLD"]
    assert issue_flow.issue_labels("A", a, cfg) == ["watchtower:A"]
    # As the repo's catch-all the new queue needs no routing label at all.
    star = _gh("acme/storefront", auto_drain=True, queue_label="*")
    assert issue_flow.issue_labels("A", star, {"A": star, "OLD": old}) == []
    assert issue_flow.membership_label("A", star, {"A": star, "OLD": old}) == ""


def _repo_view(monkeypatch, private=True, open_issues=0):
    """repo_info with GitHub's answer supplied, so the queue logic is testable."""
    import json as _json
    answer = _json.dumps({
        "nameWithOwner": "acme/storefront", "isPrivate": private, "viewerPermission": "ADMIN",
        "url": "https://github.com/acme/storefront", "hasIssuesEnabled": True,
        "issues": {"totalCount": open_issues},
    })
    monkeypatch.setattr(issue_flow, "_gh", lambda args, timeout=None: (0, answer, ""))
    monkeypatch.setattr(issue_flow, "_safe_known_paths", lambda: [])


def test_repo_info_makes_a_new_queue_the_catch_all_beside_an_archived_one(monkeypatch):
    _repo_view(monkeypatch)
    cfg = {"OLD": _gh("acme/storefront", archived=True), "OTHER": _gh("acme/billing")}
    info = issue_flow.repo_info(_Core(cfg), "acme/storefront")
    # The archived queue is not offered as "already set up"...
    assert info["existing_queues"] == []
    assert info["dormant_queues"] == ["OLD"]
    # ...but the new queue must be the catch-all to receive plain issues.
    assert info["queue_label"] == "*"
    assert "OLD" in info["taken_queues"]


def test_repo_info_leaves_the_label_alone_on_an_unshared_repo(monkeypatch):
    _repo_view(monkeypatch)
    info = issue_flow.repo_info(_Core({"OTHER": _gh("acme/billing")}), "acme/storefront")
    assert (info["queue_label"], info["dormant_queues"], info["existing_queues"]) == ("", [], [])
    live = issue_flow.repo_info(_Core({"LIVE": _gh("acme/storefront")}), "acme/storefront")
    assert live["existing_queues"] == ["LIVE"]
    assert live["queue_label"] == ""


def test_test_issue_creates_every_label_it_is_about_to_use(monkeypatch):
    calls = []

    def fake_gh(args, timeout=None):
        calls.append(list(args))
        if args[:2] == ["issue", "create"]:
            return 0, "https://github.com/acme/storefront/issues/7\n", ""
        return 0, "", ""
    monkeypatch.setattr(issue_flow, "_gh", fake_gh)
    a = _gh("acme/storefront", auto_drain=False)
    cfg = {"A": a, "OLD": _gh("acme/storefront", archived=True)}
    out = issue_flow.create_test_issue(_Core(cfg), "a")
    assert out["number"] == 7 and out["ref"] == "A-7"
    assert out["labels"] == [issue_flow.RUN_LABEL, "watchtower:A"]
    created = [c[2] for c in calls if c[:2] == ["label", "create"]]
    # gh refuses an issue carrying a label the repo does not have.
    assert created == [issue_flow.RUN_LABEL, "watchtower:A"]
    create = next(c for c in calls if c[:2] == ["issue", "create"])
    assert create.count("--label") == 2


def test_a_closed_issue_reads_as_done_with_the_agents_closing_note():
    issue = {"state": "CLOSED", "comments": [
        {"body": "first thoughts"},
        {"body": "WatchTower resolution: Pipeline test passed"},
    ]}
    assert issue_flow.closed_issue_stage(issue) == {"stage": "done", "summary": "Pipeline test passed"}
    assert issue_flow.closed_issue_stage({"state": "CLOSED", "comments": []}) == {"stage": "done", "summary": ""}
    assert issue_flow.closed_issue_stage({"state": "OPEN", "comments": []}) == {}
    assert issue_flow.closed_issue_stage(None) == {}


class _ListCore(_Core):
    def __init__(self, cfg, items):
        super().__init__(cfg)
        self._items = items

    def _ux_fixes_list_items_cached(self, status_filter=None, lane_filter=None, fresh=False):
        return list(self._items)


def test_a_ticket_gone_from_the_queue_list_is_checked_against_github(monkeypatch):
    # After the agent closes the issue the open-issue list drops the ticket.
    # Reading that as "not picked up yet" sent the wizard's timeline back to
    # its first step just as the run succeeded.
    calls = []

    def fake_gh(args, timeout=None):
        calls.append(list(args))
        return 0, '{"state": "CLOSED", "comments": [{"body": "WatchTower resolution: done and dusted"}]}', ""
    monkeypatch.setattr(issue_flow, "_gh", fake_gh)
    issue_flow._issue_state_memo.clear()
    core = _ListCore({"A": _gh("acme/storefront", auto_drain=True)}, [])
    out = issue_flow.test_status(core, "a", 7)
    assert (out["stage"], out["summary"], out["ref"]) == ("done", "done and dusted", "A-7")
    # Closed is remembered: polling again costs no second gh call.
    assert issue_flow.test_status(core, "a", 7)["stage"] == "done"
    assert len(calls) == 1


def test_a_listed_ticket_never_costs_a_github_call(monkeypatch):
    def boom(args, timeout=None):
        raise AssertionError("gh must not be called for a ticket the queue lists")
    monkeypatch.setattr(issue_flow, "_gh", boom)
    row = {"ref": "A-7", "status": "in_progress", "claimed_session_id": "s1"}
    core = _ListCore({"A": _gh("acme/storefront", auto_drain=True)}, [row])
    out = issue_flow.test_status(core, "A", 7)
    assert (out["stage"], out["session_id"]) == ("working", "s1")


def test_an_unlisted_open_issue_is_still_just_sent(monkeypatch):
    monkeypatch.setattr(issue_flow, "_gh", lambda args, timeout=None: (0, '{"state": "OPEN", "comments": []}', ""))
    issue_flow._issue_state_memo.clear()
    core = _ListCore({"A": _gh("acme/storefront", auto_drain=True)}, [])
    assert issue_flow.test_status(core, "A", 8)["stage"] == "sent"


def test_awaiting_answer_reads_as_blocked():
    row = {"ref": "A-1", "status": "awaiting_answer", "block_question": "Which one?"}
    out = issue_flow.derive_stage(row, auto_drain=True, grace_s=180)
    assert (out["stage"], out["question"]) == ("blocked", "Which one?")


def test_flow_row_carries_the_labels_the_wizard_puts_in_its_snippets():
    conf = _gh("acme/storefront", desired_workers=2)
    row = issue_flow._flow_row("storefront", conf, {"storefront": conf})
    assert row["queue"] == "STOREFRONT"
    assert row["mode"] == "labeled"
    assert row["workers"] == 2
    assert row["grace_s"] == issue_flow.DEFAULT_GRACE_S
    assert row["labels"] == [issue_flow.RUN_LABEL]


# -- stage derivation -----------------------------------------------------

NOW = 1_800_000_000.0
JUST_NOW = "2027-01-15T08:00:00Z"   # == NOW


def _stage(item, auto_drain=True, grace_s=180, now=NOW):
    return issue_flow.derive_stage(item, auto_drain=auto_drain, grace_s=grace_s, now=now)


def test_created_at_fixture_matches_now():
    assert issue_flow._parse_iso(JUST_NOW) == NOW


def test_an_issue_the_queue_has_not_listed_yet_is_sent():
    assert _stage(None) == {"stage": "sent"}


def test_a_new_issue_waits_out_the_grace_period():
    out = _stage({"ref": "S-1", "status": "open", "created_at": JUST_NOW}, now=NOW + 37)
    assert out["stage"] == "grace"
    assert out["grace_remaining_s"] == 143
    assert out["ref"] == "S-1"


def test_past_grace_or_explicitly_requested_is_queued():
    assert _stage({"status": "open", "created_at": JUST_NOW}, now=NOW + 181)["stage"] == "queued"
    assert _stage({"status": "open", "created_at": JUST_NOW, "run_requested": True},
                  auto_drain=False)["stage"] == "queued"
    assert _stage({"status": "open", "created_at": JUST_NOW, "auto_eligible": True})["stage"] == "queued"


def test_an_issue_nothing_will_start_is_parked_with_the_reason():
    off = _stage({"status": "open", "created_at": JUST_NOW}, auto_drain=False)
    assert off["stage"] == "parked"
    assert issue_flow.RUN_LABEL in off["reason"]
    held = _stage({"status": "open", "created_at": JUST_NOW, "no_auto_drain": True})
    assert held["stage"] == "parked"
    assert issue_flow.HOLD_LABEL in held["reason"]


def test_claimed_blocked_and_closed_tickets():
    working = _stage({"status": "in_progress", "claimed_session_id": "sid-1", "claimed_by": "w1"})
    assert (working["stage"], working["session_id"], working["worker"]) == ("working", "sid-1", "w1")
    blocked = _stage({"status": "in_progress", "needs_input": True, "block_question": "Which branch?"})
    assert (blocked["stage"], blocked["question"]) == ("blocked", "Which branch?")
    done = _stage({"status": "closed", "resolution": {"summary": "Pipeline test passed", "session_id": "sid-2"}})
    assert (done["stage"], done["summary"], done["session_id"]) == ("done", "Pipeline test passed", "sid-2")


# -- local repo discovery -------------------------------------------------

def _checkout(path, url):
    (path / ".git").mkdir(parents=True)
    (path / ".git" / "config").write_text(
        '[core]\n\tbare = false\n[remote "upstream"]\n\turl = https://github.com/other/thing.git\n'
        f'[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        '[branch "main"]\n\tremote = origin\n'
    )
    return path


def test_origin_slug_reads_the_origin_remote_without_running_git(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("origin_slug must not spawn a process")
    monkeypatch.setattr(issue_flow.subprocess, "run", boom)
    repo = _checkout(tmp_path / "storefront", "git@github.com:acme/storefront.git")
    assert issue_flow.origin_slug(str(repo)) == "acme/storefront"
    assert issue_flow.origin_slug(str(tmp_path / "missing")) == ""


def test_origin_slug_follows_a_linked_worktree_to_the_main_config(tmp_path):
    main = _checkout(tmp_path / "main", "https://github.com/acme/storefront.git")
    wt_gitdir = main / ".git" / "worktrees" / "feature"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "feature"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    assert issue_flow.origin_slug(str(tree)) == "acme/storefront"


def test_origin_slug_notices_a_changed_remote(tmp_path):
    repo = _checkout(tmp_path / "storefront", "https://github.com/acme/storefront.git")
    assert issue_flow.origin_slug(str(repo)) == "acme/storefront"
    _config = repo / ".git" / "config"
    _config.write_text('[remote "origin"]\n\turl = https://github.com/acme/renamed-storefront.git\n')
    assert issue_flow.origin_slug(str(repo)) == "acme/renamed-storefront"


def test_known_repos_lists_only_github_checkouts_once_each(tmp_path):
    a = _checkout(tmp_path / "a", "https://github.com/acme/a.git")
    b = _checkout(tmp_path / "b", "https://gitlab.com/acme/b.git")
    plain = tmp_path / "plain"
    plain.mkdir()
    rows = issue_flow.known_repos([str(a), str(b), str(plain), str(a), str(tmp_path / "gone")])
    assert [(r["name"], r["github_repo"]) for r in rows] == [("a", "acme/a")]


def test_inspect_repo_path_flags_a_checkout_of_a_different_repo(tmp_path):
    repo = _checkout(tmp_path / "storefront", "https://github.com/acme/storefront.git")
    same = issue_flow.inspect_repo_path(str(repo), "Acme/Storefront")
    assert (same["exists"], same["is_git"], same["matches"]) == (True, True, True)
    other = issue_flow.inspect_repo_path(str(repo), "acme/billing")
    assert other["matches"] is False
    assert other["origin"] == "acme/storefront"
    gone = issue_flow.inspect_repo_path(str(tmp_path / "nope"), "acme/storefront")
    assert gone["exists"] is False


# -- operations refuse bad input before reaching GitHub -------------------

def _no_gh(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must be rejected before any gh call")
    monkeypatch.setattr(issue_flow, "_gh", boom)


def test_prepare_rejects_unknown_queues_modes_and_local_queues(monkeypatch):
    _no_gh(monkeypatch)
    core = _Core({"LOCAL": {"backend": "local"}, "GH": _gh("acme/storefront")})
    with pytest.raises(issue_flow.FlowError):
        issue_flow.prepare(core, "NOPE", "labeled")
    with pytest.raises(issue_flow.FlowError):
        issue_flow.prepare(core, "LOCAL", "labeled")
    with pytest.raises(issue_flow.FlowError):
        issue_flow.prepare(core, "GH", "everything")
    with pytest.raises(issue_flow.FlowError):
        issue_flow.prepare(core, "../etc", "labeled")


def test_test_issue_rejects_unknown_queues(monkeypatch):
    _no_gh(monkeypatch)
    with pytest.raises(issue_flow.FlowError):
        issue_flow.create_test_issue(_Core({}), "NOPE")
    with pytest.raises(issue_flow.FlowError):
        issue_flow.repo_info(_Core({}), "not a repo")


def test_the_test_issue_tells_the_agent_to_change_nothing():
    body = issue_flow.TEST_BODY.lower()
    assert "pipeline test passed" in body
    assert "do not" in body or "no code" in body or "without" in body


# -- wiring ---------------------------------------------------------------

def test_server_routes_exist_and_writes_are_local_only():
    source = (PROJECT_ROOT / "server.py").read_text(encoding="utf-8")
    for route in ('"/api/issue-flow/status"', '"/api/issue-flow/repo"', '"/api/issue-flow/test"',
                  '"/api/issue-flow/prepare"'):
        assert route in source, route
    start = source.index('if path in ("/api/issue-flow/prepare", "/api/issue-flow/test"):')
    block = source[start:start + 900]
    # Opening issues and labelling a repo from a phone-relayed request is refused.
    assert "is_remote_request" in block
    assert "403" in block


def test_wizard_is_loaded_and_reachable_from_the_main_ui():
    static = PROJECT_ROOT / "static"
    index = (static / "index.html").read_text(encoding="utf-8")
    q2 = (static / "q2.html").read_text(encoding="utf-8")
    rail = (static / "app-rail.js").read_text(encoding="utf-8")
    for page in (index, q2):
        assert "/static/issue-flow.js" in page
        assert "/static/issue-flow.css" in page
        assert "data-issue-flow-open" in page
    # Rail button: opens in place where the wizard is loaded, else hands off.
    assert "cccIssueFlow.open()" in rail
    assert "/?issue-flow=1" in rail


def test_wizard_never_posts_a_queue_with_auto_drain_on():
    js = (PROJECT_ROOT / "static" / "issue-flow.js").read_text(encoding="utf-8")
    # The queue is created parked; drain is switched on only after existing
    # issues are held back, as a separate step.
    create = js[js.index("queue: () => post('/api/queue/config'"):]
    create = create[:create.index("prepare: ()")]
    assert "auto_drain: false" in create
    order = js[js.index("const plan = ["):js.index("// A retry keeps")]
    assert order.index("'queue'") < order.index("'prepare'") < order.index("'drain'")


def test_handoff_lands_on_the_queues_board():
    js = (PROJECT_ROOT / "static" / "issue-flow.js").read_text(encoding="utf-8")
    q2 = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert "window.cccQ2SelectQueue = function" in q2
    assert "cccSwitchCoreApp('queues')" in js
    assert "cccQ2SelectQueue(name)" in js
