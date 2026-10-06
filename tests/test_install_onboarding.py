"""Installer opens the onboarding tour + marketing site pages.

The installer change is small but sits on the path every new user takes, so
these tests pin the decision itself (not just "the script parses"):
first-time installs open /?onboarding=1, users who already finished the tour
land on the plain dashboard, and the site pages carry the real one-liner.

Runs without network or Docker: install.sh is sourced, never executed, and
the URL probe uses a local stub `open` binary plus a bound TCP socket.
"""
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
INSTALL_PS1 = REPO / "scripts" / "install.ps1"
SITE_INDEX = REPO / "site" / "index.html"
SITE_INSTALL = REPO / "site" / "install" / "index.html"
SITE_CSS = REPO / "site" / "assets" / "css" / "site.css"
SITE_JS = REPO / "site" / "assets" / "js" / "site.js"

CURL_LINE = (
    "curl -fsSL https://raw.githubusercontent.com/amirfish1/"
    "claude-command-center/main/scripts/install.sh"
)


def _open_url(home: Path) -> str:
    """Source install.sh under an isolated HOME and ask which URL it opens."""
    return subprocess.run(
        ["bash", "-c", 'source "$1" && dashboard_open_url', "_", str(INSTALL_SH)],
        env={**os.environ, "HOME": str(home)},
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_state(home: Path, text: str) -> None:
    state_dir = home / ".claude" / "command-center"
    state_dir.mkdir(parents=True)
    (state_dir / "onboarding.json").write_text(text)


def test_install_sh_is_valid_bash():
    subprocess.run(["bash", "-n", str(INSTALL_SH)], check=True)


def test_fresh_install_opens_onboarding_tour(tmp_path):
    url = _open_url(tmp_path)
    assert url.endswith("/?onboarding=1"), url


def test_completed_onboarding_opens_plain_dashboard(tmp_path):
    _write_state(tmp_path, '{"completed": true, "completed_at": "2026-10-06T00:00:00Z"}')
    url = _open_url(tmp_path)
    assert url.endswith(":8090"), url
    assert "onboarding" not in url, url


def test_unfinished_onboarding_replays_tour(tmp_path):
    _write_state(tmp_path, '{"completed": false}')
    assert _open_url(tmp_path).endswith("/?onboarding=1")


def test_malformed_state_file_falls_back_to_tour(tmp_path):
    _write_state(tmp_path, "{not json at all")
    assert _open_url(tmp_path).endswith("/?onboarding=1")


def test_custom_port_is_kept(tmp_path):
    out = subprocess.run(
        ["bash", "-c", 'source "$1" && dashboard_open_url', "_", str(INSTALL_SH)],
        env={**os.environ, "HOME": str(tmp_path), "PORT": "9011"},
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "http://localhost:9011/?onboarding=1", out


def test_open_when_ready_passes_the_url_to_the_browser(tmp_path):
    """The background watcher really opens the URL it was handed.

    A stub `open` binary records its argument; a bound TCP socket stands in
    for the dashboard so the watcher does not poll for a full minute.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def acceptor():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            conn.close()

    thread = threading.Thread(target=acceptor, daemon=True)
    thread.start()
    try:
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        log = tmp_path / "opened.txt"
        stub = stub_dir / "open"
        stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$OPEN_LOG"\n')
        stub.chmod(0o755)
        script = (
            f'source "{INSTALL_SH}" && '
            f'open_when_ready "http://localhost:{port}/?onboarding=1"; wait'
        )
        subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "PATH": f"{stub_dir}:{os.environ['PATH']}",
                "PORT": str(port),
                "OPEN_LOG": str(log),
            },
            capture_output=True, text=True, timeout=15, check=True,
        )
        assert log.read_text().strip() == f"http://localhost:{port}/?onboarding=1"
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=2)


def test_install_ps1_mirrors_the_decision():
    src = INSTALL_PS1.read_text()
    assert "onboarding.json" in src
    assert "/?onboarding=1" in src
    assert ".completed" in src


def test_progress_ui_present_in_install_sh():
    src = INSTALL_SH.read_text()
    for marker in ("Step 1 of 4", "Step 2 of 4", "Step 3 of 4", "Step 4 of 4"):
        assert marker in src, marker


def test_site_home_hero_promises_free_five_minutes():
    html = SITE_INDEX.read_text()
    assert "free ai dev team" in html.lower()
    assert "5 minutes" in html
    assert CURL_LINE in html
    assert 'href="/install/"' in html


def test_site_home_has_css_only_demo():
    html = SITE_INDEX.read_text()
    assert 'class="demo' in html
    assert 'role="img"' in html and 'aria-label=' in html
    # The animated beats: typed command, four installer steps, tour checklist,
    # card sliding to Done, savings flipbook, confetti.
    for marker in ("t-typed", "t-line l4", "tour-card", "mini-card", "save-line", "demo-confetti"):
        assert marker in html, marker
    # And it must not load any external animation asset.
    demo = html.split('class="demo', 1)[1].split("</section>", 1)[0]
    assert "<script" not in demo and "<img" not in demo and "<video" not in demo


def test_site_install_page_covers_the_journey():
    html = SITE_INSTALL.read_text()
    assert CURL_LINE in html
    assert "install.ps1 | iex" in html
    assert "brew tap amirfish1/ccc" in html
    assert "brew install ccc" in html
    # Novice path: open Terminal, paste, tour.
    for needle in ("Terminal", "setup tour", "data-copy", "<details class=\"faq\">"):
        assert needle in html, needle
    # Free-model promise and the keyless-provider disclosure.
    assert "$0" in html
    assert "logs prompts" in html


def test_site_pages_share_the_design_system():
    for page in (SITE_INDEX, SITE_INSTALL):
        html = page.read_text()
        assert '/assets/css/site.css' in html, page
        assert '/assets/js/site.js' in html, page


def test_demo_respects_reduced_motion():
    css = SITE_CSS.read_text()
    assert "prefers-reduced-motion" in css
    assert ".demo" in css
    assert "@keyframes" in css


def test_copy_button_behavior_in_site_js():
    js = SITE_JS.read_text()
    assert "data-copy" in js
    assert "clipboard" in js


def test_no_em_dash_in_install_page_copy():
    # Novice-facing copy stays in plain punctuation.
    assert "—" not in SITE_INSTALL.read_text()
