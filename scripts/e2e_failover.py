import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
import uuid


REPO = Path(__file__).resolve().parent.parent
PROOF_FILE = "e2e-failover-proof.txt"
PROOF_TEXT = "hello from free failover"
SEED_PROMPT = (
    "Reply with READY and stop. Do not create any files in this turn. "
    "When I later send continue, create e2e-failover-proof.txt in the current "
    "directory containing exactly: hello from free failover. Then stop."
)
COST_BASIS = "successful keyless Kilo :free request rows; not a billing counter"
REQUEST_FIELDS = ("id", "platform", "modelId", "status", "inputTokens", "outputTokens")


class FailoverError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise FailoverError(message)


def clean_env(source, home):
    keep = {"PATH", "PYTHONPATH", "PYTHONUSERBASE", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {key: value for key, value in source.items() if key in keep}
    env["HOME"] = str(home)
    return env


def validate_home(target, real_home):
    home = Path(target).expanduser().absolute()
    require(not home.is_symlink(), "The test HOME must not be a symlink.")
    require(home.resolve() != Path(real_home).resolve(), "The test HOME must not be your real HOME.")
    require(not home.exists() or (home.is_dir() and not any(home.iterdir())), "Use an empty directory for CCC_E2E_HOME.")
    return home


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def validate_port(value, other=None):
    port = int(value)
    require(1024 <= port <= 65535 and port not in (8090, 3017) and port != other, "Choose distinct test ports, not 8090 or 3017.")
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise FailoverError(f"Test port {port} is already in use. Choose another port.") from exc
    return port


def json_file(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        os.chmod(target, 0o600)
        json.dump(data, stream, indent=2)
        stream.write("\n")


def events(target):
    if not target.is_file():
        return []
    out = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def snapshot_analytics(summary, requests):
    require(isinstance(summary, dict) and isinstance(requests, dict), "Router analytics returned an unexpected shape.")
    for field in ("totalRequests", "totalInputTokens", "totalOutputTokens"):
        require(type(summary.get(field)) is int and summary[field] >= 0, f"Router analytics is missing {field}.")
    rows = requests.get("rows")
    require(isinstance(rows, list) and type(requests.get("total")) is int and requests["total"] == len(rows), "Router request analytics is incomplete. Refusing a partial count.")
    sanitized = []
    for row in rows:
        require(isinstance(row, dict) and all(field in row for field in REQUEST_FIELDS), "Router request analytics is missing required fields.")
        for field in ("id", "inputTokens", "outputTokens"):
            require(type(row[field]) is int and row[field] >= 0, f"Router request analytics has invalid {field}.")
        require(row["status"] in ("success", "error", "canceled"), "Router request analytics has an unknown status.")
        sanitized.append({field: row[field] for field in REQUEST_FIELDS})
    require(len({row["id"] for row in sanitized}) == len(sanitized), "Router request IDs are not unique.")
    require(summary["totalRequests"] == len(sanitized), "Router summary and request counts disagree.")
    require(summary["totalInputTokens"] == sum(row["inputTokens"] for row in sanitized) and summary["totalOutputTokens"] == sum(row["outputTokens"] for row in sanitized), "Router summary and request token counts disagree.")
    return {"summary": {field: summary[field] for field in ("totalRequests", "totalInputTokens", "totalOutputTokens")}, "rows": sanitized}


def free_evidence(before, after, eligible):
    delta = after["summary"]["totalRequests"] - before["summary"]["totalRequests"]
    require(delta > 0, "The router recorded no new failover requests.")
    baseline = {row["id"]: row for row in before["rows"]}
    baseline_ids = set(baseline)
    require(baseline_ids <= {row["id"] for row in after["rows"]}, "Router request history changed during the test.")
    require(all(row == baseline[row["id"]] for row in after["rows"] if row["id"] in baseline), "Router prior request rows changed during the test.")
    new_rows = [row for row in after["rows"] if row["id"] not in baseline_ids]
    require(len(new_rows) == delta, "Router request delta does not match the captured request rows.")
    require(all(row["platform"] == "kilo" and row["modelId"] in eligible and row["modelId"].endswith(":free") for row in new_rows), "A failover request did not use an eligible keyless Kilo free model.")
    successes = [row for row in new_rows if row["status"] == "success"]
    require(successes and sum(row["inputTokens"] + row["outputTokens"] for row in successes) > 0, "No successful free inference with token usage was recorded.")
    return {"router_requests_delta": delta, "free_success_count": len(successes), "inference_cost_usd": 0, "cost_basis": COST_BASIS}


class Harness:
    def __init__(self):
        self.phase = "preflight"
        self.home = None
        self.created_home = False
        self.children = []
        self.resume_pid = None
        self.secrets = []
        self.env = None

    def step(self, phase, text):
        self.phase = phase
        print(f"[failover] {text}", flush=True)

    def http(self, base, route, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(base + route, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise FailoverError(f"{route.split('?')[0]} returned HTTP {exc.code}.") from exc
        except (OSError, ValueError) as exc:
            raise FailoverError(f"{route.split('?')[0]} did not return valid JSON.") from exc

    def wait(self, predicate, text, seconds=120):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            require(all(proc.poll() is None for proc, _ in self.children), f"A test service stopped while waiting for {text}.")
            value = predicate()
            if value:
                require(all(proc.poll() is None for proc, _ in self.children), f"A test service stopped while waiting for {text}.")
                return value
            time.sleep(1)
        raise FailoverError(f"Timed out waiting for {text}. Logs are in {self.home / 'logs'}.")

    def ready(self, base, route):
        try:
            result = self.http(base, route)
            return result if route != "/readyz" or result.get("status") == "ok" else None
        except FailoverError:
            return None

    def start(self, command, cwd, env, log_name):
        stream = open(self.home / "logs" / log_name, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            stream.close()
            raise
        self.children.append((proc, stream))
        return proc

    def run_command(self, command, cwd, log_name, env=None, timeout=900):
        proc = self.start(command, cwd, env or self.env, log_name)
        try:
            code = proc.wait(timeout=timeout)
            require(code == 0, f"{log_name} step failed. See the private log.")
        finally:
            if proc.poll() is not None:
                for item in list(self.children):
                    if item[0] is proc:
                        item[1].close()
                        self.children.remove(item)

    def analytics(self):
        return snapshot_analytics(
            self.http(self.router, "/api/analytics/summary?range=24h", token=self.admin_token),
            self.http(self.router, "/api/analytics/requests?range=24h&limit=500&offset=0", token=self.admin_token),
        )

    def run(self):
        require(os.environ.get("CCC_E2E_FREE") == "1", "Set CCC_E2E_FREE=1 to consent to synthetic free-provider calls.")
        require(sys.version_info >= (3, 12), "Use Python 3.12 or newer via CCC_PYTHON.")
        source = Path(os.environ.get("CCC_FREELLMAPI_SRC", "")).expanduser()
        require(os.environ.get("CCC_FREELLMAPI_SRC") and (source / "server/src").is_dir(), "Set CCC_FREELLMAPI_SRC to a local freellmapi checkout.")
        claude = os.environ.get("CCC_E2E_CLAUDE_BIN") or shutil.which("claude")
        require(claude and Path(claude).is_file() and os.access(claude, os.X_OK), "Install Claude Code or set CCC_E2E_CLAUDE_BIN to its executable.")
        claude = str(Path(claude).resolve())
        node = shutil.which("node")
        require(node and shutil.which("npm"), "Install Node 20.18 or newer, below 25, and npm.")
        version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
        require((20, 18) <= tuple(map(int, version.split(".")[:2])) < (25, 0), "Use Node 20.18 or newer, below 25.")
        ccc_port = validate_port(os.environ.get("CCC_E2E_CCC_PORT", "9202"))
        router_port = validate_port(os.environ.get("CCC_E2E_ROUTER_PORT") or free_port(), ccc_port)
        supplied = os.environ.get("CCC_E2E_HOME")
        if supplied:
            self.home = validate_home(supplied, Path.home())
            self.home.mkdir(parents=True, exist_ok=True)
        else:
            self.home = Path(tempfile.mkdtemp(prefix="ccc-failover-"))
            self.created_home = True
        os.chmod(self.home, 0o700)
        (self.home / "logs").mkdir()
        self.env = clean_env(os.environ, self.home)
        self.run_command([sys.executable, "-c", "import watchtower.queue"], self.home, "python-dependencies.log", timeout=30)
        self.step("router-setup", f"Preparing a private test HOME: {self.home}")
        target = self.home / ".ccc/freellmapi"
        ignored = shutil.ignore_patterns(".git", ".env", ".env.*", "data", "*.db", "*.db-*", "*.sqlite*", "*.log", "logs", ".claude", ".ccc*", "desktop", "repo-assets", "coverage")
        target.mkdir(parents=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(source / name, target / name)
        for name in ("server", "shared", "client", "cli", "node_modules"):
            if (source / name).is_dir():
                shutil.copytree(source / name, target / name, ignore=ignored)
        if not (target / "server/dist/index.js").is_file() or not (target / "node_modules").is_dir():
            self.step("router-build", "Building the router inside the test HOME.")
            self.run_command(["npm", "ci", "--no-audit", "--no-fund"], target, "npm-ci.log")
            self.run_command(["npm", "run", "build"], target, "npm-build.log")
        json_file(target / ".ccc-router-managed", {"managed_by": "ccc"})
        password = secrets.token_urlsafe(24)
        encryption_key = secrets.token_hex(32)
        self.secrets.extend((password, encryption_key))
        config = self.home / ".ccc/router-config.json"
        json_file(config, {"admin": {"email": "e2e-admin@localhost.test", "password": password}})
        router_env = dict(self.env, HOST="127.0.0.1", PORT=str(router_port), ENCRYPTION_KEY=encryption_key, FREEAPI_CONFIG_PATH=str(config), FREEAPI_DB_PATH=str(self.home / ".ccc/router-data/freeapi.db"))
        self.router = f"http://127.0.0.1:{router_port}"
        self.start([node, "server/dist/index.js"], target, router_env, "router.log")
        self.wait(lambda: self.ready(self.router, "/api/auth/status"), "router startup")
        login = self.http(self.router, "/api/auth/login", {"email": "e2e-admin@localhost.test", "password": password})
        self.admin_token = login["token"]
        unified = self.http(self.router, "/api/settings/api-key", token=self.admin_token)["apiKey"]
        self.secrets.extend((self.admin_token, unified))
        self.step("provider", "Enabling keyless Kilo for synthetic prompts only. Kilo logs prompts and outputs for training.")
        self.http(self.router, "/api/keys", {"platform": "kilo", "label": "e2e-keyless"}, self.admin_token)
        keys = self.http(self.router, "/api/keys", token=self.admin_token)
        require(isinstance(keys, list) and len(keys) == 1 and keys[0].get("platform") == "kilo", "The isolated router must have only the keyless Kilo provider.")
        catalog = self.http(self.router, "/api/models", token=self.admin_token)
        candidates = [model for model in catalog if model.get("platform") == "kilo" and model.get("enabled") and model.get("supportsTools") and model.get("keyCount", 0) > 0 and model.get("modelId", "").endswith(":free")]
        require(candidates, "Kilo has no enabled free model with tools. Try again when its free catalog is available.")
        self.eligible = {model["modelId"] for model in candidates}
        chosen = os.environ.get("CCC_E2E_MODEL") or min(candidates, key=lambda model: (model["intelligenceRank"], model["modelId"]))["modelId"]
        require(chosen in self.eligible, "CCC_E2E_MODEL must be an enabled tool-capable Kilo :free model.")
        json_file(self.home / ".ccc/free-router.json", {"port": router_port, "admin_email": "e2e-admin@localhost.test", "admin_password": password, "unified_key": unified, "default_model": chosen})
        self.wait(lambda: self.ready(self.router, "/readyz"), "a ready free provider")
        playground = self.home / "CCC-Playground"
        playground.mkdir()
        self.run_command(["git", "init", "-q", str(playground)], playground, "git-init.log", timeout=30)
        sid = str(uuid.uuid4())
        self.step("seed", "Starting real Claude Code to create a small conversation. This bootstrap also uses the free router.")
        seed_env = dict(self.env, ANTHROPIC_BASE_URL=self.router, ANTHROPIC_AUTH_TOKEN=unified, ANTHROPIC_MODEL=chosen)
        self.run_command([claude, "-p", "--verbose", "--session-id", sid, "--output-format", "stream-json", "--setting-sources", "project", "--tools", "", "--", SEED_PROMPT], playground, "seed.log", seed_env, timeout=int(os.environ.get("CCC_E2E_SPAWN_TIMEOUT_S", "420")))
        seed_result = [row for row in events(self.home / "logs/seed.log") if row.get("type") == "result"]
        require(seed_result and not seed_result[-1].get("is_error") and seed_result[-1].get("subtype") == "success", "Claude Code did not finish the seed turn successfully.")
        transcripts = list((self.home / ".claude/projects").glob(f"*/{sid}.jsonl"))
        require(len(transcripts) == 1, "Claude Code did not create one real transcript for the test session.")
        transcript = transcripts[0]
        seed_rows = events(transcript)
        seed_history = {row.get("uuid") for row in seed_rows if row.get("type") in ("user", "assistant")}
        seed_assistant_count = sum(row.get("type") == "assistant" for row in seed_rows)
        require(seed_history and seed_assistant_count and any(row.get("type") == "user" for row in seed_rows), "The seed transcript has no real conversation history.")
        proof = playground / PROOF_FILE
        require(not proof.exists(), "The proof file appeared before failover approval.")
        seed_analytics = self.analytics()
        self.step("limit-stop", "Adding a synthetic limit stop to that real conversation.")
        with open(transcript, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Claude AI usage limit reached", "sessionId": sid, "session_id": sid, "uuid": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")
        self.ccc = f"http://127.0.0.1:{ccc_port}"
        json_file(self.home / ".claude/command-center/engine-updates.json", {"automatic": False})
        server_env = dict(self.env, CCC_EPHEMERAL="1", CCC_ALLOW_DUPLICATE_REPO="1", CCC_CONTROL_PLANE_ENGINES="0", CCC_TELEMETRY_DISABLED="1", CCC_CLAUDE_BIN=claude, CCC_FREE_ROUTER_HOME=str(self.home / ".ccc"))
        self.start([sys.executable, str(REPO / "server.py"), "--port", str(ccc_port)], playground, server_env, "ccc-server.log")
        self.wait(lambda: self.ready(self.ccc, "/api/version"), "CCC startup")
        def limited():
            status = self.http(self.ccc, "/api/free-failover/status")
            row = status.get("sessions", {}).get(sid, {})
            return row if status.get("free_ready") and row.get("state") == "limited" and row.get("supports_continue_free") else None
        self.wait(limited, "the normal limit watcher to offer Continue free")
        before = self.analytics()
        json_file(self.home / "logs/analytics-before.json", before)
        require(before == seed_analytics and not proof.exists(), "The session continued without approval.")
        self.step("continue-free", "Approving Continue free for the same session.")
        continued = self.http(self.ccc, "/api/free-failover/continue", {"session_id": sid, "always": False})
        require(continued.get("ok") and continued.get("session_id") == sid and type(continued.get("pid")) is int and continued["pid"] > 1, "Continue free did not return a real same-session child.")
        self.resume_pid = continued["pid"]
        def completed():
            logs = list(playground.rglob(f"resume-{sid[:8]}-*.log"))
            results = [row for log in logs for row in events(log) if row.get("type") == "result"]
            if results:
                require(not results[-1].get("is_error") and results[-1].get("subtype") == "success", "The free resumed turn ended with an error.")
                require(proof.is_file() and proof.read_text().rstrip("\n") == PROOF_TEXT, "The free resumed turn did not create the exact proof file.")
                return True
            return False
        self.wait(completed, "the free turn to finish and write its proof", int(os.environ.get("CCC_E2E_SPAWN_TIMEOUT_S", "420")))
        rows = events(transcript)
        require(any(row.get("subtype") == "ccc_free_runtime" and row.get("event") == "failover_start" and "$0" in row.get("text", "") for row in rows), "The real transcript is missing its $0 failover marker.")
        require(sum(row.get("type") == "assistant" for row in rows) > seed_assistant_count, "No new assistant event reached the resumed transcript.")
        require(self.http(self.ccc, "/api/free-failover/status")["sessions"].get(sid, {}).get("state") == "free", "The session is not recorded as running free.")
        after = self.analytics()
        json_file(self.home / "logs/analytics-after.json", after)
        evidence = free_evidence(before, after, self.eligible)
        self.step("switch-back", "Switching back without sending a paid turn.")
        switched = self.http(self.ccc, "/api/free-failover/switch-back", {"session_id": sid})
        require(switched.get("ok") and switched.get("session_id") == sid, "Switch back was not accepted.")
        def back():
            row = self.http(self.ccc, "/api/free-failover/status").get("sessions", {}).get(sid, {})
            return row.get("state") not in ("free", "switch_back_pending") and any(event.get("event") == "failover_back" and event.get("subtype") == "ccc_free_runtime" for event in events(transcript))
        self.wait(back, "switch back to finish")
        def child_gone():
            try:
                os.kill(self.resume_pid, 0)
            except ProcessLookupError:
                return True
            return False
        self.wait(child_gone, "the free child to exit", 30)
        self.resume_pid = None
        time.sleep(2)
        final = self.analytics()
        json_file(self.home / "logs/analytics-final.json", final)
        require(final == after, "Switch back sent an unexpected model request.")
        current_history = {row.get("uuid") for row in events(transcript) if row.get("type") in ("user", "assistant")}
        require(seed_history <= current_history, "Switching models lost the seed conversation history.")
        require(not (self.home / ".claude/settings.json").exists(), "The test unexpectedly changed Claude's global settings.")
        result = dict(evidence, ok=True, session_id=sid, home=str(self.home), proof_file=str(proof), transcript_file=str(transcript), switch_back_complete=True, seed_runtime="free bootstrap", synthetic_limit=True)
        json_file(self.home / "logs/result.json", result)
        print("E2E_RESULT " + json.dumps(result), flush=True)
        self.step("done", "PASS: same conversation, real free work, then switch back. No paid turn was sent.")

    def cleanup(self):
        pids = ([self.resume_pid] if self.resume_pid else []) + [proc.pid for proc, _ in self.children if proc.poll() is None]
        for pid in pids:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc, stream in self.children:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            stream.close()
        if self.home:
            if self.created_home and os.environ.get("CCC_E2E_KEEP") != "1":
                shutil.rmtree(self.home)
            else:
                print(f"[failover] Kept private evidence in {self.home / 'logs'}", flush=True)


def main():
    harness = Harness()
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
