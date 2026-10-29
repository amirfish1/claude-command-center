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
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "install.sh")
PRE_PUSH_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "pre-push.sh")
INSTALL_SMOKE_DOCKERFILE = os.path.join(
    PROJECT_ROOT, "tests", "install-smoke", "Dockerfile"
)


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

    def test_python_gate_accepts_39_and_honors_override(self):
        script = Path(INSTALL_SCRIPT).read_text(encoding="utf-8")

        self.assertIn('PYTHON3="${CCC_PYTHON:-python3}"', script)
        self.assertIn("sys.version_info >= (3, 9)", script)
        self.assertIn("requires Python 3.9+", script)

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

    def test_install_smoke_uses_real_local_clone_into_empty_destination(self):
        with open(INSTALL_SMOKE_DOCKERFILE, encoding="utf-8") as fh:
            dockerfile = fh.read()

        self.assertIn("git init -q /repo-source", dockerfile)
        self.assertIn("CCC_REPO_URL=/repo-source", dockerfile)
        self.assertNotIn(
            "cp -r /repo /root/.ccc/claude-command-center",
            dockerfile,
        )
        self.assertNotIn(
            "> /usr/local/sbin/git",
            dockerfile,
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

    def test_service_needs_a_systemd_user_bus_on_linux(self):
        # A container or bare SSH login has systemctl but no user bus; a
        # "yes" there used to end the install with "Failed to connect to bus".
        for bus_status, expected in ((0, 0), (1, 1)):
            with self.subTest(bus_status=bus_status):
                result = _run_install_script_function(
                    "service_supported",
                    prelude=('uname() { printf "Linux"; }\n'
                             f'systemctl() {{ return {bus_status}; }}'),
                )
                self.assertEqual(result.returncode, expected)

    def test_service_is_always_offered_on_macos(self):
        result = _run_install_script_function(
            "service_supported",
            prelude='uname() { printf "Darwin"; }',
        )
        self.assertEqual(result.returncode, 0)


    def _clt_gate(self, xcode_select_status):
        return _run_install_script_function(
            "require_macos_clt",
            prelude=('uname() { printf "Darwin"; }\n'
                     f'xcode-select() {{ return {xcode_select_status}; }}'),
            env_extra={"PATH": "/usr/bin:/bin"},
        )

    def test_clt_gate_explains_stub_git_on_blank_mac(self):
        if not os.path.exists("/usr/bin/git"):
            self.skipTest("no /usr/bin/git on this runner")
        result = self._clt_gate(1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("xcode-select --install", result.stderr)
        self.assertIn("git", result.stderr)

    def test_clt_gate_passes_when_tools_installed(self):
        self.assertEqual(self._clt_gate(0).returncode, 0)

    def test_clt_gate_ignores_darwin_lookalike_without_xcode_select(self):
        # The Linux install-smoke image fakes `uname -s` = Darwin; its real
        # /usr/bin/git must not be mistaken for Apple's stub.
        if shutil.which("xcode-select") or not os.path.exists("/usr/bin/git"):
            self.skipTest("needs a non-macOS host with /usr/bin/git")
        result = _run_install_script_function(
            "require_macos_clt",
            prelude='uname() { printf "Darwin"; }',
            env_extra={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)


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
class TestPrePushScript(unittest.TestCase):
    def test_discovers_a_pytest_capable_python3_in_bash(self):
        """The Bash gate must not use zsh-only `command -v -a` lookup."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_dir = root / "scripts"
            tests_dir = root / "tests"
            bin_dir = root / "bin"
            script_dir.mkdir()
            tests_dir.mkdir()
            bin_dir.mkdir()
            shutil.copy2(PRE_PUSH_SCRIPT, script_dir / "pre-push.sh")
            (tests_dir / "test_perf_budget.py").write_text("# stub\n")
            python3 = bin_dir / "python3"
            python3.write_text(
                "#!/usr/bin/env bash\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pytest\" ]; then exit 0; fi\n"
                "exit 1\n"
            )
            python3.chmod(0o755)
            env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
            result = subprocess.run(
                ["bash", str(script_dir / "pre-push.sh")],
                capture_output=True,
                text=True,
                env=env,
                stdin=subprocess.DEVNULL,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre-push: running perf-budget gate", result.stdout)
        self.assertIn("pre-push: perf gate passed", result.stdout)


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


class PickPortTests(unittest.TestCase):
    """A clean machine can already have another program on 8090."""

    def _pick(self, port, explicit=False):
        env = {k: v for k, v in os.environ.items() if k != "PORT"}
        if explicit:
            env["PORT"] = str(port)
        program = (
            f'source "{INSTALL_SCRIPT}"; '
            f'INSTALL_DIR="{PROJECT_ROOT}"; PORT={port}; '
            'note() { :; }; pick_port; printf "%s %s" "$PORT" "$DASHBOARD_URL"'
        )
        out = subprocess.run(["bash", "-c", program], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.split()

    def test_busy_default_port_moves_to_a_free_one(self):
        import socket
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            picked, url = self._pick(port)
        self.assertGreater(int(picked), port)
        self.assertEqual(url, f"http://localhost:{picked}")

    def test_explicit_port_is_left_for_run_sh_to_report(self):
        import socket
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            picked, _url = self._pick(port, explicit=True)
        self.assertEqual(int(picked), port)


class TailnetInstallTests(unittest.TestCase):
    """Tailscale integration in scripts/install.sh for headless installs."""

    def _run_parse_tailnet(self, env_extra=None, args=()):
        env = os.environ.copy()
        env.pop("CCC_TAILNET", None)
        if env_extra:
            for k, v in env_extra.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        bash_program = (
            f'source "{INSTALL_SCRIPT}"; '
            'parse_tailnet_flag "$@"'
        )
        result = subprocess.run(
            ["bash", "-c", bash_program, "bash", *args],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_parse_tailnet_flags_and_env(self):
        self.assertEqual(self._run_parse_tailnet(args=("--tailnet",)), "enable")
        self.assertEqual(self._run_parse_tailnet(args=("--tailscale",)), "enable")
        self.assertEqual(self._run_parse_tailnet(args=("--no-tailnet",)), "disable")
        self.assertEqual(self._run_parse_tailnet(args=("--no-tailscale",)), "disable")
        self.assertEqual(self._run_parse_tailnet(env_extra={"CCC_TAILNET": "1"}), "enable")
        self.assertEqual(self._run_parse_tailnet(env_extra={"CCC_TAILNET": "true"}), "enable")
        self.assertEqual(self._run_parse_tailnet(env_extra={"CCC_TAILNET": "0"}), "disable")
        self.assertEqual(self._run_parse_tailnet(env_extra={"CCC_TAILNET": "false"}), "disable")
        self.assertEqual(self._run_parse_tailnet(), "")

    def test_apply_tailnet_network_config_writes_network_json(self):
        import json
        with tempfile.TemporaryDirectory() as fake_home:
            env = os.environ.copy()
            env["HOME"] = fake_home
            info_json = json.dumps({
                "ip": "100.108.91.118",
                "ips": ["100.108.91.118"],
                "dns": "hermes-vps.tail1234.ts.net",
            })
            bash_program = (
                f'source "{INSTALL_SCRIPT}"; '
                f'apply_tailnet_network_config 8090 \'{info_json}\''
            )
            result = subprocess.run(
                ["bash", "-c", bash_program],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            net_json_path = Path(fake_home) / ".claude" / "command-center" / "network.json"
            self.assertTrue(net_json_path.exists())
            data = json.loads(net_json_path.read_text())
            self.assertEqual(data.get("bind_host"), "100.108.91.118")
            self.assertTrue(data.get("trust_tailnet"))
            origins = data.get("allowed_origins", [])
            self.assertIn("http://hermes-vps.tail1234.ts.net:8090", origins)
            self.assertIn("https://hermes-vps.tail1234.ts.net:8090", origins)
            self.assertIn("http://100.108.91.118:8090", origins)

    def test_apply_tailnet_network_config_preserves_existing_origins(self):
        import json
        with tempfile.TemporaryDirectory() as fake_home:
            net_dir = Path(fake_home) / ".claude" / "command-center"
            net_dir.mkdir(parents=True)
            net_json_path = net_dir / "network.json"
            net_json_path.write_text(json.dumps({
                "bind_host": "127.0.0.1",
                "allowed_origins": ["http://existing-origin:8090"],
                "trust_tailnet": False,
            }))

            env = os.environ.copy()
            env["HOME"] = fake_home
            info_json = json.dumps({
                "ip": "100.108.91.118",
                "ips": ["100.108.91.118"],
                "dns": "hermes-vps.tail1234.ts.net",
            })
            bash_program = (
                f'source "{INSTALL_SCRIPT}"; '
                f'apply_tailnet_network_config 8090 \'{info_json}\''
            )
            result = subprocess.run(
                ["bash", "-c", bash_program],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            data = json.loads(net_json_path.read_text())
            self.assertEqual(data.get("bind_host"), "100.108.91.118")
            self.assertTrue(data.get("trust_tailnet"))
            origins = data.get("allowed_origins", [])
            self.assertIn("http://existing-origin:8090", origins)
            self.assertIn("http://hermes-vps.tail1234.ts.net:8090", origins)


class ServiceInstallWaitTests(unittest.TestCase):
    def test_run_sh_waits_20s_and_detects_systemd_failure(self):
        run_sh = Path(__file__).resolve().parent.parent / "run.sh"
        content = run_sh.read_text(encoding="utf-8")
        self.assertIn("seq 1 40", content)
        self.assertIn("within 20s", content)
        self.assertIn("systemctl --user is-failed --quiet", content)
        self.assertIn("is_port_bound", content)

    def test_install_sh_checks_active_systemd_before_foreground_exec(self):
        content = Path(INSTALL_SCRIPT).read_text(encoding="utf-8")
        self.assertIn("systemctl --user is-active --quiet ccc.service", content)
        self.assertIn("CCC is running in the background (it was slow to start)", content)

    def test_run_sh_is_port_bound_accepts_explicit_host(self):
        run_sh = Path(__file__).resolve().parent.parent / "run.sh"
        content = run_sh.read_text(encoding="utf-8")
        # Extract is_port_bound definition
        self.assertIn("explicit_host", content)
        bash_program = (
            f'eval "$(sed -n \'/^is_port_bound()/,/^}}/p\' "{run_sh}")"; '
            'type is_port_bound'
        )
        result = subprocess.run(
            ["bash", "-c", bash_program],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("explicit_host", result.stdout)


if __name__ == "__main__":
    unittest.main()
