"""Installer failures must give an action without touching real user state."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_unwritable_settings_give_next_step(tmp_path):
    # A file in place of the state directory fails even under root in CI.
    (tmp_path / ".claude").write_text("blocked")
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; persist_channel readme', "bash",
         str(ROOT / "scripts/install.sh")],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == (
        "install: Cannot write CCC settings in HOME; use a writable home directory "
        "and run the installer again."
    )


def test_missing_watchtower_installer_stops_with_next_step(tmp_path):
    installer = tmp_path / "install.sh"
    installer.write_text((ROOT / "scripts/install.sh").read_text())
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; install_watchtower', "bash", str(installer)],
        cwd=tmp_path, env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "download a fresh CCC checkout" in result.stderr
    assert "built-in queue" not in result.stdout
