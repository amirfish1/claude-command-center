import json
import os

import server  # noqa: F401  (registers core names)
from ccc_server import background_runs


def _write(root, slug, sid, mtime, usage):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(json.dumps({
        "type": "assistant", "sessionId": sid, "timestamp": "2026-10-16T10:00:00Z",
        "message": {"id": "m-" + sid, "model": "claude-x", "usage": usage},
    }) + "\n")
    os.utime(p, (mtime, mtime))


def test_groups_temp_dir_runs_by_spawner_and_ignores_real_projects(tmp_path):
    now = 1_800_000_000.0
    u = {"input_tokens": 1, "cache_read_input_tokens": 10, "output_tokens": 2}
    _write(tmp_path, "-private-var-folders-ab-T", "a1", now - 60, u)
    _write(tmp_path, "-private-var-folders-ab-T", "a2", now - 7200, u)
    _write(tmp_path, "-private-var-folders-ab-T", "b1", now - 60, u)
    _write(tmp_path, "-Users-me-Apps-repo", "r1", now - 60, u)
    markers = {"a1": {"spawned_via": "evalx", "kind": "grader"},
               "a2": {"spawned_via": "evalx", "kind": "grader"}}

    p = background_runs.background_runs_payload(
        now=now, projects_root=tmp_path, markers=markers, use_cache=False)

    by = {g["spawner"]: g for g in p["groups"]}
    assert by["evalx"]["runs"] == 2 and by["evalx"]["runs_last_hour"] == 1
    assert by["evalx"]["tokens_last_day"] == 26  # both runs are inside the day window
    assert by["unknown"]["runs"] == 1
    assert p["totals"]["runs"] == 3
    assert by["evalx"]["cost_usd"] > 0


def test_payload_reports_cpu_pressure(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getloadavg", lambda: (9.0, 4.0, 2.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    p = background_runs.background_runs_payload(now=1_800_000_000.0, projects_root=tmp_path, markers={}, use_cache=False)
    assert p["cpu"]["cores"] == 4
    assert p["cpu"]["load_per_core"] == 2.25
    assert p["cpu"]["level"] == "saturated"


def test_payload_reports_memory_pressure(tmp_path, monkeypatch):
    real_open = open
    meminfo = "MemTotal: 1000000 kB\nMemAvailable: 50000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"

    def fake_open(path, *a, **kw):
        if path == "/proc/meminfo":
            import io
            return io.StringIO(meminfo)
        if path == "/proc/pressure/memory":
            raise OSError("no psi")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    p = background_runs.background_runs_payload(now=1_800_000_000.0, projects_root=tmp_path, markers={}, use_cache=False)
    assert p["memory"]["used_pct"] == 95.0
    assert p["memory"]["level"] == "saturated"
    assert "swap_used_pct" not in p["memory"]


def test_tts_pressure_measures_cores_and_load_per_core(tmp_path, monkeypatch):
    from ccc_server import free_runtime as fr

    with fr._TTS_CPU_LOCK:
        saved_hist = (fr._TTS_CPU_HISTORY["pid"], fr._TTS_CPU_HISTORY["samples"])
        fr._TTS_CPU_HISTORY["pid"] = None
        fr._TTS_CPU_HISTORY["samples"] = None
    try:
        health_state = {"ok": True, "pid": 42424, "threads": 4, "busy": True, "fg_waiting": 1, "bg_waiting": 0}
        cpu_state = {"cpu_s": 10.0, "up_s": 500.0}
        monkeypatch.setattr(fr, "tts_queue_stats", lambda: {
            "enabled": True, "queued": 2, "in_flight": 2, "active": 1,
            "waiting": 1, "session_waiting": 0, "fg": 1, "bg": 1,
        })
        monkeypatch.setattr(fr, "_local_tts_probe_for_pressure", lambda _mono: dict(health_state))
        monkeypatch.setattr(fr, "_proc_cpu_stats", lambda _pid: (cpu_state["cpu_s"], cpu_state["up_s"]))

        first = fr.tts_pressure(now_mono=100.0, host_cores=16)
        assert first["enabled"] is True
        assert first["running"] is True
        assert first["level"] == "busy"
        assert first["queued"] == 2

        # 24 CPU-seconds burned over 10 wall-seconds = 2.4 cores = 0.15x/core on 16 cores.
        cpu_state["cpu_s"] = 34.0
        second = fr.tts_pressure(now_mono=110.0, host_cores=16)
        assert second["cores"] == 2.4
        assert second["recent_cores"] == 2.4
        assert second["load_per_core"] == 0.15
        assert second["threads"] == 4
        assert second["level"] == "busy"

        # When TTS is turned off, contribution drops to 0 and level is "off".
        monkeypatch.setattr(fr, "tts_queue_stats", lambda: {"enabled": False, "queued": 0})
        off = fr.tts_pressure(now_mono=112.0, host_cores=16)
        assert off["enabled"] is False
        assert off["level"] == "off"
        assert off["load_per_core"] == 0.0
        assert off["cores"] == 0.0
    finally:
        with fr._TTS_CPU_LOCK:
            fr._TTS_CPU_HISTORY["pid"], fr._TTS_CPU_HISTORY["samples"] = saved_hist


def test_payload_includes_tts_load_and_renders_fourth_metric(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    monkeypatch.setattr(os, "getloadavg", lambda: (15.68, 12.0, 10.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(background_runs, "tts_pressure", lambda host_cores=None: {
        "enabled": True,
        "running": True,
        "busy": True,
        "level": "busy",
        "cores": 2.4,
        "recent_cores": 2.8,
        "load_per_core": round(2.4 / (host_cores or 16), 2),
        "host_cores": host_cores or 16,
        "threads": 4,
        "queued": 2,
        "cpu_time_s": 48.5,
    })
    p = background_runs.background_runs_payload(now=1_800_000_000.0, projects_root=tmp_path, markers={}, use_cache=False)
    assert p["tts"]["load_per_core"] == 0.15
    assert p["tts"]["cores"] == 2.4

    app_js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    start = app_js.index("  function renderBgRuns(p) {")
    end = app_js.index("  async function pollBgRuns()", start)
    render_fn = app_js[start:end]
    script = (
        "const btn = { style: {}, classList: { toggle() {} }, setAttribute() {} };\n"
        "const panel = { hidden: false, innerHTML: '' };\n"
        "const $bgRunsBtn = btn, $bgRunsPanel = panel;\n"
        "const escapeHtml = s => String(s).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        + render_fn
        + "\nrenderBgRuns(" + json.dumps(p) + ");\n"
        + "console.log(JSON.stringify({ btnText: btn.textContent, panelHtml: panel.innerHTML }));\n"
    )
    out = json.loads(subprocess.check_output(["node", "-e", script], text=True))
    assert "TTS 0.15×" in out["btnText"]
    assert "TTS · busy" in out["panelHtml"]
    assert "0.15×/core · 2.4 cores · 2 queued" in out["panelHtml"]
