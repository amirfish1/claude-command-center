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
