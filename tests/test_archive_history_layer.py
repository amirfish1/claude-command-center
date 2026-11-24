import contextlib
import gzip
import json
import zlib

import pytest

import server
from ccc_server import archive_history_layer as layer, archive_store


def inventory(*session_ids):
    return [(sid, archive_store.identity({"session_id": sid}), "", f"digest-{sid}")
            for sid in session_ids]


def test_wide_order_spans_require_opt_in_and_a_matching_saved_order():
    previous = tuple(map(str, range(25000)))
    # Move 1,800 rows as a group while preserving long unchanged runs.
    current = previous[::7][:1800]
    moved = set(current)
    current += tuple(sid for sid in previous if sid not in moved)
    arguments = (previous, current, "before", "after", {"conversations": []})
    assert layer.delta_payload(*arguments)["ids"] == current
    assert layer.delta_payload(*arguments, wide_order_spans=True, saved_order=False)["ids"] == current
    patch = layer.delta_payload(*arguments, wide_order_spans=True)
    assert patch["ids"] == []
    spans = patch["order_spans"]
    assert 256 < len(spans) <= 4096
    assert tuple(sid for start, count in spans for sid in previous[start:start + count]) == current
    reverse = previous[::-1]
    assert layer.delta_payload(previous, reverse, "before", "after", {},
                               wide_order_spans=True)["ids"] == reverse


def test_full_layer_can_use_wide_spans_against_its_embedded_order(monkeypatch):
    from collections import OrderedDict
    from dataclasses import replace

    previous = tuple(map(str, range(4300)))
    current = previous[:1024:2] + previous[1:1024:2] + previous[1024:]
    base_inputs = layer.read_inputs(inventory(*previous), {}, {}, (), overlay_maps=(), edges=())
    current_inputs = replace(base_inputs, order=current)
    metadata = {"count": len(previous), "window": "all"}
    rows = [{"session_id": sid} for sid in previous]
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    basis = layer.make_basis(base_inputs, (), {}, dict(metadata, conversations=rows))
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE", {"views": OrderedDict(), "loaded": True})
    monkeypatch.setattr(server, "_archive_history_raw_basis", lambda *args: basis)
    monkeypatch.setattr(server, "_archive_history_select_basis", lambda *args: basis)
    monkeypatch.setattr(server, "_archive_history_read_cut",
                        lambda *args, **kwargs: contextlib.nullcontext((current_inputs, {"tickets": None})))
    view = layer.View(current_inputs, frozenset(), {}, "current", metadata, {})
    monkeypatch.setattr(server, "_archive_history_project_cut", lambda *args, **kwargs: (view, [], []))
    monkeypatch.setattr(server, "_archive_history_should_rebase", lambda *args: False)
    monkeypatch.setattr(server, "_trace_row_flicker", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_archive_list_ticket_effects", lambda *args, **kwargs: None)

    for since, wide, saved_order in (("", False, ""), ("", True, ""), ("old", True, "stale"),
                                     (basis.token, True, "stale")):
        body, encoding, _ = server._archive_history_response(
            "scope", {}, since, saved_order, ("gzip",), phases={}, check_client=None, wide_order_spans=wide)
        assert encoding == "gzip"
        document = json.loads(gzip.decompress(body))
        if since == basis.token:
            assert document["ids"] == list(current)
            continue
        patch = document["patch"]
        assert document["base"]["order_token"] == basis.order_token
        if wide:
            assert patch["base_order"] == basis.order_token
            assert patch["ids"] == []
            assert tuple(sid for start, count in patch["order_spans"]
                         for sid in previous[start:start + count]) == current
        else:
            assert patch["ids"] == list(current)


def test_seven_day_compatibility_uses_the_refresh_encoding():
    from types import SimpleNamespace

    rows = [{"session_id": str(i), "turn_driver": {"kind": "owner", "age": i}}
            for i in range(24)]
    sources = {row["session_id"]: ("hot", layer.hot_row_token(row)) for row in rows}
    basis = SimpleNamespace(inputs=SimpleNamespace(sources=sources))
    durable = json.loads(json.dumps(rows, sort_keys=True))
    assert durable == rows
    assert not server._archive_seven_day_compatible(basis, durable)
    assert server._archive_seven_day_compatible(basis, rows)
    changed = [dict(row, modified=42) if i < 4 else row for i, row in enumerate(rows)]
    assert server._archive_seven_day_compatible(basis, changed)


@pytest.mark.parametrize("retain_persisted", [False, True])
def test_refresh_publisher_receives_accepted_row_encoding(tmp_path, monkeypatch, retain_persisted):
    import threading
    from types import SimpleNamespace

    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_PUBLISH_LOCK", threading.Lock())
    monkeypatch.setattr(server, "_ARCHIVE_SEVEN_DAY_PUBLISH_LAST_START", None)
    monkeypatch.setattr(server, "_archive_refresh_result_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_serve_version", 0)
    monkeypatch.setattr(server, "record_diag", lambda *args: None)
    key = server._archive_response_cache_key()
    generation = server._archive_serve_generation
    incoming = [{"session_id": "kept", "nested": {"z": 1, "a": 2}},
                {"session_id": "changed", "modified": 2}]
    if retain_persisted:
        previous = [{"session_id": "kept", "nested": {"a": 2, "z": 1}},
                    {"session_id": "changed", "modified": 1}]
        assert server._archive_serve_cache_store(key, previous, generation, hot_selected=True)
    refresh = tmp_path / "refresh.json"
    server._write_archive_refresh_result(
        refresh, {"ok": True, "key": key, "rows": incoming}, basis=set())
    result, _ = server._read_archive_refresh_result(refresh)
    accepted = result["rows"]
    assert server._archive_serve_cache_store(
        key, accepted, generation, transfer_rows=result["transfer_rows"], hot_selected=True)
    observed = []

    def publish(args, **kwargs):
        assert args[-3:-1] == ["--seven-day", "--refresh"]
        source, _ = server._read_archive_refresh_result(args[-1])
        observed.append((args[-1], source["key"], source["rows"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(server.subprocess, "run", publish)
    server._archive_history_start_publication(
        refresh_source=refresh, refresh_snapshot=(accepted, result["transfer_rows"]), seven_day=True)
    # The publisher releases its lock only after cleaning up both handoffs.
    with server._ARCHIVE_HISTORY_PUBLISH_LOCK:
        assert len(observed) == 1
    selected, observed_key, rows = observed[0]
    assert observed_key == key
    assert rows == accepted
    assert list(rows[0]["nested"]) == (["a", "z"] if retain_persisted else ["z", "a"])
    assert (selected == f"{refresh}.sidebar") is not retain_persisted
    assert list(tmp_path.iterdir()) == [refresh]


def test_seven_day_basis_preserves_current_rows_order_and_token(tmp_path, monkeypatch):
    import http.client
    import threading

    from ccc_server import continuation_topics, cpu_priority, ship_graph

    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(server, "_ARCHIVE_SEVEN_DAY_BASIS", (None, None))
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_delta_ctx_remember", lambda *args: None)
    monkeypatch.setattr(server, "_archive_list_ticket_effects", lambda *args, **kwargs: None)
    now = 1_790_000_000
    monkeypatch.setattr(server.time, "time", lambda: now)
    monkeypatch.setattr(server, "_load_usage_limit_resumes", dict)
    monkeypatch.setattr(server, "_load_session_name_overrides", dict)
    monkeypatch.setattr(server, "_archive_list_row_overlays", lambda names=None: {})
    monkeypatch.setattr(server._idea_tickets, "_enabled", lambda: False)
    monkeypatch.setattr(server._session_graph, "parent_snapshot", dict)
    monkeypatch.setattr(server._session_graph, "driver_snapshot", dict)
    monkeypatch.setattr(server._session_graph, "projection_revision", lambda: 1)
    monkeypatch.setattr(continuation_topics, "revision", lambda: 2)
    monkeypatch.setattr(continuation_topics, "archive_connection", lambda: None)
    monkeypatch.setattr(continuation_topics, "archive_projection_inputs",
                        lambda *args, **kwargs: (({}, {}, {}), (), ()))
    monkeypatch.setattr(continuation_topics, "attach_archive_topics", lambda *args, **kwargs: None)
    monkeypatch.setattr(continuation_topics, "attach_topics", lambda *args, **kwargs: None)
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    scope = server._archive_response_cache_key()
    first = {"session_id": "first", "modified": now - 1}
    second = {"session_id": "second", "modified": now - 2}
    old = {"session_id": "outside", "modified": now - 30 * 86400}
    original = [first, second, old]
    generation = server._archive_serve_generation
    database = tmp_path / "topics.sqlite"
    database.touch()
    monkeypatch.setattr(ship_graph, "_get_db_path", lambda: database)
    monkeypatch.setattr(cpu_priority, "set_background_priority", lambda *args: None)
    monkeypatch.setattr(server, "_WT_DISPLAY_SNAPSHOT_ONLY", False)
    monkeypatch.setattr(server, "_load_conv_meta_cache", lambda **kwargs: None)
    monkeypatch.setattr(server._session_graph, "load", lambda: None)
    monkeypatch.setattr(server, "_archive_list_source_overlays", lambda rows, *args: (rows, []))
    refresh = tmp_path / "refresh.json"
    refresh.write_text(json.dumps({"ok": True, "key": scope, "rows": original}))
    assert server._archive_history_publisher_main(seven_day=True, refresh_source=refresh)
    updated = [dict(second, name="Updated"), {"session_id": "new", "modified": now}, old]
    assert server._archive_serve_cache_store(scope, updated, generation, hot_selected=True)
    updated = server._archive_serve_cache[scope]["rows"]
    delta = server._archive_list_delta_state(scope, updated, "7d", True)
    projections = []

    def attach_archive_topics(rows, **kwargs):
        projections.append(rows)
        if len(projections) == 1:
            raise continuation_topics.StaleArchiveTopicCut()

    monkeypatch.setattr(continuation_topics, "attach_archive_topics", attach_archive_topics)
    phases = {}
    body, encoding, etag = server._archive_seven_day_response(
        scope, updated, True, delta, ("gzip",), phases)
    assert len(projections) == 2
    assert "stale_topic_projection" in phases
    assert encoding == "gzip"
    assert etag == '"d-' + delta["token"] + '"'
    document = json.loads(gzip.decompress(body))
    patch = document["patch"]
    assert document["history_layer"] == 1
    assert document["base"]["window"] == patch["window"] == "7d"
    assert patch["delta_token"] == delta["token"]
    assert patch["ids"] == ["second", "new"]
    expected = server._archive_list_payload(updated, window="7d", now=now, cached=True)
    actual = [dict(zip(patch["row_layouts"][slot], values))
              for slot, values in patch["conversations"]]
    assert actual == expected["conversations"]
    assert {key: patch[key] for key in ("count", "total_count", "queue_health")} == {
        key: expected[key] for key in ("count", "total_count", "queue_health")
    }
    mismatched = dict(delta, topic_revision=3)
    assert server._archive_seven_day_response(scope, updated, True, mismatched, ("gzip",), {}) is None

    monkeypatch.setattr(server, "_archive_list_disk_candidate", lambda *args: None)
    monkeypatch.setattr(server, "_archive_list_source_rows_cached",
                        lambda *args, **kwargs: (updated, True, delta["ver"]))
    monkeypatch.setattr(server, "_ARCHIVE_LIST_BODY_CACHE", server.ByteBudgetCache(1 << 20, 8))
    url = "/api/conversations/list?window=7d&stale_ok=1&row_layouts=1&history_layer=1"
    with server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        try:
            connection.request("GET", url, headers={"Accept-Encoding": "gzip"})
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Encoding") == "gzip"
            assert response.getheader("ETag") == etag
            assert json.loads(gzip.decompress(response.read())) == document
            connection.request("GET", url + "&since=" + delta["token"])
            response = connection.getresponse()
            assert response.status == 304
            assert response.read() == b""
        finally:
            connection.close()
            httpd.shutdown()
            thread.join(timeout=5)


def test_effect_scope_keeps_ticket_members_pending_and_lineage():
    from types import SimpleNamespace

    sources = inventory("lead", "member", "pending", "unrelated-skip", "linked-skip",
                        "old-child", "new-child", "unrelated")
    previous = layer.read_inputs(sources, {}, {}, (), overlay_maps=(),
                                 edges=(("old-child", "lead"),))
    current = layer.read_inputs(sources, {}, {}, (), overlay_maps=(),
                                edges=(("linked-skip", "lead"), ("new-child", "member")))
    state = {"sessions": {
        "lead": {"ref": "TEST-1", "kind": "started"},
        "member": {"ref": "TEST-1", "kind": "child"},
        "unrelated-skip": {"skip": "not a UI spawn"},
        "linked-skip": {"skip": "not a UI spawn"},
    }, "pending": {"pending": {}, "absent": {}}}

    assert server._archive_history_effect_ids(
        current, SimpleNamespace(inputs=previous), {"tickets": state},
    ) == {"lead", "member", "pending", "linked-skip", "old-child", "new-child"}


def test_scope_guard_tracks_output_order_separately_from_content():
    previous = layer.read_inputs(inventory("a", "b"), {}, {}, (),
                                 overlay_maps=({},), edges=())
    current = layer.read_inputs(inventory("b", "a"), {}, {}, (),
                                overlay_maps=({},), edges=())
    assert current.order == ("b", "a")
    assert layer.candidates(previous, current) == set()


def test_scope_guard_tracks_native_overlay_values_and_ancestor_closure():
    source = inventory("a", "b", "c", "d")
    edges = (("a", "b"), ("a", "c"))
    previous = layer.read_inputs(source, {}, {}, (), overlay_maps=({"c": False},),
                                 edges=edges)
    current = layer.read_inputs(source, {}, {}, (), overlay_maps=({"c": 0},),
                                edges=edges)
    assert layer.candidates(previous, current) == {"a", "b", "c"}
    assert layer.candidates(previous, current, max_rows=2) is None


def test_source_guard_refuses_shrinking_corpus_by_default():
    old = layer.read_inputs(inventory("a", "b"), {}, {}, (),
                            overlay_maps=(), edges=())
    deleted = layer.read_inputs(inventory("a"), {}, {}, (),
                                overlay_maps=(), edges=())
    assert layer.candidates(old, deleted) is None


def test_hot_replacement_requires_exact_identity_and_a_content_token():
    source = inventory("a", "b")
    assert layer.read_inputs(source, {"b": {"session_id": "b"}}, {}, (),
                             overlay_maps=(), edges=()) is None
    different = {"session_id": "b", "source": "new-engine"}
    assert layer.read_inputs(source, {"b": different}, {"b": "token"}, (),
                             overlay_maps=(), edges=()) is None
    valid = layer.read_inputs(source, {"b": {"session_id": "b"}}, {"b": "token"}, (),
                              overlay_maps=(), edges=())
    assert valid.order == ("a", "b")
    assert valid.sources["b"] == ("hot", "token")


def test_hot_only_membership_keeps_order_edges_and_requires_tokens(monkeypatch):
    def unexpected_identity(*args):
        pytest.fail("A hot-only scope has no stored identity to compare")

    monkeypatch.setattr(archive_store, "identity", unexpected_identity)
    hot = {"b": {"session_id": "b", "parent_session_id": "a"},
           "a": {"session_id": "a", "continuation_origin": "outside"}}
    tokens = {"a": "token-a", "b": "token-b", "outside": "unused"}
    inputs = layer.read_inputs((), hot, tokens, (), overlay_maps=(),
                               edges=(("external", "a"),))
    assert inputs.order == ("b", "a")
    assert inputs.edges == (("external", "a"), ("b", "a"), ("a", "outside"))
    assert inputs.sources == {"b": ("hot", "token-b"), "a": ("hot", "token-a")}
    assert layer.read_inputs((), hot, {"a": "token-a"}, (), overlay_maps=(), edges=()) is None


def test_pinned_membership_reuses_only_the_captured_cut(monkeypatch):
    source = tuple(inventory("older", "active"))
    hot = {"active": {"session_id": "active", "parent_session_id": "older"}}
    tokens = {"active": "hot-token"}

    def read(cut, changed_hot=hot, changed_tokens=tokens, extra=(), sidecars=(), **kwargs):
        return layer.read_inputs(cut, changed_hot, changed_tokens, extra,
                                 overlay_maps=sidecars, edges=(), pinned_hot=True, **kwargs)

    baseline = read(source)
    assert baseline.edges == (("active", "older"),)
    overlay = {"session_id": "overlay", "parent_session_id": "active"}
    repeated = read(source, extra=[overlay], sidecars=({"active": True},))
    assert repeated.order == ("older", "active", "overlay")
    assert repeated.edges == baseline.edges + (("overlay", "active"),)
    assert repeated.overlays == ({"active": "b1"},)
    assert read(source).sources is baseline.sources
    assert "overlay" not in baseline.sources

    replaced = read(source, {"active": {"session_id": "active"}},
                    {"active": "new-token"})
    assert replaced.sources["active"] == ("hot", "new-token")
    assert replaced.edges == ()
    assert read(tuple(inventory("older")), removed={"older"}).order == ("active",)
    wrong_identity = (("active", "wrong-identity", "", "digest"),)
    assert read(wrong_identity) is None


def test_new_hot_cut_reuses_only_unchanged_row_guards(monkeypatch):
    cut = tuple(inventory("a", "b"))
    first = {"a": {"session_id": "a", "parent_session_id": "b"},
             "b": {"session_id": "b"}}
    second = dict(first, b={"session_id": "b", "continuation_origin": "older"})

    def read(inventory_rows, hot):
        tokens = {sid: layer.hot_row_token(row) for sid, row in hot.items()}
        return layer.read_inputs(inventory_rows, hot, tokens, (), overlay_maps=(),
                                 edges=(), pinned_hot=True)

    assert read(cut, first).edges == (("a", "b"),)
    original_identity = archive_store.identity
    checked = []

    def record_identity(row):
        checked.append(row["session_id"])
        return original_identity(row)

    monkeypatch.setattr(archive_store, "identity", record_identity)
    updated = read(cut, second)
    assert checked == ["b"]
    assert updated.order == ("a", "b")
    assert updated.edges == (("a", "b"), ("b", "older"))
    wrong = (("a", "wrong-identity", "", "digest-a"), cut[1])
    assert read(wrong, second) is None
    assert checked == ["b"]


def test_history_hot_guard_ignores_top_level_order_across_refresh(monkeypatch):
    persisted = {"all_lane_override": "", "archived": False, "session_id": "a",
                 "subagent_recent": [{"id": "child", "type": "task"}]}
    refreshed = dict(reversed(tuple(persisted.items())))
    refreshed_rows = [refreshed]
    entry = {"rows": [persisted], "ver": 1}
    monkeypatch.setattr(server, "_archive_serve_cache", {"base": entry})
    _, persisted_tokens = server._archive_history_hot_snapshot("base")

    entry.update(rows=refreshed_rows, ver=2, transfer_rows={layer.row_token(refreshed): refreshed})
    _, refreshed_tokens = server._archive_history_hot_snapshot("base")
    assert persisted_tokens == refreshed_tokens
    assert server._archive_history_hot_snapshot("base")[1] == refreshed_tokens

    nested_change = dict(refreshed, subagent_recent=[{"type": "task", "id": "child"}])
    entry.update(rows=[nested_change], ver=3)
    assert server._archive_history_hot_snapshot("base")[1] != refreshed_tokens


def test_hot_encoding_reuses_layout_without_reusing_values():
    cases = (
        {"z": {"b": 1, "a": 2}, "a": "first"},
        {"z": {"a": 2, "b": 1}, "a": "second"},
        {"a": "third", "z": [1, None]},
        {"z": False},
        {},
        {1: "legacy"},
        {True: "legacy"},
    )
    for row in cases:
        assert layer.encode_hot_row(row) == json.dumps(
            dict(sorted(row.items())), separators=(",", ":"),
        )


def test_history_hot_guard_reuses_tokens_for_unchanged_published_rows(monkeypatch):
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_serve_version", 0)
    gen = server._archive_serve_generation
    unchanged = {"session_id": "stable"}
    changed = {"session_id": "changed", "display_name": "Before"}
    assert server._archive_serve_cache_store("base", [unchanged, changed], gen, hot_selected=True)
    _, before = server._archive_history_hot_snapshot("base")

    hashed = []
    original = layer.hot_row_token

    def counted(row):
        hashed.append(row["session_id"])
        return original(row)

    monkeypatch.setattr(layer, "hot_row_token", counted)
    changed = dict(changed, display_name="After")
    assert server._archive_serve_cache_store("base", [dict(unchanged), changed], gen, hot_selected=True)
    _, after = server._archive_history_hot_snapshot("base")
    assert after["stable"] == before["stable"]
    assert after["changed"] != before["changed"]
    assert server._archive_history_hot_snapshot("base")[1] == after
    assert hashed == ["changed"]


def test_history_hot_cut_reuses_capture_and_invalidates_on_lifecycle_change(monkeypatch):
    rows = [{"session_id": "stable", "archived": False}]
    entry = {"rows": rows, "ver": 1}
    monkeypatch.setattr(server, "_archive_serve_cache", {"base": entry})
    monkeypatch.setattr(server, "_archive_serve_version", 1)
    monkeypatch.setattr(server, "_archive_serve_generation", 1)
    monkeypatch.setattr(server, "_archive_serve_refreshing", set())

    cut = server._archive_history_hot_snapshot("base", (rows, 1))
    assert server._archive_history_hot_snapshot("base") is cut
    server._restamp_archive_serve_cache_after_mutation(archived_set={"stable"},
                                                      mutated_sids={"stable"})

    assert cut[0]["stable"]["archived"] is False
    assert server._archive_history_hot_snapshot("base", (rows, 1)) is None
    assert rows[0]["archived"] is False
    current_rows = server._archive_serve_cache["base"]["rows"]
    updated = server._archive_history_hot_snapshot("base", (current_rows, 2))
    assert updated[0]["stable"]["archived"] is True
    assert updated[1]["stable"] != cut[1]["stable"]


def test_scoped_hot_cut_uses_published_tokens_before_copying_rows(monkeypatch):
    from ccc_server import archive_history_layer as layer

    row = {"session_id": "stable", "archived": False}
    rows = [row]
    token = layer.hot_row_token(row)
    entry = {"rows": rows, "ver": 1, "history_hot_tokens": (1, rows, {id(row): token})}
    monkeypatch.setattr(server, "_archive_serve_cache", {"base": entry})

    assert server._archive_history_hot_snapshot("base", (rows, 1), tokens_only=True) == (
        None, {"stable": token})
    assert "history_hot_snapshot" not in entry
    assert server._archive_history_hot_snapshot("base", (rows, 0), tokens_only=True) is None

    entry["history_hot_tokens"] = (1, rows, {id(row): None})
    assert server._archive_history_hot_snapshot("base", (rows, 1), tokens_only=True)[1] == {
        "stable": token}
    assert server._archive_history_hot_snapshot("base")[0] == {"stable": row}
    assert server._archive_history_hot_snapshot("base")[0]["stable"] is not row


@pytest.mark.parametrize("targeted", [False, True])
def test_lifecycle_restamp_rehashes_only_affected_hot_rows(monkeypatch, targeted):
    rows = [
        dict(session_id="stable", archived=False, trashed=False, all_lane_override=""),
        dict(session_id="changed", archived=False, trashed=False, all_lane_override=""),
        dict(session_id="legacy", archived=0, trashed=False, all_lane_override=""),
    ]
    monkeypatch.setattr(server, "_archive_serve_cache", {"base": {"rows": rows, "ver": 1}})
    monkeypatch.setattr(server, "_archive_serve_version", 1)
    monkeypatch.setattr(server, "_archive_serve_generation", 1)
    monkeypatch.setattr(server, "_archive_serve_refreshing", {})
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: ({"changed"}, set()))
    monkeypatch.setattr(server, "_load_session_lane_overrides", lambda: {})
    before = server._archive_history_hot_snapshot("base")

    rehashed = []
    token = layer.hot_row_token

    def record_token(row):
        rehashed.append(row["session_id"])
        return token(row)

    monkeypatch.setattr(layer, "hot_row_token", record_token)
    if targeted:
        server._restamp_archive_serve_cache_after_mutation(archived_set={"changed"},
                                                          mutated_sids={"changed"})
    else:
        server._restamp_archive_serve_cache_after_mutation()
    after = server._archive_history_hot_snapshot("base")

    assert rehashed == (["changed"] if targeted else ["changed", "legacy"])
    assert after[1] == {sid: token(row) for sid, row in after[0].items()}
    assert before[0]["changed"]["archived"] is False
    assert after[0]["changed"]["archived"] is True


def test_history_hot_cut_survives_identical_refresh_but_checks_capture(monkeypatch):
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_serve_version", 0)
    generation = server._archive_serve_generation
    rows = [{"session_id": "first"}, {"session_id": "second"}]
    assert server._archive_serve_cache_store("base", rows, generation, hot_selected=True)
    cut = server._archive_history_hot_snapshot("base", (rows, 1))
    assert cut is not None

    refreshed = [row.copy() for row in rows]
    assert server._archive_serve_cache_store("base", refreshed, generation, hot_selected=True)
    assert server._archive_history_hot_snapshot("base", (refreshed, 1)) is cut
    assert server._archive_history_hot_snapshot("base", (rows, 1)) is None

    reordered = list(reversed(refreshed))
    assert server._archive_serve_cache_store("base", reordered, generation, hot_selected=True)
    new_cut = server._archive_history_hot_snapshot("base", (reordered, 2))
    assert new_cut is not cut
    assert list(new_cut[0]) == ["second", "first"]
    assert new_cut[1] == cut[1]


def test_history_hot_guard_uses_durable_tokens_only_before_lifecycle_changes(tmp_path, monkeypatch):
    key = server._archive_response_cache_key()
    path = tmp_path / 'archive.json'
    base = {'archived': False, 'trashed': False, 'all_lane_override': '',
            'subagent_recent': [{'type': 'task', 'id': 'child'}]}
    archive_store.save(path, {
        'schema_version': server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
        'build_id': server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
        'entries': {key: {'cached_at': 10, 'conversations': [
            dict(base, session_id='stable', display_name='Quoted "title" — résumé'),
            dict(base, session_id='archived'),
            dict(base, session_id='moved'),
            dict(base, session_id='deleted', engine='codex'),
        ]}},
    }, complete=True)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_FILE', path)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE_LOADED', False)
    monkeypatch.setattr(server, '_ARCHIVE_RESPONSE_CACHE', {})
    monkeypatch.setattr(server, '_archive_serve_cache', {})
    monkeypatch.setattr(server, '_archive_serve_version', 0)
    monkeypatch.setattr(server, '_archive_refresh_load_blocked', lambda now: True)
    monkeypatch.setattr(server, '_load_conversation_lifecycle_sets', lambda: ({'archived'}, set()))
    monkeypatch.setattr(server, '_codex_deleted_thread_ids', lambda: {'deleted'})
    monkeypatch.setattr(server, '_load_session_lane_overrides', lambda: {'moved': 'other'})
    hashed = []
    original = layer.hot_row_token

    def counted(row):
        hashed.append(row['session_id'])
        return original(row)

    monkeypatch.setattr(layer, 'hot_row_token', counted)
    server._archive_serve_hot_rows_versioned(key, {}, defer_cold=True, copy_rows=False)
    rows, tokens = server._archive_history_hot_snapshot(key)

    assert list(rows) == ['stable', 'archived', 'moved']
    assert rows['archived']['archived'] is True
    assert rows['moved']['all_lane_override'] == 'other'
    assert tokens == {sid: original(row) for sid, row in rows.items()}
    assert hashed == ['archived', 'moved']


def test_layer_never_adopts_duplicate_overlay_identity():
    source = inventory("a")
    assert layer.read_inputs(source, {}, {}, [{"session_id": "a"}],
                             overlay_maps=(), edges=()) is None


def test_large_published_basis_spends_more_compression_than_a_delta(monkeypatch):
    levels = []

    def encode(raw, *, level=3):
        levels.append(level)
        return b"frame"

    monkeypatch.setattr(layer.list_encoding, "_zstd", encode)
    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    row = {"session_id": "a", "title": "a" * (8 << 20)}
    assert layer.make_basis(inputs, (), {}, {"conversations": [row]}).frames["zstd"] == b"frame"
    assert layer.delta_representation({"conversations": [row]}, "zstd") == b"frame"
    assert levels == [6, 3]


def test_persisted_basis_is_scoped_and_corruption_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: b"frame:" + raw)
    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=({"a": False},),
                               edges=(("a", "root"),))
    row = {"session_id": "a"}
    basis = layer.make_basis(inputs, {"a"}, {"a": 12}, {"conversations": [row]},
                             effect_rows={"a": row})
    target = tmp_path / "sidebar-layer"
    assert layer.write_basis(target, "current", basis)
    assert layer.load_basis(target, "current") == basis
    assert layer.load_basis(target, "different") is None
    saved = target.read_bytes()
    target.write_bytes(saved[: layer._HEADER.size - 1])
    assert layer.load_basis(target, "current") is None
    target.write_bytes(saved[:-1] + b"X")
    assert layer.load_basis(target, "current") is None


def test_persisted_basis_replays_and_validates_when_the_current_projection_agrees(tmp_path, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: b"frame:" + raw)
    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    metadata = {"ok": True, "window": "all", "count": 1}
    row = {"session_id": "a"}
    path = tmp_path / "archive.json"
    key = server._archive_response_cache_key()
    scope = server._archive_history_scope(key)
    basis = layer.make_basis(inputs, set(), {}, dict(metadata, conversations=[row]))
    assert layer.write_basis(layer.basis_path(path), scope, basis)

    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE",
                        {"basis": None, "views": OrderedDict(), "loaded": False})
    monkeypatch.setattr(server, "_archive_history_read_cut",
                        lambda *args, **kwargs: contextlib.nullcontext((inputs, {"tickets": None})))
    monkeypatch.setattr(server, "_trace_row_flicker", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_archive_list_ticket_effects", lambda *args, **kwargs: None)
    etag = '"d-' + basis.token + '"'

    def checked_projection(cut, previous, **kwargs):
        assert previous is not None
        return layer.View(inputs, frozenset(), {}, "candidate", metadata, {}), [], []

    monkeypatch.setattr(server, "_archive_history_project_cut", checked_projection)
    assert server._archive_history_response(key, {}, basis.token, basis.order_token, ("gzip",),
                                           phases={}, check_client=None) == (None, None, etag)
    assert server._archive_history_response(key, {}, "", "", ("zstd", "gzip"),
                                           phases={}, check_client=None) == (basis.frames["zstd"], "zstd", etag)
    body, encoding, validator = server._archive_history_response(
        key, {}, "", "", ("gzip",), phases={}, check_client=None)
    assert (encoding, validator) == ("gzip", etag)
    assert json.loads(gzip.decompress(body))["patch"]["same_order"] is True


def test_cold_rebased_basis_defers_raw_load_and_honors_saved_validators(tmp_path, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    metadata = {"window": "all", "count": 1}
    row = {"session_id": "a"}
    path = tmp_path / "archive.json"
    key = server._archive_response_cache_key()
    scope = server._archive_history_scope(key)
    raw = layer.make_basis(inputs, (), {}, dict(metadata, conversations=[row]))
    rebased = layer.make_basis(inputs, (), {}, dict(metadata, conversations=[row]), rebased=True)
    raw_path, rebased_path = layer.basis_path(path), layer.rebased_path(path)
    assert layer.write_basis(raw_path, scope, raw)
    assert layer.write_basis(rebased_path, scope, rebased)

    state = {"basis": None, "views": OrderedDict(), "loaded": False}
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE", state)
    monkeypatch.setattr(server, "_archive_history_read_cut",
                        lambda *args, **kwargs: contextlib.nullcontext((inputs, {"tickets": None})))
    monkeypatch.setattr(server, "_trace_row_flicker", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_archive_list_ticket_effects", lambda *args, **kwargs: None)
    projected = []
    reject_rebased = False

    def project(cut, previous, **kwargs):
        projected.append((previous.token, kwargs.get("max_rows")))
        if reject_rebased and previous.token == rebased.token:
            return None
        return layer.View(inputs, frozenset(), {}, "candidate", metadata, {}), [], []

    monkeypatch.setattr(server, "_archive_history_project_cut", project)

    def request(since=""):
        return server._archive_history_response(key, {}, since, "", ("gzip",),
                                                phases={}, check_client=None)

    body, encoding, etag = request()
    assert encoding == "gzip"
    assert etag == '"d-' + rebased.token + '"'
    assert json.loads(gzip.decompress(body))["base"]["conversations"] == [[0, ["a"]]]
    assert request(rebased.token) == (None, None, etag)
    for stale in (layer.REBASED_TOKEN_PREFIX + "old", layer.view_token()):
        projected.clear()
        phases = {}
        body, encoding, current_etag = server._archive_history_response(
            key, {}, stale, "", ("gzip",), phases=phases, check_client=None)
        assert (encoding, current_etag) == ("gzip", etag)
        document = json.loads(gzip.decompress(body))
        assert document["history_layer"] == 1
        assert document["base"]["conversations"] == [[0, ["a"]]]
        assert document["patch"]["since"] == rebased.token
        assert "load_basis" not in phases
        assert projected == [(rebased.token, server._ARCHIVE_HISTORY_COLD_REBASE_MAX_ROWS)]
    assert not state["loaded"]
    assert request(raw.token) == (None, None, '"d-' + raw.token + '"')
    assert state["basis"] == raw

    state.update(loaded=False, basis=None, views=OrderedDict())
    projected.clear()
    reject_rebased = True
    assert request(layer.view_token())[2] == '"d-' + raw.token + '"'
    assert projected == [(rebased.token, server._ARCHIVE_HISTORY_COLD_REBASE_MAX_ROWS), (raw.token, None)]

    def project_with_scope_limit(cut, previous, *, max_rows=None, phases, **kwargs):
        projected.append((previous.token, max_rows))
        if max_rows is not None:
            phases["scope_rejected"] = max_rows + 1
            return None
        return layer.View(inputs, frozenset(), {}, "candidate", metadata, {}), [], []

    state.update(loaded=False, basis=None, views=OrderedDict())
    projected.clear()
    monkeypatch.setattr(server, "_archive_history_project_cut", project_with_scope_limit)
    monkeypatch.setattr(server, "_archive_history_select_basis", lambda *args: rebased)
    assert request()[2] == '"d-' + rebased.token + '"'
    assert projected == [(rebased.token, server._ARCHIVE_HISTORY_COLD_REBASE_MAX_ROWS),
                         (rebased.token, None)]


def test_aged_rebased_tail_requires_a_large_patch_and_an_existing_basis():
    from types import SimpleNamespace

    basis = SimpleNamespace(frames={"gzip": b"frame"})
    now = 100_000
    deadline = server._ARCHIVE_HISTORY_AGED_TAIL_SECONDS
    tail = b"x" * server._ARCHIVE_HISTORY_AGED_TAIL_BYTES
    state = {"rebased": ((0, 0, 0, (now - deadline) * 1_000_000_000), basis)}

    assert server._archive_history_should_rebase(state, basis, tail, {}, now)
    assert not server._archive_history_should_rebase(state, basis, tail, {}, now - 1)
    assert not server._archive_history_should_rebase(state, basis, tail[:-1], {}, now)
    assert not server._archive_history_should_rebase({}, basis, tail, {}, now)
    assert not server._archive_history_should_rebase(state, basis, None, {}, now)
    changes = {"hot:hot": server._ARCHIVE_HISTORY_REBASE_MIN_CHANGES}
    assert server._archive_history_should_rebase({}, basis, b"tail", changes, now)


@pytest.mark.parametrize("restarted", [False, True])
def test_superseded_rebased_validator_still_receives_a_delta(tmp_path, monkeypatch, restarted):
    from collections import OrderedDict

    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    before = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=({"a": False},), edges=())
    after = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=({"a": True},), edges=())
    metadata = {"window": "all", "count": 1}
    row = {"session_id": "a", "archived": True}
    previous = layer.make_basis(
        before, (), {}, dict(metadata, conversations=[dict(row, archived=False)]), rebased=True)
    current = layer.make_basis(after, (), {}, dict(metadata, conversations=[row]), rebased=True)
    archive = tmp_path / "archive.json"
    target = layer.rebased_path(archive)
    previous_target = layer.previous_rebased_path(archive)
    key = server._archive_response_cache_key()
    scope = server._archive_history_scope(key)
    assert layer.write_basis(target, scope, previous)
    old_artifact = server._archive_history_artifact(target)
    assert layer.write_basis(target, scope, current, previous=previous_target)
    assert server._archive_history_artifact(previous_target) == old_artifact
    state = {"views": OrderedDict(), "loaded": False}
    if not restarted:
        state["rebased"] = old_artifact, previous
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", archive)
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE", state)
    monkeypatch.setattr(server, "_archive_history_read_cut",
                        lambda *args, **kwargs: contextlib.nullcontext((after, {"tickets": None})))
    monkeypatch.setattr(server, "_trace_row_flicker", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_archive_list_ticket_effects", lambda *args, **kwargs: None)

    def project(cut, basis, *, max_rows, **kwargs):
        assert basis.token == previous.token
        assert max_rows == (server._ARCHIVE_HISTORY_COLD_REBASE_MAX_ROWS if restarted else None)
        view = layer.View(after, frozenset(), {}, "current-view", metadata, {})
        return view, [row], []

    monkeypatch.setattr(server, "_archive_history_project_cut", project)
    assert server._archive_history_warm_rebased_basis(target, server._archive_history_artifact(target))
    phases = {}
    body, encoding, validator = server._archive_history_response(
        key, {}, previous.token, previous.order_token, ("gzip",), phases=phases, check_client=None)

    assert ("load_previous_rebased" in phases) == restarted
    assert "load_basis" not in phases
    retained = state["saved_previous_rebase"][1] if restarted else state["prior_rebased"]
    assert retained == previous.view()

    assert encoding == "gzip"
    assert validator == '"d-current-view"'
    patch = json.loads(gzip.decompress(body))
    assert patch["since"] == previous.token
    assert patch["same_order"] is True
    assert patch["conversations"] == [row]

    if restarted:
        oversized = server._ARCHIVE_HISTORY_COLD_REBASE_MAX_ROWS + 1

        def reject_old_projection(cut, basis, *, phases, max_rows, **kwargs):
            if basis.token == previous.token:
                assert max_rows < oversized
                phases["scope_rejected"] = oversized
                return None
            assert basis.token == current.token
            return current.view(), [], []

        monkeypatch.setattr(server, "_archive_history_project_cut", reject_old_projection)
        phases = {}
        body, encoding, validator = server._archive_history_response(
            key, {}, previous.token, previous.order_token, ("gzip",), phases=phases, check_client=None)
        assert phases["previous_rebased_fallback"] == {"scope_rejected": oversized}
        assert encoding == "gzip"
        assert validator == '"d-' + current.token + '"'
        document = json.loads(gzip.decompress(body))
        assert document["history_layer"] == 1
        assert document["base"]["delta_token"] == current.token


def test_background_rebase_warms_only_the_published_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    payload = {"window": "all", "count": 1, "conversations": [{"session_id": "a"}]}
    path = tmp_path / "archive.json"
    target = layer.rebased_path(path)
    key = server._archive_response_cache_key()
    scope = server._archive_history_scope(key)
    old = layer.make_basis(inputs, (), {}, payload, rebased=True)
    new = layer.make_basis(inputs, (), {}, payload, rebased=True)
    assert layer.write_basis(target, scope, old)
    initial = server._archive_history_artifact(target)
    state = {}
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE", state)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)

    with server._ARCHIVE_HISTORY_LOCK:
        assert not server._archive_history_warm_rebased_basis(target, initial)
    assert server._archive_history_warm_rebased_basis(target, initial)
    assert not server._archive_history_warm_rebased_basis(target, initial)
    with monkeypatch.context() as patch:
        def unneeded_load(*args):
            raise AssertionError("the published artifact was already decoded")
        patch.setattr(layer, "load_basis", unneeded_load)
        assert server._archive_history_rebased_basis(state, key, ("gzip",)) == old

    assert layer.write_basis(target, scope, new)
    published = server._archive_history_artifact(target)
    real_load = layer.load_basis

    def replaced_during_load(*args):
        basis = real_load(*args)
        assert layer.write_basis(target, scope, old)
        return basis

    with monkeypatch.context() as patch:
        patch.setattr(layer, "load_basis", replaced_during_load)
        assert not server._archive_history_warm_rebased_basis(target, published)
        assert state["rebased"] == (initial, old)
    assert server._archive_history_rebased_basis(state, key, ("gzip",)) == old


def test_startup_warms_saved_rebased_basis_once(tmp_path, monkeypatch):
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    path = tmp_path / "archive.json"
    target = layer.rebased_path(path)
    key = server._archive_response_cache_key()
    state = {"loaded": False}
    events = []
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_STATE", state)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "record_diag", lambda *args: events.append(args))

    server._archive_history_warm_saved_basis()
    assert events == []

    inputs = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    payload = {"window": "all", "count": 1, "conversations": [{"session_id": "a"}]}
    basis = layer.make_basis(inputs, (), {}, payload, rebased=True)
    assert layer.write_basis(target, server._archive_history_scope(key), basis)
    state["loaded"] = True
    server._archive_history_warm_saved_basis()
    assert events == []

    state["loaded"] = False
    server._archive_history_warm_saved_basis()
    server._archive_history_warm_saved_basis()
    assert len(events) == 1
    assert events[0][2] == {"stage": "startup_rebased_basis", "rebased_cached": True}
    assert server._archive_history_rebased_basis(state, key, ("gzip",)) == basis


def test_gzip_basis_and_patch_work_without_zstd(tmp_path, monkeypatch):
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    source = layer.read_inputs(inventory("a"), {}, {}, (), overlay_maps=(), edges=())
    basis = layer.make_basis(source, set(), {}, {"conversations": [{"session_id": "a"}]})
    assert set(basis.frames) == {"gzip"}
    target = layer.basis_path(tmp_path / "archive.json")
    assert layer.write_basis(target, "test-scope", basis)
    basis = layer.load_basis(target, "test-scope")
    patch = layer.delta_payload(("a",), ("a", "b"), basis.token, "new",
                                {"conversations": [{"session_id": "b"}]})
    encoded = layer.layer_representation(basis, patch, "gzip")
    decoder = zlib.decompressobj(wbits=31)
    envelope = json.loads(decoder.decompress(encoded))
    assert decoder.eof and not decoder.unused_data
    assert envelope["history_layer"] == 1
    assert envelope["base"]["delta_token"] == basis.token
    assert envelope["patch"]["since"] == basis.token
    assert envelope["patch"]["delta_token"] == "new"
    assert json.loads(gzip.decompress(layer.delta_representation(patch, "gzip")))["ids"] == ["a", "b"]


def test_large_delta_packs_rows_only_for_layout_capable_clients():
    rows = [{"session_id": f"sid-{i}", "title": "caf\u00e9"} for i in range(512)]
    rows[-1] = {"id": "last", "ticket": {"ref": "TEST-1"}}
    order = tuple(row.get("session_id") or row["id"] for row in rows)
    patch = layer.delta_payload(order[::-1], order, "old", "new",
                                {"count": len(rows), "conversations": rows})
    plain = json.loads(gzip.decompress(layer.delta_representation(patch, "gzip")))
    assert "row_layouts" not in plain
    checks = []
    packed = json.loads(gzip.decompress(layer.delta_representation(
        patch, "gzip", row_layouts=True, check_client=lambda: checks.append(True))))
    layouts = packed.pop("row_layouts")
    packed["conversations"] = [dict(zip(layouts[index], values, strict=True))
                               for index, values in packed["conversations"]]
    assert packed == plain
    assert checks
    assert patch["conversations"] == rows

    small = dict(patch, conversations=rows[:17])
    assert layer.delta_representation(small, "gzip", row_layouts=True) == (
        layer.delta_representation(small, "gzip"))


def test_known_deletion_rechecks_remaining_family_and_explicit_order():
    previous = layer.read_inputs(inventory("a", "b"), {}, {}, (), overlay_maps=(),
                                 edges=(("a", "b"),))
    current = layer.read_inputs(inventory("b"), {}, {}, (), overlay_maps=(), edges=())
    changes = layer.Changes(previous, current, allow_shrink=True)
    assert changes.removed == {"a"}
    assert changes.scope() == {"b"}
    patch = layer.delta_payload(previous.order, current.order, "old", "new",
                                {"conversations": [{"session_id": "b"}]}, saved_order=False)
    assert patch["ids"] == ("b",)
    assert patch["delta_token"] == "new"


def test_changes_do_not_share_edges_discovered_during_hydration():
    rows = inventory("a", "b", "c")
    previous = layer.read_inputs(rows, {}, {}, (), overlay_maps=(), edges=(("a", "b"),))
    current = layer.read_inputs(rows, {}, {}, (), overlay_maps=(), edges=(("a", "b"),))

    first = layer.Changes(previous, current)
    assert first.scope(("a",)) == {"a", "b"}
    first.add_edges((("b", "c"),))
    assert first.scope(("a",)) == {"a", "b", "c"}

    second = layer.Changes(previous, current)
    assert second.scope(("a",)) == {"a", "b"}


def test_detached_rebase_keeps_boot_basis_and_publishes_exact_order(tmp_path, monkeypatch):
    from dataclasses import replace

    from ccc_server import cpu_priority

    monkeypatch.setattr(cpu_priority, "set_background_priority", lambda _: None)
    monkeypatch.setattr(server, "_WT_DISPLAY_SNAPSHOT_ONLY", False)
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    archive = tmp_path / "archive.json"
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", archive)
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_REBASE_MIN_CHANGES", 1)
    key = server._archive_response_cache_key()
    scope = server._archive_history_scope(key)
    before = layer.read_inputs(inventory("a", "b", "removed"), {}, {}, (), overlay_maps=(), edges=())
    after = layer.read_inputs(inventory("b", "a", "new"), {}, {}, (), overlay_maps=(), edges=())
    metadata = {"ok": True, "window": "all", "count": 3}
    old_rows = [{"session_id": sid, "name": "Old"} for sid in before.order]
    old = layer.make_basis(before, (), {}, dict(metadata, conversations=old_rows))
    boot_path = layer.basis_path(archive)
    assert layer.write_basis(boot_path, scope, old)
    changed = [{"session_id": "a", "name": "Changed"}, {"session_id": "new"}]
    patch = layer.delta_payload(before.order, after.order, old.token, "current",
                                dict(metadata, conversations=changed))
    seed = replace(old, inputs=after, order_token=patch["order_token"], token="current",
                   frames={"gzip": layer.layer_representation(old, patch, "gzip")},
                   effect_rows={"a": changed[0]})
    path = tmp_path / "seed"
    assert layer.write_basis(path, scope + ":rebase", seed)
    assert server._archive_history_publisher_main(path)
    published = layer.load_basis(layer.rebased_path(archive), scope)
    assert published.effect_rows == seed.effect_rows
    assert published.inputs == after
    decoder = zlib.decompressobj(wbits=31)
    raw = decoder.decompress(published.frames["gzip"])
    payload = json.loads(raw.removeprefix(layer._LAYER_PREFIX))
    rows = [dict(zip(payload["row_layouts"][index], values)) for index, values in payload["conversations"]]
    assert rows == [old_rows[1], *changed]
    assert layer.load_basis(boot_path, scope) == old

    state = {}
    choose = server._archive_history_select_basis
    assert choose(state, key, old, before, "", ("gzip",)) == old
    assert choose(state, key, old, after, "", ("gzip",)) == published
    assert choose(state, key, old, before, published.token, ("gzip",)) == published
    assert choose(state, key, old, after, old.token, ("gzip",)) == old

    assert server._archive_history_publisher_main(path)
    replacement = layer.load_basis(layer.rebased_path(archive), scope)
    previous_path = layer.previous_rebased_path(archive)
    assert replacement.token != published.token
    assert layer.load_basis(previous_path, scope) == published

    assert layer.write_basis(path, scope + ":rebase", replace(seed, token="unrelated"))
    assert not server._archive_history_publisher_main(path)
    assert layer.load_basis(layer.rebased_path(archive), scope) == replacement
    assert layer.load_basis(previous_path, scope) == published


def test_refresh_rebases_only_large_hot_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(layer.list_encoding, "_zstd", lambda raw: None)
    archive = tmp_path / "archive.json"
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", archive)
    monkeypatch.setattr(server, "_ARCHIVE_HISTORY_REBASE_MIN_CHANGES", 2)
    key = server._archive_response_cache_key()
    old_rows = [{"session_id": sid, "state": "idle"} for sid in ("a", "b", "c")]
    new_rows = [dict(row, state="working") for row in old_rows[:2]] + old_rows[2:]

    def guards(rows):
        hot = {row["session_id"]: row for row in rows[:2]}
        return layer.read_inputs(
            inventory("a", "b", "c"), hot, {sid: layer.hot_row_token(row) for sid, row in hot.items()},
            (), overlay_maps=(), edges=(),
        )

    before = guards(old_rows)
    metadata = {"window": "all", "count": 3}
    basis = layer.make_basis(before, set(), {}, dict(metadata, conversations=old_rows))
    target = layer.basis_path(archive)
    assert layer.write_basis(target, server._archive_history_scope(key), basis)

    def project(cut, previous, *, persist_topics):
        assert not persist_topics
        assert previous == basis
        view = layer.View(cut[0], frozenset(("a", "b")), {}, "next", metadata, {})
        return view, new_rows[:2], {}

    monkeypatch.setattr(server, "_archive_history_project_cut", project)
    after = guards(new_rows)
    assert server._archive_history_rebase_cut((before,), key) is None
    assert server._archive_history_rebase_cut((guards([new_rows[0], *old_rows[1:]]),), key) is None
    refreshed = server._archive_history_rebase_cut((after,), key)
    assert refreshed.inputs == after
    assert refreshed.working == {"a", "b"}
    raw = zlib.decompressobj(wbits=31).decompress(refreshed.frames["gzip"])
    packed = json.loads(raw.removeprefix(layer._LAYER_PREFIX))
    assert [dict(zip(packed["row_layouts"][index], values))
            for index, values in packed["conversations"]] == new_rows
    assert layer.load_basis(target, server._archive_history_scope(key)) == basis


def test_changed_cut_reuses_unchanged_effect_rows_and_adds_discovered_children(monkeypatch):
    old_inputs = layer.read_inputs(inventory("tracked", "changed", "untracked"), {}, {}, (),
                                   overlay_maps=({},), edges=())
    inputs = layer.read_inputs(inventory("tracked", "changed", "untracked"), {}, {}, (),
                               overlay_maps=({"changed": True},), edges=())
    tracked = {"session_id": "tracked", "display_name": "still valid"}
    child = {"session_id": "changed", "parent_session_id": "tracked"}
    other = {"session_id": "untracked", "display_name": "other"}
    previous = layer.View(old_inputs, frozenset(), {}, "old", {}, {"tracked": tracked})
    reads = []

    def read(wanted):
        reads.append(wanted)
        return [row for row in (tracked, child, other) if row["session_id"] in wanted]

    monkeypatch.setattr(server, "_archive_list_project_rows", lambda rows, **kw: rows)
    monkeypatch.setattr(server, "_queue_health_stats", lambda *args, **kw: {})
    cut = (inputs, {"now": 100, "tickets": {"sessions": {"tracked": {"ref": "TEST-1"}},
                                           "pending": {}}}, read, True)
    assert server._archive_history_project_cut(cut, previous, max_rows=1) is None
    assert reads == [{"changed"}]
    reads.clear()
    _, changed, effects = server._archive_history_project_cut(cut, previous)

    assert reads == [{"changed"}, {"tracked", "changed"}]
    assert changed == [tracked, child]
    assert effects == [tracked, child]

    unrelated = layer.read_inputs(inventory("tracked", "changed", "untracked"), {}, {}, (),
                                  overlay_maps=({"untracked": True},), edges=())
    reads.clear()
    _, changed, effects = server._archive_history_project_cut((unrelated, *cut[1:]), previous)

    assert reads == [{"untracked"}]
    assert changed == [other]
    assert effects == [tracked]


def test_inventory_memo_changes_when_the_pinned_cut_or_exclusion_changes():
    same_cut = tuple(inventory("a", "b"))
    first = layer.read_inputs(same_cut, {}, {}, (), overlay_maps=(), edges=())
    excluded = layer.read_inputs(same_cut, {}, {}, (), overlay_maps=(), edges=(),
                                 removed={"b"})
    restored = layer.read_inputs(same_cut, {}, {}, (), overlay_maps=(), edges=())
    next_cut = same_cut[:-1] + (("b", same_cut[-1][1], "", "changed-while-open"),)
    updated = layer.read_inputs(next_cut, {}, {}, (), overlay_maps=(), edges=())
    assert excluded.order == ("a",)
    assert first == restored
    assert layer.candidates(restored, updated) == {"b"}


def test_pinned_topic_guards_refresh_with_the_snapshot_and_live_overlays():
    from types import MappingProxyType

    source = tuple(inventory("a", "b", "c"))
    topics = MappingProxyType({"a": ("", "", "old", 1), "c": ("", "", "stable", 1)})
    live = {"b": False}

    def read(records):
        return layer.read_inputs(source, {}, {}, (), overlay_maps=(records, live),
                                 edges=(), pinned_overlays=(records,))

    first = read(topics)
    live["b"] = 0
    second = read(topics)
    assert first.overlays[0] is second.overlays[0]
    assert layer.candidates(first, second) == {"b"}

    third = read(MappingProxyType({"a": ("", "", "new", 1), "c": ("", "", "stable", 1)}))
    assert layer.candidates(second, third) == {"a"}
    assert second.overlays[0]["c"] is third.overlays[0]["c"]

    fourth = read(MappingProxyType({"c": ("", "", "stable", True)}))
    assert layer.candidates(third, fourth) == {"a", "c"}
    assert fourth.overlays[0] == {"c": '["","","stable",true]'}


def test_cold_read_pins_the_selected_hot_rows_after_a_row_ages_out(tmp_path, monkeypatch):
    import time

    from ccc_server import continuation_topics

    now = time.time()
    hot = {"session_id": "recent", "mtime": now}
    aged = {"session_id": "aged", "mtime": now - archive_store.HOT_SECONDS - 5}
    extra = {"session_id": "extra"}
    persisted = {"cached_at": now - 10, "conversations": [hot, aged]}
    path = tmp_path / "archive.json"
    key = server._archive_response_cache_key()
    archive_store.save(path, {
        "schema_version": server._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION,
        "build_id": server._ARCHIVE_RESPONSE_CACHE_BUILD_ID,
        "entries": {key: persisted},
    }, complete=True, sidebar_key=key)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", path)
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE", {key: persisted})
    monkeypatch.setattr(server, "_archive_serve_cache", {})
    monkeypatch.setattr(server, "_archive_serve_version", 0)
    monkeypatch.setattr(server, "_archive_response_cache_get", lambda key: persisted)
    monkeypatch.setattr(server, "_archive_refresh_load_blocked", lambda now: True)
    monkeypatch.setattr(server, "_conversation_lifecycle_snapshot", lambda: (set(), set(), set(), {}))
    monkeypatch.setattr(server, "_overlay_conversation_lifecycle_flags", lambda rows, **kwargs: rows)
    monkeypatch.setattr(server, "_archive_list_source_overlays", lambda *_, **kwargs: ([], [extra]))
    monkeypatch.setattr(server, "_capture_archive_history_sidecars", lambda _: dict.fromkeys(
        ("resumes", "parents", "names", "drivers", "ideas", "overlays"), {}))
    monkeypatch.setattr(layer, "_INVENTORY", (None, frozenset(), None))
    reader = object()
    monkeypatch.setattr(continuation_topics, "archive_projection_inputs",
                        lambda conn, **kwargs: (({}, {}, {}, {}), [], 0))
    original_load = archive_store._load_override_entry
    sqlite_reads = []

    def track_load(*args, **kwargs):
        sqlite_reads.append(kwargs["session_ids"])
        return original_load(*args, **kwargs)

    monkeypatch.setattr(archive_store, "_load_override_entry", track_load)

    with server._archive_history_read_cut(key, {}, now, topic_reader=reader) as cut:
        assert cut is not None
        inputs, _, hydrate, _ = cut
        assert inputs.sources["recent"][0] == "hot"
        assert inputs.sources["aged"][0] == "canonical"
        assert hydrate(None) == [hot, aged, extra]
        assert hydrate({"recent", "aged", "extra"}) == [hot, aged, extra]
        assert hydrate({"recent", "extra"}) == [hot, extra]
        assert hydrate({"extra"}) == [extra]
        assert sqlite_reads == [None, {"aged"}]
        hydrate(None)[0]["mtime"] = 0
        hydrate({"recent"})[0]["mtime"] = 1
        assert hydrate({"recent"}) == [hot]
