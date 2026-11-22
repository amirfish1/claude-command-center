import contextlib
import gzip
import json
import zlib

import server
from ccc_server import archive_history_layer as layer, archive_store


def inventory(*session_ids):
    return [(sid, archive_store.identity({"session_id": sid}), "", f"digest-{sid}")
            for sid in session_ids]


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


def test_layer_never_adopts_duplicate_overlay_identity():
    source = inventory("a")
    assert layer.read_inputs(source, {}, {}, [{"session_id": "a"}],
                             overlay_maps=(), edges=()) is None


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


def test_cold_read_pins_the_selected_hot_rows_after_a_row_ages_out(tmp_path, monkeypatch):
    import time

    from ccc_server import continuation_topics

    now = time.time()
    hot = {"session_id": "recent", "mtime": now}
    aged = {"session_id": "aged", "mtime": now - archive_store.HOT_SECONDS - 5}
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
    monkeypatch.setattr(server, "_archive_list_source_overlays", lambda *_: ([], []))
    monkeypatch.setattr(server, "_capture_archive_history_sidecars", lambda _: dict.fromkeys(
        ("resumes", "parents", "names", "drivers", "ideas", "overlays"), {}))
    monkeypatch.setattr(layer, "_INVENTORY", (None, frozenset(), None))
    reader = object()
    monkeypatch.setattr(continuation_topics, "archive_projection_inputs",
                        lambda conn, **kwargs: (({}, {}, {}, {}), [], 0))

    with server._archive_history_read_cut(key, {}, now, topic_reader=reader) as cut:
        assert cut is not None
        inputs, _, hydrate, _ = cut
        assert inputs.sources["recent"][0] == "hot"
        assert inputs.sources["aged"][0] == "canonical"
        assert hydrate(None) == [hot, aged]
