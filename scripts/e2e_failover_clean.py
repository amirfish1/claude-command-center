"""Failover end to end on a clean machine, the way a new user installs it.

Unlike e2e_failover.py (which copies a dev router checkout and runs this
repo's server.py), every piece here comes from the public install path:

1. an empty HOME and an `env -i` environment with only the system PATH;
2. Claude Code from its official installer (or CCC_E2E_CLAUDE_BIN, linked in);
3. CCC from the README one-liner (scripts/install.sh), which picks a free
   dashboard port when 8090 is taken;
4. the free router through CCC's own setup job (/api/free-router/install)
   and keyless Kilo through Free model settings;
5. a real conversation, a synthetic limit stop, Continue free approval,
   real free work, then Switch back, all through CCC's HTTP actions.

The CCC server runs with a fake paid key and a fake subscription token in its
environment, and the test reads the free child's real environment to prove
neither reached it. Nothing touches your real HOME, 8090, or 3017.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_failover as base  # noqa: E402
from e2e_failover import FailoverError, require, events, json_file, free_evidence  # noqa: E402

FAKE_PAID = {
    "ANTHROPIC_API_KEY": "sk-ant-test-XXXX-clean-e2e",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test-XXXX-clean-e2e",
}
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin" + (":/opt/homebrew/bin" if sys.platform == "darwin" else "")
INSTALL_SH = "https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.sh"


class CleanHarness(base.Harness):
    def run(self):
        require(os.environ.get("CCC_E2E_FREE") == "1", "Set CCC_E2E_FREE=1 to consent to synthetic free-provider calls.")
        system_path = os.environ.get("CCC_E2E_PATH", DEFAULT_PATH)
        for tool in ("git", "python3", "node", "npm", "curl"):
            require(shutil.which(tool, path=system_path), f"{tool} is not on the clean PATH ({system_path}). Install it, or set CCC_E2E_PATH.")
        self.home = Path(tempfile.mkdtemp(prefix="ccc-clean-"))
        self.created_home = True
        os.chmod(self.home, 0o700)
        logs = self.home / "logs"
        logs.mkdir()
        local_bin = self.home / ".local/bin"
        router_port = base.validate_port(os.environ.get("CCC_E2E_ROUTER_PORT") or base.free_port())
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{local_bin}:{system_path}",
            "LANG": "C.UTF-8",
            "TERM": os.environ.get("TERM", "xterm"),
            "CCC_TELEMETRY_DISABLED": "1",
            # A private router port: never adopt or restart a real router on 3017.
            "CCC_FREE_ROUTER_PORT": str(router_port),
        }
        # The user's agent CLIs must not self-update during the test.
        json_file(self.home / ".claude/command-center/engine-updates.json", {"automatic": False})

        self.step("claude-install", f"Clean HOME {self.home}. Installing Claude Code (no login).")
        supplied = os.environ.get("CCC_E2E_CLAUDE_BIN")
        if supplied:
            local_bin.mkdir(parents=True)
            (local_bin / "claude").symlink_to(Path(supplied).resolve())
        else:
            self.run_command(["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"], self.home, "claude-install.log", timeout=600)
        claude = local_bin / "claude"
        require(claude.exists(), "Claude Code did not land in ~/.local/bin.")
        require(not (self.home / ".claude/.credentials.json").exists(), "The clean HOME unexpectedly has a Claude login.")

        self.step("ccc-install", "Installing CCC with the README one-liner.")
        source = os.environ.get("CCC_E2E_REPO_URL")
        if not source:
            # Default: install the commit under test, through the same installer.
            source = str(self.home / "ccc-source.git")
            self.run_command(["git", "clone", "-q", "--bare", "--branch", self._branch(), str(base.REPO), source], self.home, "source.log", timeout=300)
        if os.environ.get("CCC_E2E_INSTALL_URL") == "github":
            fetch = f"curl -fsSL {INSTALL_SH}"
        else:
            fetch = f"cat {base.REPO / 'scripts/install.sh'}"
        install_env = dict(self.env, CCC_REPO_URL=source, CCC_FROM="readme", **FAKE_PAID)
        self.secrets.extend(FAKE_PAID.values())
        self.start(["bash", "-c", f"{fetch} | bash"], self.home, install_env, "ccc-install.log")
        port = self.wait(self._dashboard_port, "the installer to start CCC", 600)
        self.ccc = f"http://127.0.0.1:{port}"
        self.wait(lambda: self.ready(self.ccc, "/api/version"), "CCC startup")
        print(f"[failover] CCC is up at {self.ccc}", flush=True)

        self.step("router-install", "Setting up the free router through CCC (clone, npm ci, build, start).")
        job = self.http(self.ccc, "/api/free-router/install", {})["job_id"]
        def installed():
            state = self.http(self.ccc, f"/api/free-router/jobs/{job}")
            if state.get("status") == "running":
                return None
            require(state.get("status") == "done", f"Router setup failed at '{state.get('step')}': {state.get('error')}")
            return state
        self.wait(installed, "router setup", 1200)
        self.step("provider", "Enabling keyless Kilo in Free model settings. Kilo logs prompts and outputs for training; only synthetic prompts are sent.")
        added = self.http(self.ccc, "/api/free-settings/keys/add", {"platform": "kilo", "label": "e2e-keyless"})
        require(added.get("ok"), "CCC could not enable keyless Kilo.")
        self.wait(lambda: self.http(self.ccc, "/api/free-router/status").get("ready"), "a ready free router")

        state_file = self.home / ".ccc/free-router.json"
        state = json.loads(state_file.read_text())
        require(state.get("port") == router_port, "CCC did not use the private router port.")
        self.router = f"http://127.0.0.1:{router_port}"
        login = self.http(self.router, "/api/auth/login", {"email": state["admin_email"], "password": state["admin_password"]})
        self.admin_token = login["token"]
        unified = state["unified_key"]
        self.secrets.extend((state["admin_password"], self.admin_token, unified))
        catalog = self.http(self.router, "/api/models", token=self.admin_token)
        candidates = [m for m in catalog if m.get("platform") == "kilo" and m.get("enabled") and m.get("supportsTools") and m.get("keyCount", 0) > 0 and m.get("modelId", "").endswith(":free")]
        require(candidates, "Kilo has no enabled free model with tools. Try again when its free catalog is available.")
        self.eligible = {m["modelId"] for m in candidates}
        chosen = os.environ.get("CCC_E2E_MODEL") or min(candidates, key=lambda m: (m["intelligenceRank"], m["modelId"]))["modelId"]
        require(chosen in self.eligible, "CCC_E2E_MODEL must be an enabled tool-capable Kilo :free model.")
        state["default_model"] = chosen
        json_file(state_file, state)
        print(f"[failover] Free model: {chosen}", flush=True)

        playground = self.home / "CCC-Playground"
        playground.mkdir()
        self.run_command(["git", "init", "-q", str(playground)], playground, "git-init.log", timeout=30)
        sid = str(uuid.uuid4())
        self.step("seed", "Starting real Claude Code for a short conversation (on the free router; there is no Claude login here).")
        seed_env = dict(self.env, ANTHROPIC_BASE_URL=self.router, ANTHROPIC_AUTH_TOKEN=unified, ANTHROPIC_MODEL=chosen)
        self.run_command([str(claude), "-p", "--verbose", "--session-id", sid, "--output-format", "stream-json", "--setting-sources", "project", "--tools", "", "--", base.SEED_PROMPT], playground, "seed.log", seed_env, timeout=int(os.environ.get("CCC_E2E_SPAWN_TIMEOUT_S", "420")))
        seed_result = [row for row in events(logs / "seed.log") if row.get("type") == "result"]
        require(seed_result and not seed_result[-1].get("is_error") and seed_result[-1].get("subtype") == "success", "Claude Code did not finish the seed turn successfully.")
        transcripts = list((self.home / ".claude/projects").glob(f"*/{sid}.jsonl"))
        require(len(transcripts) == 1, "Claude Code did not create one real transcript for the test session.")
        transcript = transcripts[0]
        seed_rows = events(transcript)
        seed_history = {row.get("uuid") for row in seed_rows if row.get("type") in ("user", "assistant")}
        seed_assistant_count = sum(row.get("type") == "assistant" for row in seed_rows)
        proof = playground / base.PROOF_FILE
        require(seed_history and seed_assistant_count and not proof.exists(), "The seed turn has no history, or wrote the proof early.")
        seed_analytics = self.analytics()

        self.step("limit-stop", "Simulating a Claude usage limit in that real conversation.")
        with open(transcript, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Claude AI usage limit reached", "sessionId": sid, "session_id": sid, "uuid": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")
        def limited():
            row = self.http(self.ccc, "/api/free-failover/status").get("sessions", {}).get(sid, {})
            return row if row.get("state") == "limited" and row.get("supports_continue_free") else None
        self.wait(limited, "CCC to offer Continue free", 180)
        print("[failover] CCC offers: Continue free  ·  waiting for approval", flush=True)
        before = self.analytics()
        require(before == seed_analytics and not proof.exists(), "The session continued without approval.")

        self.step("continue-free", "Approving Continue free for the same session.")
        continued = self.http(self.ccc, "/api/free-failover/continue", {"session_id": sid, "always": False})
        require(continued.get("ok") and continued.get("session_id") == sid and type(continued.get("pid")) is int and continued["pid"] > 1, "Continue free did not return a real same-session child.")
        self.resume_pid = continued["pid"]
        child_env = self._child_env(self.resume_pid)
        if child_env is not None:
            leaked = sorted(k for k in FAKE_PAID if k in child_env)
            require(not leaked, f"Paid credentials reached the free child: {leaked}")
            require(child_env.get("ANTHROPIC_BASE_URL") == self.router, "The free child is not pointed at the local router.")
            print("[failover] Free child env: router only; no paid key, no subscription token.", flush=True)
        def completed():
            results = [row for log in playground.rglob(f"resume-{sid[:8]}-*.log") for row in events(log) if row.get("type") == "result"]
            if results:
                require(not results[-1].get("is_error") and results[-1].get("subtype") == "success", "The free resumed turn ended with an error.")
                require(proof.is_file() and proof.read_text().rstrip("\n") == base.PROOF_TEXT, "The free resumed turn did not create the exact proof file.")
                return True
            return False
        self.wait(completed, "the free turn to finish and write its proof", int(os.environ.get("CCC_E2E_SPAWN_TIMEOUT_S", "420")))
        print(f"[failover] Proof file written by the free model: {proof.read_text().strip()!r}", flush=True)
        rows = events(transcript)
        require(any(row.get("subtype") == "ccc_free_runtime" and row.get("event") == "failover_start" for row in rows), "The transcript is missing its $0 failover marker.")
        require(sum(row.get("type") == "assistant" for row in rows) > seed_assistant_count, "No new assistant event reached the resumed transcript.")
        require(self.http(self.ccc, "/api/free-failover/status")["sessions"].get(sid, {}).get("state") == "free", "The session is not recorded as running free.")
        after = self.analytics()
        evidence = free_evidence(before, after, self.eligible)

        self.step("switch-back", "Switching back to the Claude plan, without sending a paid turn.")
        switched = self.http(self.ccc, "/api/free-failover/switch-back", {"session_id": sid})
        require(switched.get("ok") and switched.get("session_id") == sid, "Switch back was not accepted.")
        def back():
            row = self.http(self.ccc, "/api/free-failover/status").get("sessions", {}).get(sid, {})
            return row.get("state") not in ("free", "switch_back_pending") and any(e.get("event") == "failover_back" and e.get("subtype") == "ccc_free_runtime" for e in events(transcript))
        self.wait(back, "switch back to finish", 180)
        def child_gone():
            try:
                os.kill(self.resume_pid, 0)
            except ProcessLookupError:
                return True
            return False
        self.wait(child_gone, "the free child to exit", 60)
        self.resume_pid = None
        time.sleep(2)
        require(self.analytics() == after, "Switch back sent an unexpected model request.")
        require(seed_history <= {row.get("uuid") for row in events(transcript) if row.get("type") in ("user", "assistant")}, "Switching models lost the conversation history.")
        require(not (self.home / ".claude/settings.json").exists(), "CCC changed Claude's global settings.")
        require(not (self.home / ".claude/.credentials.json").exists(), "A Claude login appeared during the test.")
        result = dict(evidence, ok=True, session_id=sid, dashboard=self.ccc, model=chosen, clean_home=True, installed_from="install.sh", child_env_checked=child_env is not None, synthetic_limit=True)
        json_file(logs / "result.json", result)
        print("E2E_RESULT " + json.dumps(result), flush=True)
        self.step("done", "PASS: clean install, limit, approval, free work in the same conversation, switch back. No paid turn sent.")

    def _branch(self):
        out = subprocess.check_output(["git", "-C", str(base.REPO), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        require(out and out != "HEAD", "Check out a branch, or set CCC_E2E_REPO_URL.")
        return out

    def _dashboard_port(self):
        log = self.home / "logs/ccc-install.log"
        match = re.search(r"url\s+: http://localhost:(\d+)", log.read_text(errors="replace")) if log.is_file() else None
        return int(match.group(1)) if match else None

    @staticmethod
    def _child_env(pid):
        environ = Path(f"/proc/{pid}/environ")
        try:
            raw = environ.read_bytes()
        except OSError:
            return None  # macOS has no /proc; the unit tests cover the scrub there
        return dict(item.split("=", 1) for item in raw.decode(errors="replace").split("\0") if "=" in item)

    def cleanup(self):
        # CCC, its worker and the router run detached from us. Everything
        # they start lives under the private HOME, so match on that path.
        if self.home:
            try:
                out = subprocess.run(["pgrep", "-f", str(self.home)], capture_output=True, text=True).stdout.split()
            except OSError:
                out = []
            for pid in out:
                if int(pid) != os.getpid():
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        super().cleanup()


def main():
    harness = CleanHarness()
    def interrupted(signum, frame):
        raise FailoverError("The test was interrupted.")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        harness.run()
        return 0
    except (FailoverError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        message = str(exc)
        for secret in harness.secrets:
            message = message.replace(secret, "[redacted]")
        print(f"[failover] FAIL ({harness.phase}): {message}", file=sys.stderr, flush=True)
        return 1
    finally:
        harness.cleanup()


if __name__ == "__main__":
    sys.exit(main())
