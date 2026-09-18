"""fallback_to_default_worker in the dashboard's queue settings (CCC-1161).

WatchTower's per-queue "revert to CCC default worker if current model is
exhausted" key is set from both queue dialogs. The save is a full replace, so
the risky part is callers that re-post a queue's config without knowing about
this field: they must not wipe it (same rule as queue_label / grace_s).
"""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parent.parent


def test_payload_accepts_bool_and_string_and_omits_absent():
    base = {"queue": "PQ"}
    assert "fallback_to_default_worker" not in server._queue_config_from_payload(base)["config"]
    on = server._queue_config_from_payload({**base, "fallback_to_default_worker": True})
    assert on["config"]["fallback_to_default_worker"] is True
    off = server._queue_config_from_payload({**base, "fallback_to_default_worker": False})
    assert off["config"]["fallback_to_default_worker"] is False
    # A raw API caller may post the string form a select element produces.
    assert server._queue_config_from_payload(
        {**base, "fallback_to_default_worker": "on"}
    )["config"]["fallback_to_default_worker"] is True
    assert server._queue_config_from_payload(
        {**base, "fallback_to_default_worker": "false"}
    )["config"]["fallback_to_default_worker"] is False


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


def test_save_sets_and_clears_the_toggle(api):
    post, stored = api
    assert post({"queue": "PQ", "fallback_to_default_worker": True})[0] == 200
    assert stored("PQ")["fallback_to_default_worker"] is True
    assert post({"queue": "PQ", "fallback_to_default_worker": False})[0] == 200
    assert stored("PQ")["fallback_to_default_worker"] is False


def test_save_without_the_key_leaves_the_toggle_alone(api):
    post, stored = api
    assert post({"queue": "PQ", "fallback_to_default_worker": True})[0] == 200
    assert stored("PQ")["fallback_to_default_worker"] is True
    # A caller that predates the field (e.g. an engine-only edit) re-posts
    # without the key: the toggle must survive the full-replace save.
    assert post({"queue": "PQ", "auto_drain": True})[0] == 200
    assert stored("PQ")["fallback_to_default_worker"] is True


def test_duplicate_shaped_save_copies_the_toggle(api):
    post, stored = api
    src = {"queue": "SRC", "fallback_to_default_worker": True}
    assert post(src)[0] == 200
    # The Q2 dialog carries the field into a duplicate like every other
    # visible setting — unlike queue_label it is not reset.
    assert post({**src, "queue": "COPY"})[0] == 200
    assert stored("COPY")["fallback_to_default_worker"] is True


def test_both_settings_uis_expose_the_toggle():
    q2 = (ROOT / "static" / "q2.js").read_text(encoding="utf-8")
    assert 'data-q2-cfg="fallback_to_default_worker"' in q2
    assert "Revert to CCC default worker if current model is exhausted" in q2
    assert "payload.fallback_to_default_worker = payload.fallback_to_default_worker === 'true'" in q2
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "fqConfigFallback" in app
    assert "Revert to CCC default worker if current model is exhausted" in app
    assert "fallback_to_default_worker: fields.fallback.checked" in app
    assert "fields.fallback.checked = !!c.fallback_to_default_worker" in app
