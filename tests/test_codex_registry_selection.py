import server
from ccc_server import codex


def test_selected_registry_entries_leave_unrelated_records_untouched(monkeypatch):
    class Unrelated(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("Unrelated history must not be copied")

    record = {"ccc": {"prompt": "Review CI"}}
    monkeypatch.setattr(codex, "_cached_codex_thread_registry_data", lambda: {
        "threads": {"wanted": record, "unrelated": Unrelated(), "invalid": None},
    })
    result = codex._codex_thread_registry_entries({"wanted", "missing", "invalid"})
    assert result == {"wanted": record}
    result["wanted"]["ccc"]["prompt"] = "Changed"
    assert record["ccc"]["prompt"] == "Review CI"
    assert codex._codex_thread_registry_entries(set()) == {}


def test_selected_spawn_entries_preserve_launch_and_resume_precedence(monkeypatch):
    launch = {"session_id": "launch", "resumed_sid": "resumed", "engine": "codex", "prompt": "Launch"}
    monkeypatch.setattr(server, "_load_spawn_registry", lambda: [launch])
    requested = []

    def registry_entries(session_ids):
        requested.append(set(session_ids))
        return {"native": {"ccc": {"prompt": "Native"}}}

    monkeypatch.setattr(server, "_codex_thread_registry_entries", registry_entries)
    result = server._spawn_registry_entries_by_session(
        engine="codex", session_ids={"launch", "resumed", "native"},
    )
    assert requested == [{"native"}]
    assert result["launch"] is launch
    assert result["resumed"] is launch
    assert result["native"]["prompt"] == "Native"
    assert result["native"]["codex_thread_registry"] is True
