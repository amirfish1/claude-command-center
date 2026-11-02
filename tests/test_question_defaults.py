"""Question counter safety and default instruction consent integration."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from ccc_server import config_consent as cc
from ccc_server.question_defaults import guidance, install_helper

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "q-next.py"
spec = importlib.util.spec_from_file_location("question_counter", SCRIPT)
counter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(counter)


def context(tmp_path):
    return cc.Ctx(home=tmp_path, state_dir=tmp_path / "state", ccc_root=ROOT,
                  hook_scripts_dir=tmp_path / "hooks", codex_present=True)


def test_parallel_processes_preserve_counter(tmp_path):
    (tmp_path / "counter").write_text("93\n")
    env = dict(os.environ, Q_NEXT_DIR=str(tmp_path))
    def reserve(_):
        return int(subprocess.check_output([sys.executable, str(SCRIPT)], env=env))
    with ThreadPoolExecutor(max_workers=12) as pool:
        numbers = list(pool.map(reserve, range(40)))
    assert sorted(numbers) == list(range(94, 134))
    assert (tmp_path / "counter").read_text() == "133\n"


def test_bad_state_and_lock_never_reset(tmp_path):
    import pytest
    for value in ("", "oops", "-1", "4.2"):
        (tmp_path / "counter").write_text(value)
        with pytest.raises(ValueError):
            counter.next_number(tmp_path)
        assert (tmp_path / "counter").read_text() == value
    (tmp_path / "lock.d").mkdir()
    with pytest.raises(RuntimeError):
        counter.next_number(tmp_path, timeout=0)
    assert (tmp_path / "lock.d").is_dir()
    (tmp_path / "lock.d").rmdir()
    (tmp_path / "counter").unlink()
    assert counter.next_number(tmp_path) == 1
    (tmp_path / "counter").unlink()
    with pytest.raises(ValueError):
        counter.next_number(tmp_path)


def test_consent_symlink_exact_revoke_and_codex_home(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex"))
    for engine in ("claude", "codex"):
        item = cc.QuestionGuidance(engine)
        target = item.targets(ctx)[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        real = tmp_path / f"{engine}-real.md"
        original = "# Personal instructions\nKeep this. café"  # no final newline
        real.write_text(original)
        target.symlink_to(real)
        assert cc.decide({item.id: "approve"}, ctx=ctx)["ok"]
        after = real.read_text()
        assert guidance() in after and after.endswith(original)
        assert target.is_symlink()
        assert item.apply(ctx) is None
        assert real.read_text() == after
        assert list((ctx.state_dir / cc.BACKUP_DIR_NAME).iterdir())
        assert cc.revoke([item.id], ctx=ctx)["ok"]
        assert real.read_text() == original
        assert target.is_symlink()


def test_existing_private_rule_untouched(tmp_path):
    ctx = context(tmp_path)
    item = cc.QuestionGuidance("claude")
    target = item.targets(ctx)[0]
    target.parent.mkdir()
    original = "Custom: run q-next before [Q<n>:slug].\n"
    target.write_text(original)
    assert item.apply(ctx) is None
    assert item.revoke(ctx) is None
    assert target.read_text() == original


def test_startup_offers_guidance_without_editing_globals(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    monkeypatch.setenv("PATH", "")
    result = cc.startup(ctx, log=None)
    assert "claude-question-guidance" in result["pending"]
    assert "codex-question-guidance" in result["pending"]
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()
    assert (ctx.state_dir / "bin" / "q-next.py").exists()
    link = tmp_path / ".local" / "bin" / "q-next"
    assert link.exists()
    before = link.lstat().st_mtime_ns
    install_helper(ROOT, tmp_path, ctx.state_dir)
    assert link.lstat().st_mtime_ns == before


def test_custom_helper_preserved(tmp_path):
    helper = tmp_path / ".local" / "bin" / "q-next"
    helper.parent.mkdir(parents=True)
    helper.write_text("custom helper")
    install_helper(ROOT, tmp_path)
    assert helper.read_text() == "custom helper"


def test_install_and_prompt_entrypoints():
    assert 'question_defaults.py' in (ROOT / "scripts/link-ccc-cli.sh").read_text()
    for filename in ("scripts/install.sh", "run.sh"):
        assert "link-ccc-cli.sh" in (ROOT / filename).read_text()
    assert "question_defaults.py" in (ROOT / "run.ps1").read_text()
    assert '"run.ps1"' in (ROOT / "scripts/install.ps1").read_text()
    import server
    assert guidance() in server._CCC_SESSION_STATE_SYSTEM_PROMPT


def test_shell_install_really_exposes_executable(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), PATH="/usr/bin:/bin",
               CCC_CLI_SOURCE_DIR=str(ROOT), Q_NEXT_DIR=str(tmp_path / "numbers"))
    for _ in range(2):
        subprocess.run(["bash", str(ROOT / "scripts/link-ccc-cli.sh")], env=env, check=True,
                       capture_output=True)
    executable = tmp_path / ".local/bin/q-next"
    assert subprocess.check_output([str(executable)], env=env).strip() == b"1"
    assert subprocess.check_output([str(executable)], env=env).strip() == b"2"


def test_managed_updates_and_malformed_block(tmp_path):
    ctx = context(tmp_path)
    item = cc.QuestionGuidance("claude")
    assert item.apply(ctx) is None
    target = item.targets(ctx)[0]
    target.write_text(target.read_text().replace("## User question tags", "## Old guidance"))
    assert item.apply(ctx) is None
    assert "## Old guidance" not in target.read_text()
    assert target.read_text().count("CCC:user-question-tags") == 2
    target.write_text("<!-- CCC:user-question-tags -->\nIncomplete")
    before = target.read_bytes()
    assert item.apply(ctx)
    assert item.revoke(ctx)
    assert target.read_bytes() == before


def test_crlf_surrounding_text_preserved(tmp_path):
    ctx = context(tmp_path)
    item = cc.QuestionGuidance("claude")
    target = item.targets(ctx)[0]
    target.parent.mkdir(parents=True)
    original = b"# Custom\r\nKeep\r\n"
    target.write_bytes(original)
    assert item.apply(ctx) is None
    assert target.read_bytes().endswith(original)
    assert item.revoke(ctx) is None
    assert target.read_bytes() == original
