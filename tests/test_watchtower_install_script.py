"""Decision-logic tests for scripts/install-watchtower.sh.

The script itself is all side effects — pip, git, launchd — so the tests drive
it against stubbed `python3`, `git` and `wt` binaries on PATH and assert on
what it *tried to do*, in what order. That is where the interesting rules live:

  - PyPI is attempted last, never before the repo sources.
  - A dev checkout is never pulled (it is somebody's working tree).
  - The CCC-managed clone is fast-forwarded at most once a day.
  - Every failure path still exits 0, because CCC must start regardless.

Plus the usual static bar from `tests/test_install_script.py`: shebang,
executable bit, shellcheck.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WT_SCRIPT = PROJECT_ROOT / "scripts" / "install-watchtower.sh"
RUN_SCRIPT = PROJECT_ROOT / "run.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install.sh"
README = PROJECT_ROOT / "README.md"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
CI_WATCHTOWER_REF = "221260bbc5d31ae57eeacde542154c1c9ad5f3c6"


FAKE_PYTHON = r"""#!/usr/bin/env bash
# Stand-in for the interpreter CCC would install into. Dispatches on the exact
# probes install-watchtower.sh runs, and records pip/daemon calls.
log() { printf '%s\n' "$*" >> "$WT_FAKE_LOG"; }

if [ "$1" = "-c" ]; then
  code="$2"
  case "$code" in
    *"watchtower.__file__"*)
      if [ ! -f "$WT_FAKE_INSTALLED" ]; then exit 1; fi
      printf '%s\n' "${WT_FAKE_ROOT:-}"
      exit 0
      ;;
    *"import watchtower.queue"*)
      if [ -f "$WT_FAKE_INSTALLED" ]; then exit 0; fi
      if [ -f "$WT_FAKE_SITE_PACKAGES/ccc-watchtower.pth" ]; then
        source_dir="$(head -n 1 "$WT_FAKE_SITE_PACKAGES/ccc-watchtower.pth")"
        if [ -f "$source_dir/watchtower/queue.py" ]; then exit 0; fi
      fi
      exit 1
      ;;
    *"import watchtower"*)
      if [ -f "$WT_FAKE_INSTALLED" ] || [ "${WT_FAKE_PARTIAL_PACKAGE:-0}" = "1" ]; then exit 0; fi
      exit 1
      ;;
    *"version_info"*)
      exit "${WT_FAKE_VERSION_RC:-0}"
      ;;
    *"platform.python_version"*)
      printf '%s\n' "${WT_FAKE_VERSION:-3.9.6}"
      exit 0
      ;;
    *"getusersitepackages"*)
      printf '%s\n' "$WT_FAKE_SITE_PACKAGES"
      exit 0
      ;;
    *"sys.prefix"*)
      printf '%s\n' "${WT_FAKE_IN_VENV:-0}"
      exit 0
      ;;
    *"sysconfig"*)
      printf '%s\n' "$HOME/.local/bin"
      exit 0
      ;;
  esac
  exit 0
fi

if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  log "pip $*"
  for pat in ${WT_FAKE_PIP_FAIL:-}; do
    case "$*" in
      *"$pat"*) exit 1 ;;
    esac
  done
  if [ "${WT_FAKE_PIP_MAKES_IMPORTABLE:-1}" = "1" ]; then : > "$WT_FAKE_INSTALLED"; fi
  exit 0
fi

if [ "$1" = "-m" ] && [ "$2" = "watchtower.cli" ]; then
  log "wtcli $*"
  exit 0
fi
exit 0
"""

FAKE_GIT = r"""#!/usr/bin/env bash
log() { printf '%s\n' "$*" >> "$WT_FAKE_LOG"; }
log "git $*"
case "$*" in
  *"rev-parse HEAD"*)
    printf '%s\n' "${WT_FAKE_GIT_HEAD:-${WATCHTOWER_REF:-}}"
    exit 0
    ;;
  *"rev-list"*)
    printf '%s\n' "${WT_FAKE_BEHIND:-0}"
    exit 0
    ;;
  *"pull"*)
    exit "${WT_FAKE_GIT_PULL_RC:-0}"
    ;;
  *"clone"*)
    if [ "${WT_FAKE_GIT_CLONE_RC:-0}" != "0" ]; then exit 1; fi
    dest="${!#}"
    mkdir -p "$dest/.git"
    mkdir -p "$dest/watchtower"
    : > "$dest/pyproject.toml"
    : > "$dest/watchtower/__init__.py"
    : > "$dest/watchtower/queue.py"
    exit 0
    ;;
esac
exit 0
"""

FAKE_WT = r"""#!/usr/bin/env bash
printf 'wt %s\n' "$*" >> "$WT_FAKE_LOG"
exit 0
"""


class WatchtowerInstallHarness(unittest.TestCase):
    """Runs the real script with stubbed python3/git/wt on PATH."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.log = self.root / "calls.log"
        self.installed_marker = self.root / "installed"
        self.site_packages = self.root / "site-packages"
        for name, body in (
            ("python3", FAKE_PYTHON),
            ("git", FAKE_GIT),
            ("wt", FAKE_WT),
        ):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.addCleanup(self._tmp.cleanup)

    def make_checkout(self, path, git=True):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text("[project]\nname = 'watchtower'\n")
        (path / "watchtower").mkdir(exist_ok=True)
        (path / "watchtower" / "__init__.py").write_text("")
        (path / "watchtower" / "queue.py").write_text("")
        if git:
            (path / ".git").mkdir(exist_ok=True)
        return path

    def set_installed(self, root):
        """Pretend `import watchtower` already works, resolving under `root`."""
        self.installed_marker.write_text("")
        return str(root)

    def run_script(self, env_extra=None, installed_root=None, expected_returncode=0):
        env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "WT_FAKE_LOG": str(self.log),
            "WT_FAKE_INSTALLED": str(self.installed_marker),
            "WT_FAKE_SITE_PACKAGES": str(self.site_packages),
            "WT_FAKE_ROOT": installed_root or "",
            "CCC_PYTHON": "python3",
        }
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        result = subprocess.run(
            ["bash", str(WT_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(
            result.returncode,
            expected_returncode,
            "unexpected installer exit status\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        return result

    def calls(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text().splitlines() if line]

    def pip_targets(self):
        return [line for line in self.calls() if line.startswith("pip ")]


class TestStatics(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(WT_SCRIPT.is_file(), "scripts/install-watchtower.sh must exist")
        self.assertTrue(
            os.stat(WT_SCRIPT).st_mode & stat.S_IXUSR,
            "scripts/install-watchtower.sh must have the executable bit set",
        )

    def test_bash_shebang(self):
        first_line = WT_SCRIPT.read_text().splitlines()[0]
        self.assertIn(first_line, ("#!/usr/bin/env bash", "#!/bin/bash"))

    def test_shellcheck_clean(self):
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck not installed; skipping lint check")
        result = subprocess.run(
            ["shellcheck", str(WT_SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"shellcheck failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )

    def test_pypi_is_documented_as_the_last_resort(self):
        body = WT_SCRIPT.read_text()
        self.assertLess(
            body.index("WATCHTOWER_TARBALL_URL\" && wt_finish"),
            body.index("WATCHTOWER_PYPI_NAME\" && wt_finish"),
            "the PyPI attempt must come after the tarball attempt",
        )


class TestAlreadyInstalled(WatchtowerInstallHarness):
    def test_pinned_mode_rejects_importable_package_at_wrong_commit(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")

        result = self.run_script(
            installed_root=self.set_installed(managed),
            env_extra={
                "WATCHTOWER_REF": CI_WATCHTOWER_REF,
                "WT_FAKE_GIT_HEAD": "0" * 40,
            },
            expected_returncode=1,
        )

        self.assertIn("does not match pinned commit", result.stdout)
        self.assertEqual(self.pip_targets(), [])

    def test_partial_package_without_queue_is_not_treated_as_installed(self):
        result = self.run_script(env_extra={"WT_FAKE_PARTIAL_PACKAGE": "1"})

        self.assertTrue(any("clone" in call for call in self.calls()))
        self.assertIn("watchtower.queue is now available", result.stdout)

    def test_dev_checkout_is_never_pulled(self):
        """A dev checkout is a working tree: report, don't touch."""
        dev = self.make_checkout(self.root / "dev-watchtower")
        result = self.run_script(
            env_extra={"WATCHTOWER_DIR": str(dev), "WT_FAKE_BEHIND": "3"},
            installed_root=self.set_installed(dev),
        )
        pulls = [c for c in self.calls() if c.startswith("git ") and "pull" in c]
        self.assertEqual(pulls, [], f"a dev checkout must never be pulled: {pulls}")
        self.assertIn("3 commit(s) behind", result.stdout)
        self.assertIn("not pulling it", result.stdout)

    def test_dev_checkout_up_to_date_says_nothing(self):
        dev = self.make_checkout(self.root / "dev-watchtower")
        result = self.run_script(
            env_extra={"WATCHTOWER_DIR": str(dev), "WT_FAKE_BEHIND": "0"},
            installed_root=self.set_installed(dev),
        )
        self.assertNotIn("behind", result.stdout)
        self.assertEqual([c for c in self.calls() if "pull" in c], [])

    def test_managed_clone_is_pulled_at_most_once_a_day(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        root = self.set_installed(managed)
        self.run_script(installed_root=root)
        first = [c for c in self.calls() if "pull" in c]
        self.assertEqual(len(first), 1, f"expected exactly one pull, got {first}")

        self.run_script(installed_root=root)
        second = [c for c in self.calls() if "pull" in c]
        self.assertEqual(
            second,
            first,
            "the second launch on the same day must not pull again",
        )

    def test_force_overrides_the_daily_rate_limit(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        root = self.set_installed(managed)
        self.run_script(installed_root=root)
        self.run_script(installed_root=root, env_extra={"CCC_WATCHTOWER_FORCE": "1"})
        self.assertEqual(len([c for c in self.calls() if "pull" in c]), 2)

    def test_ccc_version_change_overrides_the_daily_rate_limit(self):
        """An upgraded CCC must not wait out today's rate-limit window.

        A `brew upgrade`/Sparkle/`git pull` relaunch never sets
        CCC_WATCHTOWER_FORCE (only an explicit install does), so without this
        WatchTower could sit stale for up to a day after CCC itself moved.
        """
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        root = self.set_installed(managed)
        self.run_script(installed_root=root, env_extra={"CCC_VERSION": "5.21.0"})
        self.assertEqual(len([c for c in self.calls() if "pull" in c]), 1)

        # Same version, same day: still rate-limited, no new pull or wt calls.
        before = len(self.calls())
        self.run_script(installed_root=root, env_extra={"CCC_VERSION": "5.21.0"})
        self.assertEqual(len(self.calls()), before, "same-version relaunch must be a no-op")

        # CCC just upgraded: bypasses the rate limit even though it's the same day,
        # both re-pulling the clone AND restarting the daemon (not just a no-op start).
        before_wt = [c for c in self.calls() if c.startswith("wt ")]
        self.run_script(installed_root=root, env_extra={"CCC_VERSION": "5.22.0"})
        self.assertEqual(len([c for c in self.calls() if "pull" in c]), 2)
        after_wt = [c for c in self.calls() if c.startswith("wt ")]
        self.assertGreater(
            len(after_wt), len(before_wt), "version bump must also re-ensure the daemon"
        )
        self.assertIn("wt start", after_wt)

    def test_daemon_is_ensured_on_the_installed_path(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        self.run_script(installed_root=self.set_installed(managed))
        self.assertIn("wt start", self.calls())

    def test_nothing_runs_when_opted_out(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        self.run_script(
            installed_root=self.set_installed(managed),
            env_extra={"CCC_SKIP_WATCHTOWER": "1"},
        )
        self.assertEqual(self.calls(), [])

    def test_installed_path_makes_no_pip_calls(self):
        managed = self.make_checkout(self.home / ".ccc" / "watchtower")
        self.run_script(installed_root=self.set_installed(managed))
        self.assertEqual(self.pip_targets(), [])


class TestInstallChain(WatchtowerInstallHarness):
    def test_dev_checkout_wins_and_is_installed_editable(self):
        dev = self.make_checkout(self.root / "dev-watchtower")
        result = self.run_script(env_extra={"WATCHTOWER_DIR": str(dev)})
        pips = self.pip_targets()
        self.assertEqual(len(pips), 1, pips)
        self.assertIn(f"-e {dev}", pips[0])
        self.assertEqual(
            [c for c in self.calls() if "clone" in c],
            [],
            "a usable dev checkout must not trigger a clone",
        )
        self.assertIn("wt start", self.calls())
        self.assertIn("watchtower.queue is now available", result.stdout)

    def test_empty_candidate_directory_is_not_treated_as_a_checkout(self):
        """~/dev/watchtower with no pyproject.toml would send pip at nothing."""
        (self.home / "dev" / "watchtower").mkdir(parents=True)
        self.run_script()
        pips = self.pip_targets()
        self.assertEqual(len(pips), 1, pips)
        self.assertIn(str(self.home / ".ccc" / "watchtower"), pips[0])

    def test_clone_is_used_when_no_dev_checkout_exists(self):
        self.run_script()
        clones = [c for c in self.calls() if "clone" in c]
        self.assertEqual(len(clones), 1, clones)
        self.assertIn("--depth 1", clones[0])
        self.assertIn(f"-e {self.home / '.ccc' / 'watchtower'}", self.pip_targets()[0])

    def test_explicit_ref_is_checked_out_immutably(self):
        self.run_script(env_extra={"WATCHTOWER_REF": CI_WATCHTOWER_REF})

        calls = self.calls()
        clone = next(call for call in calls if " clone " in f" {call} ")
        self.assertNotIn("--depth 1", clone)
        self.assertTrue(any(
            "checkout --detach" in call and CI_WATCHTOWER_REF in call
            for call in calls
        ), calls)

    def test_pinned_clone_failure_never_uses_mutable_fallbacks(self):
        result = self.run_script(
            env_extra={
                "WATCHTOWER_REF": CI_WATCHTOWER_REF,
                "WT_FAKE_GIT_CLONE_RC": "1",
            },
            expected_returncode=1,
        )

        self.assertIn("could not install pinned WatchTower commit", result.stdout)
        self.assertFalse(any("main.tar.gz" in p for p in self.pip_targets()))
        self.assertFalse(any("watchtower-cli" in p for p in self.pip_targets()))

    def test_source_tree_is_activated_when_pip_is_unavailable(self):
        """Minimal Python installs may have no pip, but WatchTower has no
        runtime dependencies. A .pth activation must make the cloned source
        importable before CCC starts."""
        dev = self.make_checkout(self.root / "dev-watchtower")

        result = self.run_script(env_extra={
            "WATCHTOWER_DIR": str(dev),
            "WT_FAKE_PIP_FAIL": "dev-watchtower",
        })

        pth = self.site_packages / "ccc-watchtower.pth"
        self.assertTrue(
            pth.is_file(),
            "source fallback must create a .pth file\n"
            f"stdout={result.stdout}\ncalls={self.calls()}",
        )
        self.assertEqual(pth.read_text().strip(), str(dev.resolve()))
        self.assertIn("watchtower.queue is now available", result.stdout)
        self.assertFalse(any("main.tar.gz" in p for p in self.pip_targets()))

    def test_managed_clone_is_activated_when_pip_is_unavailable(self):
        managed = self.home / ".ccc" / "watchtower"

        result = self.run_script(env_extra={
            "WT_FAKE_PIP_FAIL": str(managed),
        })

        clones = [call for call in self.calls() if "clone" in call]
        self.assertEqual(len(clones), 1, clones)
        pth = self.site_packages / "ccc-watchtower.pth"
        self.assertTrue(pth.is_file())
        self.assertEqual(pth.read_text().strip(), str(managed.resolve()))
        self.assertIn("watchtower.queue is now available", result.stdout)
        self.assertFalse(any("main.tar.gz" in p for p in self.pip_targets()))

    def test_pypi_is_the_last_resort_after_clone_and_tarball(self):
        result = self.run_script(
            env_extra={
                "WT_FAKE_GIT_CLONE_RC": "1",
                "WT_FAKE_PIP_FAIL": "main.tar.gz",
            }
        )
        pips = self.pip_targets()
        self.assertTrue(
            any("main.tar.gz" in p for p in pips), f"tarball never tried: {pips}"
        )
        self.assertTrue(
            any("watchtower-cli" in p for p in pips), f"PyPI never tried: {pips}"
        )
        first_pypi = next(i for i, p in enumerate(pips) if "watchtower-cli" in p)
        last_tarball = max(i for i, p in enumerate(pips) if "main.tar.gz" in p)
        self.assertGreater(first_pypi, last_tarball, pips)
        self.assertIn("last resort", result.stdout)

    def test_python_below_39_skips_with_a_reason_and_no_pip(self):
        result = self.run_script(
            env_extra={"WT_FAKE_VERSION_RC": "1", "WT_FAKE_VERSION": "3.8.20"}
        )
        self.assertIn("3.8.20", result.stdout)
        self.assertIn("3.9 minimum", result.stdout)
        self.assertEqual(self.pip_targets(), [])

    def test_python_below_39_says_it_once_a_day_not_once_a_launch(self):
        """The 3.9 notice is unactionable and CCC restarts often.

        run.sh calls this script on every launch and the import probe can never
        succeed on an old interpreter, so without a back-off the same two lines
        print on every single start until the user upgrades Python — which is
        how a real notice becomes noise people stop reading.
        """
        env = {"WT_FAKE_VERSION_RC": "1", "WT_FAKE_VERSION": "3.8.20"}
        first = self.run_script(env_extra=env)
        self.assertIn("3.9 minimum", first.stdout)

        second = self.run_script(env_extra=env)
        self.assertNotIn(
            "3.9 minimum",
            second.stdout,
            "the Python-floor notice must back off like every other dead end",
        )
        self.assertEqual(self.pip_targets(), [])

        # An explicit install (or the in-app updater) is the user asking right
        # now, so it must still get the reason instead of a silent no-op.
        forced = self.run_script(env_extra=dict(env, CCC_WATCHTOWER_FORCE="1"))
        self.assertIn("3.9 minimum", forced.stdout)

    def test_total_failure_warns_loudly_and_backs_off_for_a_day(self):
        env = {
            "WT_FAKE_GIT_CLONE_RC": "1",
            "WT_FAKE_PIP_FAIL": "watchtower",  # matches every candidate target
        }
        result = self.run_script(env_extra=env)
        self.assertIn("could not install WatchTower", result.stdout)
        self.assertIn("Retry manually:", result.stdout)
        self.assertIn("main.tar.gz", result.stdout)
        self.assertTrue(
            (self.home / ".claude" / "command-center"
             / "watchtower-bootstrap-failed").exists()
        )

        before = len(self.pip_targets())
        self.run_script(env_extra=env)
        self.assertEqual(
            len(self.pip_targets()),
            before,
            "a failed install must not be retried on the next launch",
        )

    def test_pip_exiting_zero_without_a_working_import_counts_as_failure(self):
        """pip can 'succeed' into an interpreter CCC never imports from."""
        dev = self.make_checkout(self.root / "dev-watchtower")
        result = self.run_script(
            env_extra={
                "WATCHTOWER_DIR": str(dev),
                # pip exits 0 but nothing ever becomes importable.
                "WT_FAKE_PIP_MAKES_IMPORTABLE": "0",
            }
        )
        self.assertIn("could not install WatchTower", result.stdout)


class TestWiring(unittest.TestCase):
    """Both entry points must go through the one shared script."""

    def test_run_sh_delegates_and_keeps_the_happy_path_cheap(self):
        body = RUN_SCRIPT.read_text()
        self.assertIn('bash "$script"', body)
        self.assertIn("scripts/install-watchtower.sh", body)
        self.assertIn("import watchtower.queue", body)
        self.assertNotIn(
            "pip install",
            body,
            "run.sh must not carry its own copy of the install chain",
        )

    def test_install_sh_delegates(self):
        body = INSTALL_SCRIPT.read_text()
        self.assertIn("install-watchtower.sh", body)
        self.assertNotIn(
            "pip install",
            body,
            "scripts/install.sh must not carry its own copy of the install chain",
        )

    def test_ci_bootstraps_watchtower_before_importing_server(self):
        body = CI_WORKFLOW.read_text()
        command = (
            f"WATCHTOWER_REF={CI_WATCHTOWER_REF} "
            "CCC_PYTHON=python CCC_SKIP_WATCHTOWER_DAEMON=1 "
            "CCC_WATCHTOWER_FORCE=1 bash scripts/install-watchtower.sh"
        )
        targets = {
            "unittest": ("python -m pytest -q --tb=no tests/", "smoke"),
            "smoke": ("python server.py > /tmp/server.log", "python39-smoke"),
            "python39-smoke": (
                "python -c 'import server; print(server.__version__)'",
                "telemetry-worker",
            ),
        }
        for job, (target, next_job) in targets.items():
            start = body.index(f"  {job}:")
            end = body.index(f"\n  {next_job}:", start)
            block = body[start:end]
            self.assertIn(command, block, f"{job} must pin and bootstrap WatchTower")
            self.assertLess(
                block.index(command),
                block.index(target),
                f"{job} must bootstrap before importing or starting server.py",
            )

    def test_readme_no_longer_claims_installed_by_default(self):
        self.assertNotIn("installed by default as CCC's queue engine", README.read_text())


if __name__ == "__main__":
    unittest.main()
