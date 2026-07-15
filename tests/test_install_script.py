"""Smoke tests for scripts/install.sh.

The bar matches `tests/test_smoke.py`: existence, executable bit, sane
shebang, optional shellcheck pass. We don't exercise the full script
end-to-end (it clones a repo and launches a server), but we do exercise
``parse_channel`` directly so attribution wiring can't silently regress —
see the `CCC_FROM` / `--from=<channel>` resolution tests below.
"""
import glob
import os
import shutil
import stat
import subprocess
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "install.sh")


def _run_parse_channel(env_extra=None, args=()):
    """Invoke ``parse_channel`` from install.sh in isolation.

    We source the script after stubbing out ``main`` to a no-op, then call
    ``parse_channel`` with the provided argv. ``env_extra`` lets a caller
    set ``CCC_FROM`` (or explicitly unset it) for the child shell.
    Returns the function's stdout, stripped.
    """
    env = os.environ.copy()
    # Default: clear any inherited CCC_FROM so tests aren't polluted.
    env.pop("CCC_FROM", None)
    if env_extra:
        for k, v in env_extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    # install.sh's trailing `main` invocation is guarded by a
    # `BASH_SOURCE != $0` check, so sourcing it from `bash -c` defines the
    # functions without running the installer.
    bash_program = (
        f'source "{INSTALL_SCRIPT}"; '
        'parse_channel "$@"'
    )
    result = subprocess.run(
        ["bash", "-c", bash_program, "bash", *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"parse_channel exited {result.returncode}\n"
        f"STDOUT: {result.stdout!r}\nSTDERR: {result.stderr!r}"
    )
    return result.stdout.strip()


def _run_install_script_function(function_call, prelude="", env_extra=None):
    """Source install.sh and invoke one function without running main."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    bash_program = (
        f'{prelude}\n'
        f'source "{INSTALL_SCRIPT}"; '
        f'{function_call}'
    )
    return subprocess.run(
        ["bash", "-c", bash_program],
        capture_output=True,
        text=True,
        env=env,
    )


class TestInstallScript(unittest.TestCase):
    def test_install_script_exists(self):
        self.assertTrue(
            os.path.isfile(INSTALL_SCRIPT),
            "scripts/install.sh must exist",
        )

    def test_install_script_is_executable(self):
        mode = os.stat(INSTALL_SCRIPT).st_mode
        self.assertTrue(
            mode & stat.S_IXUSR,
            "scripts/install.sh must have the executable bit set",
        )

    def test_install_script_has_bash_shebang(self):
        with open(INSTALL_SCRIPT, "rb") as fh:
            first_line = fh.readline().rstrip(b"\n").decode("utf-8", "replace")
        self.assertIn(
            first_line,
            ("#!/usr/bin/env bash", "#!/bin/bash"),
            f"unexpected shebang: {first_line!r}",
        )

    def test_install_script_passes_shellcheck_when_available(self):
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck not installed; skipping lint check")
        result = subprocess.run(
            ["shellcheck", INSTALL_SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"shellcheck failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )

    def test_platform_gate_allows_macos_and_linux(self):
        for platform in ("Darwin", "Linux"):
            with self.subTest(platform=platform):
                result = _run_install_script_function(
                    "require_supported_platform",
                    prelude=f'uname() {{ printf "{platform}"; }}',
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{platform} should be accepted\n"
                    f"STDOUT: {result.stdout!r}\nSTDERR: {result.stderr!r}",
                )

    def test_platform_gate_rejects_unknown_os(self):
        result = _run_install_script_function(
            "require_supported_platform",
            prelude='uname() { printf "FreeBSD"; }',
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("macOS or Linux", result.stderr)


class TestInstallBehavior(unittest.TestCase):
    PUBLIC_REPO_URL = "https://github.com/amirfish1/claude-command-center"

    def test_app_mode_is_explicit(self):
        result = _run_install_script_function(
            "is_app_install",
            env_extra={"CCC_INSTALL_MODE": "app"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_mode_is_not_app(self):
        result = _run_install_script_function("is_app_install")
        self.assertNotEqual(result.returncode, 0)

    def test_environment_overrides_install_location_and_repository(self):
        result = _run_install_script_function(
            'printf "%s\\n%s" "$INSTALL_DIR" "$REPO_URL"',
            env_extra={
                "CCC_INSTALL_DIR": "/tmp/ccc-test-install",
                "CCC_REPO_URL": "/tmp/ccc-test-repository",
            },
        )
        self.assertEqual(
            result.stdout,
            "/tmp/ccc-test-install\n/tmp/ccc-test-repository",
        )

    def _create_repository(self, root):
        repo = os.path.join(root, "source")
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                repo,
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", repo, "config", "user.name", "CCC Test"],
            check=True,
        )
        with open(
            os.path.join(repo, "sentinel.txt"), "w", encoding="utf-8"
        ) as fh:
            fh.write("installed\n")
        subprocess.run(
            ["git", "-C", repo, "add", "sentinel.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", repo, "commit", "-qm", "test fixture"],
            check=True,
        )
        return repo

    def _isolated_git_env(self, root, fallback_repo):
        """Keep pre-override RED runs away from the real home and network."""
        return {
            "HOME": root,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.{fallback_repo}.insteadOf",
            "GIT_CONFIG_VALUE_0": self.PUBLIC_REPO_URL,
        }

    def test_new_clone_is_published_only_after_git_succeeds(self):
        with tempfile.TemporaryDirectory() as root:
            repo = self._create_repository(root)
            destination = os.path.join(root, "installed", "ccc")
            env = self._isolated_git_env(root, repo)
            env.update(
                {"CCC_INSTALL_DIR": destination, "CCC_REPO_URL": repo}
            )
            result = _run_install_script_function(
                "sync_repo",
                env_extra=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isdir(os.path.join(destination, ".git")))
            with open(
                os.path.join(destination, "sentinel.txt"), encoding="utf-8"
            ) as fh:
                self.assertEqual(fh.read(), "installed\n")
            self.assertEqual(glob.glob(destination + ".installing.*"), [])

    def test_failed_clone_leaves_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as root:
            fallback_repo = self._create_repository(root)
            destination = os.path.join(root, "installed", "ccc")
            env = self._isolated_git_env(root, fallback_repo)
            env.update(
                {
                    "CCC_INSTALL_DIR": destination,
                    "CCC_REPO_URL": os.path.join(root, "missing-repository"),
                }
            )
            result = _run_install_script_function(
                "sync_repo",
                env_extra=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(os.path.exists(destination))
            self.assertEqual(glob.glob(destination + ".installing.*"), [])

    def test_non_git_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            fallback_repo = self._create_repository(root)
            destination = os.path.join(root, "installed", "ccc")
            os.makedirs(destination)
            sentinel = os.path.join(destination, "keep-me.txt")
            with open(sentinel, "w", encoding="utf-8") as fh:
                fh.write("preserve\n")
            env = self._isolated_git_env(root, fallback_repo)
            env.update(
                {
                    "CCC_INSTALL_DIR": destination,
                    "CCC_REPO_URL": os.path.join(root, "unused"),
                }
            )
            result = _run_install_script_function(
                "sync_repo",
                env_extra=env,
            )
            self.assertNotEqual(result.returncode, 0)
            with open(sentinel, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "preserve\n")


class TestParseChannel(unittest.TestCase):
    """Channel resolution: --from=<flag> > CCC_FROM env > 'unknown'."""

    def test_no_input_defaults_to_unknown(self):
        self.assertEqual(_run_parse_channel(), "unknown")

    def test_env_var_only(self):
        self.assertEqual(
            _run_parse_channel(env_extra={"CCC_FROM": "hn"}),
            "hn",
        )

    def test_flag_only(self):
        self.assertEqual(
            _run_parse_channel(args=("--from=readme",)),
            "readme",
        )

    def test_flag_overrides_env_var(self):
        self.assertEqual(
            _run_parse_channel(
                env_extra={"CCC_FROM": "hn"},
                args=("--from=readme",),
            ),
            "readme",
        )

    def test_garbage_env_var_falls_back_to_unknown(self):
        self.assertEqual(
            _run_parse_channel(env_extra={"CCC_FROM": "bogus-channel"}),
            "unknown",
        )

    def test_garbage_flag_falls_back_to_unknown(self):
        self.assertEqual(
            _run_parse_channel(args=("--from=bogus-channel",)),
            "unknown",
        )

    def test_all_documented_channels_round_trip(self):
        for channel in (
            "readme",
            "landing-hero",
            "hn",
            "ph",
            "devto",
            "yt",
            "gh-trending",
            "unknown",
        ):
            with self.subTest(channel=channel):
                self.assertEqual(
                    _run_parse_channel(env_extra={"CCC_FROM": channel}),
                    channel,
                )


if __name__ == "__main__":
    unittest.main()
