"""Smoke tests for scripts/install.sh.

The bar matches `tests/test_smoke.py`: existence, executable bit, sane
shebang, optional shellcheck pass. We don't exercise the full script
end-to-end (it clones a repo and launches a server), but we do exercise
``parse_channel`` directly so attribution wiring can't silently regress —
see the `CCC_FROM` / `--from=<channel>` resolution tests below.
"""
import os
import shutil
import stat
import subprocess
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
