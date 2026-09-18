"""queue_label in the dashboard's queue settings (Q2 dialog + main manager).

The label marks an issue as belonging to a queue when 2+ GitHub queues share a
repo. The save endpoint is a full replace, so the risky part is callers that
re-post a queue's config without knowing about this field: they must not wipe it.
"""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parent.parent


def test_payload_accepts_trims_and_omits_blank():
    base = {"queue": "PQ", "backend": "github", "github_repo": "o/r"}
    got = server._queue_config_from_payload({**base, "queue_label": "  Personal "})
    assert got["config"]["queue_label"] == "Personal"
    blank = server._queue_config_from_payload({**base, "queue_label": "  "})
    assert "queue_label" not in blank["config"]
    absent = server._queue_config_from_payload(base)
    assert "queue_label" not in absent["config"]


@pytest.mark.parametrize("bad", ["a,b", "x" * 51, "watchtower:play", "two\nlines"])
def test_payload_rejects_labels_gh_would_mangle(bad):
    with pytest.raises(ValueError):
        server._queue_config_from_payload(
            {"queue": "PQ", "backend": "github", "github_repo": "o/r", "queue_label": bad}
        )


def test_payload_drops_label_for_file_backend():
    got = server._queue_config_from_payload({"queue": "PQ", "queue_label": "Personal"})
    assert "queue_label" not in got["config"]


@pytest.fixture
def api(tmp_path, monkeypatch):
    cfg = tmp_path / "queue-config.json"
    monkeypatch.setenv("WATCHTOWER_CONFIG_FILE", str(cfg))
    monkeypatch.setattr(server._wt_config, "CONFIG_FILE", cfg)
    monkeypatch.setattr(server, "_reconcile_once_async", lambda: None)
    monkeypatch.setattr(server, "_wt_log_queue_config_change", lambda *a, **k: None)
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def post(payload):
        req = urllib.request.Request(
            base + "/api/queue/config", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def stored(queue):
        return (json.loads(cfg.read_text()) if cfg.exists() else {}).get(queue) or {}

    yield post, stored
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def test_save_sets_preserves_and_clears_the_label(api):
    post, stored = api
    gh = {"queue": "PQ", "backend": "github", "github_repo": "amirfish1/TODO"}

    status, _ = post({**gh, "queue_label": "Personal"})
    assert status == 200 and stored("PQ")["queue_label"] == "Personal"

    # A caller that predates the field (e.g. the session-queue auto-drain flip)
    # re-posts without the key: the label must survive.
    status, _ = post({**gh, "auto_drain": True})
    assert status == 200 and stored("PQ")["queue_label"] == "Personal"

    # The dialogs always send the key; blank means "back to the default".
    status, _ = post({**gh, "queue_label": ""})
    assert status == 200 and "queue_label" not in stored("PQ")


def test_bad_label_is_refused_before_anything_is_written(api):
    post, stored = api
    status, body = post({"queue": "PQ", "backend": "github",
                         "github_repo": "amirfish1/TODO", "queue_label": "a,b"})
    assert status == 400 and "queue_label" in body["error"]
    assert stored("PQ") == {}


def test_both_settings_uis_expose_the_field():
    q2 = (ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert 'data-q2-cfg="queue_label"' in q2
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "fqConfigQueueLabel" in app
    assert "queue_label: fields.queueLabel.value" in app
    assert "fields.queueLabel.value = c.queue_label" in app


def test_payload_grace_s_is_present_only_and_validated():
    base = {"queue": "PQ"}
    assert "grace_s" not in server._queue_config_from_payload(base)["config"]
    assert "grace_s" not in server._queue_config_from_payload({**base, "grace_s": ""})["config"]
    assert server._queue_config_from_payload({**base, "grace_s": "30"})["config"]["grace_s"] == 30
    assert server._queue_config_from_payload({**base, "grace_s": 0})["config"]["grace_s"] == 0
    for bad in ("abc", -1):
        with pytest.raises(ValueError):
            server._queue_config_from_payload({**base, "grace_s": bad})


def test_duplicate_shaped_save_copies_gate_and_grace_and_never_the_label(api):
    post, stored = api
    src = {"queue": "SRC", "backend": "github", "github_repo": "amirfish/TODO",
           "queue_label": "Personal", "product_gate": True, "grace_s": "45",
           "engine": "claude", "desired_workers": 2, "claim_types": ["bug"]}
    assert post(src)[0] == 200
    assert stored("SRC")["product_gate"] is True and stored("SRC")["grace_s"] == 45

    # What the Q2 dialog posts for a duplicate: same settings, new name, label blank.
    dup = {**src, "queue": "COPY", "queue_label": "", "auto_drain": True}
    status, _ = post(dup)
    assert status == 200
    copy = stored("COPY")
    assert copy["product_gate"] is True and copy["grace_s"] == 45
    assert copy["desired_workers"] == 2 and copy["claim_types"] == ["bug"]
    assert copy["github_repo"] == "amirfish/TODO"
    assert "queue_label" not in copy
    # A brand-new queue always starts as a backlog, whatever the client sent.
    assert copy["auto_drain"] is False
    # ...and the original is untouched.
    assert stored("SRC")["queue_label"] == "Personal"


def test_save_without_gate_or_grace_keys_leaves_them_alone(api):
    post, stored = api
    gh = {"queue": "PQ", "backend": "github", "github_repo": "amirfish1/TODO"}
    post({**gh, "product_gate": True, "grace_s": 60})
    # e.g. the main manager, which has no grace_s field.
    assert post({**gh, "product_gate": True})[0] == 200
    assert stored("PQ")["grace_s"] == 60


def test_only_one_catch_all_per_repo_and_nothing_written_on_refusal(api):
    post, stored = api
    a = {"queue": "AQ", "backend": "github", "github_repo": "amirfish1/TODO"}
    assert post({**a, "queue_label": "*"})[0] == 200
    assert stored("AQ")["queue_label"] == "*"

    status, body = post({**a, "queue": "BQ", "queue_label": "*"})
    assert status == 400 and "already the catch-all" in body["error"]
    assert stored("BQ") == {}
    # A different repo may have its own catch-all; the original may re-save.
    assert post({**a, "queue": "BQ", "github_repo": "amirfish1/other", "queue_label": "*"})[0] == 200
    assert post({**a, "queue_label": "*"})[0] == 200


def test_q2_has_a_duplicate_button_and_dialog_mode():
    html = (ROOT / "static" / "q2.html").read_text(encoding="utf-8")
    assert 'id="q2DuplicateQueueBtn"' in html
    q2 = (ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert "openQueueConfig('', '', { duplicateOf: state.queue })" in q2
    assert "data-q2-dup-of" in q2                      # name-collision guard wired
    assert 'data-q2-cfg="product_gate"' in q2 and 'data-q2-cfg="grace_s"' in q2
    assert "delete c.queue_label" in q2                # label never copied


def test_q2_desired_workers_key_is_honoured_including_parked_zero(api):
    """The Q2 dialog posts `desired_workers`; the server used to read only
    `workers`, so every Q2 save silently reset a queue to 1 worker."""
    post, stored = api
    assert post({"queue": "WQ", "desired_workers": 3})[0] == 200
    assert stored("WQ")["desired_workers"] == 3
    assert post({"queue": "WQ", "desired_workers": 0})[0] == 200   # parked
    assert stored("WQ")["desired_workers"] == 0
    # The main dialog's key keeps its 1-16 rule.
    status, body = post({"queue": "WQ", "workers": 0})
    assert status == 400 and "between 1 and 16" in body["error"]
