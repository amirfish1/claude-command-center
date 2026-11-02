"""Bundled user-question guidance and installation of CCC-owned tools."""
from pathlib import Path
import os
import shutil
import sys
import tempfile


def guidance(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    return (root / "resources" / "owner-questions.md").read_text(encoding="utf-8").strip()


def install_helper(root, home=None, state_dir=None):
    """Keep custom PATH helpers; install our executable in CCC's own directory."""
    root = Path(root)
    home = Path(home) if home else Path.home()
    state = Path(state_dir) if state_dir else home / ".claude" / "command-center"
    source = root / "scripts" / "q-next.py"
    owned = state / "bin" / "q-next.py"
    owned.parent.mkdir(parents=True, exist_ok=True)
    if not owned.exists() or owned.read_bytes() != source.read_bytes():
        fd, temporary = tempfile.mkstemp(dir=owned.parent, prefix=".q-next-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.read_bytes())
            os.chmod(temporary, 0o755)
            os.replace(temporary, owned)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / ("q-next.cmd" if os.name == "nt" else "q-next")
    # Never replace a custom helper, including one outside our usual bin dir.
    if wrapper.exists() or wrapper.is_symlink() or shutil.which("q-next"):
        return owned
    if os.name == "nt":
        wrapper.write_text('@echo off\n"' + sys.executable + '" "' + str(owned) + '" %*\n', encoding="utf-8")
    else:
        wrapper.symlink_to(owned)
    return owned


if __name__ == "__main__":
    install_helper(Path(__file__).resolve().parent.parent)
