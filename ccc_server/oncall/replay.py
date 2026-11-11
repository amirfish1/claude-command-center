"""Read-only GitHub replay. No dashboard or queue is started."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from .customers import _GUARD, _quoted


class ReplayError(Exception):
    pass


class GitHub:
    """Disk-cached GETs. Stop on rate limits rather than hammering the API."""
    def __init__(self, cache, base="https://api.github.com"):
        self.cache, self.base = Path(cache), base.rstrip("/")
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, path):
        url = self.base + path
        key = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if key.exists() and time.time() - key.stat().st_mtime < 3600:
            return json.loads(key.read_text())
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ccc-oncall-replay"})
        try:
            if self.base == "https://api.github.com" and shutil.which("gh"):
                completed = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, text=True, timeout=30)
                if completed.returncode:
                    raise ReplayError("GitHub GET failed: " + completed.stderr[-1000:])
                result = json.loads(completed.stdout)
                key.write_text(json.dumps(result))
                return result
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise ReplayError("GitHub rate limit or access denial. Retry later; cached responses are kept.") from exc
            raise ReplayError(f"GitHub returned HTTP {exc.code} for {path}") from exc
        key.write_text(json.dumps(result))
        return result

    def closing_pr(self, repo, number):
        # REST closed events often omit commit_id for PR closures.
        if self.base != "https://api.github.com" or not shutil.which("gh"):
            return None
        owner, name = repo.split("/")
        query = ('{repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name) + '){issue(number:' + str(int(number)) + '){timelineItems(last:1,itemTypes:[CLOSED_EVENT]){nodes{... on ClosedEvent{closer{__typename ... on PullRequest{number merged mergeCommit{oid} repository{nameWithOwner}}}}}}}}}')
        key = self.cache / (hashlib.sha256(query.encode()).hexdigest() + ".json")
        if key.exists() and time.time() - key.stat().st_mtime < 3600:
            data = json.loads(key.read_text())
        else:
            result = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ReplayError("GitHub closing-PR lookup failed: " + result.stderr[-1000:])
            data = json.loads(result.stdout)
            if data.get("errors"):
                raise ReplayError("GitHub closing-PR lookup returned errors.")
            key.write_text(json.dumps(data))
        nodes = data["data"]["repository"]["issue"]["timelineItems"]["nodes"]
        closer = (nodes[-1].get("closer") or {}) if nodes else {}
        if closer.get("__typename") == "PullRequest" and closer.get("merged") and closer.get("repository", {}).get("nameWithOwner", "").lower() == repo.lower():
            return {"number": closer["number"], "merged_at": True, "merge_commit_sha": closer["mergeCommit"]["oid"], "base": {"repo": {"full_name": repo}}}
        return None

    def pages(self, path, limit=10):
        for page in range(1, limit + 1):
            rows = self.get(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            yield from rows
            if len(rows) < 100:
                return
        raise ReplayError("GitHub pagination limit reached; narrow the date window.")


def is_test(name):
    parts = name.lower().split("/")
    base = parts[-1]
    return (any(p in ("test", "tests", "__tests__") for p in parts[:-1]) or
            base.startswith("test_") or bool(re.search(r"(?:_test\.py|\.(?:test|spec)\.[cm]?[jt]sx?)$", base)))


def select_cases(api, repo, days, maximum, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=days)).isoformat().replace("+00:00", "Z")
    metadata = api.get(f"/repos/{repo}")
    if metadata.get("private") is not False:
        raise ReplayError("Replay requires a public repository.")
    issues = api.pages(f"/repos/{repo}/issues?state=closed&sort=updated&direction=desc&since={since[:10]}")
    selected, seen = [], set()
    for issue in issues:
        labels = [str(label.get("name", "")).lower() for label in issue.get("labels", []) if isinstance(label, dict)]
        explicit_feature = any(re.search(r"feature|enhancement", label) for label in labels)
        explicit_feature |= bool(re.match(r"\[?(?:feature(?: request)?|enhancement)\b", issue.get("title", ""), re.I))
        if explicit_feature and not any("bug" in label for label in labels):
            continue
        closed = issue.get("closed_at")
        if "pull_request" in issue or not closed or not since <= closed <= now.isoformat().replace("+00:00", "Z"):
            continue
        # A cross-reference alone is not ground truth. Require the closing event's commit.
        events = list(api.pages(f"/repos/{repo}/issues/{issue['number']}/events"))
        closing = [e for e in events if e.get("event") == "closed"]
        if closing and closing[-1].get("commit_id"):
            sha = closing[-1]["commit_id"]
            pulls = api.pages(f"/repos/{repo}/commits/{sha}/pulls")
        else:
            closer = api.closing_pr(repo, issue["number"])
            if not closer:
                continue
            sha = closer["merge_commit_sha"]
            pulls = [closer]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        for pr in pulls:
            if not pr.get("merged_at") or pr.get("merge_commit_sha") != sha or pr.get("base", {}).get("repo", {}).get("full_name", "").lower() != repo.lower():
                continue
            files = list(api.pages(f"/repos/{repo}/pulls/{pr['number']}/files", limit=30))
            tests = [f["filename"] for f in files if is_test(f["filename"]) and f.get("status") in ("added", "modified")]
            if not tests or sha in seen:
                continue
            seen.add(sha)
            selected.append({"number": issue["number"], "title": issue["title"], "body": issue.get("body") or "", "url": issue["html_url"], "merge": sha, "pr": pr["number"], "test_files": tests, "maintainer_files": [f["filename"] for f in files]})
            break
        if len(selected) >= maximum:
            break
    return selected


def run(argv, cwd, env=None, timeout=120):
    """Bound a whole subprocess group, including tools an agent started."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "Command timed out."
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def must(argv, cwd, env=None):
    code, output = run(argv, cwd, env)
    if code:
        raise ReplayError(output[-4000:])
    return output


def isolated_env(home, auth=None):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    config = home / ".codex"
    config.mkdir(mode=0o700)
    if auth and Path(auth).is_file():
        shutil.copyfile(auth, config / "auth.json")
        (config / "auth.json").chmod(0o600)
    bins = home / "bin"
    bins.mkdir()
    for name in ("codex", "git", "python3", "node", "npm", "bash", "sh", "env", "cat", "ls", "rg", "sed", "mkdir", "stat", "head", "tail", "find", "sort", "wc", "grep", "uname", "which"):
        executable = shutil.which(name)
        if executable:
            (bins / name).symlink_to(executable)
    return {"HOME": str(home), "CODEX_HOME": str(config), "XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home), "PATH": str(bins), "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "CCC_TELEMETRY_DISABLED": "1", "CCC_AUTO_UPDATE": "0"}


def snapshot(source, revision, target, env):
    """Export the tree, then create new history containing only that tree."""
    target.mkdir()
    # git archive + tar avoids copying objects, refs, reflogs or alternates.
    archive = subprocess.run(["git", "-C", str(source), "archive", revision], check=True, capture_output=True).stdout
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    must(["git", "init", "-q"], target, env)
    (target / ".git/info/exclude").write_text(".replay-venv/\nnode_modules/\n*.egg-info/\n__pycache__/\n.pytest_cache/\n")
    must(["git", "add", "--all"], target, env)
    must(["git", "-c", "user.name=Replay", "-c", "user.email=replay@example.invalid", "commit", "-qm", "Replay starting tree"], target, env)


def setup(project, env):
    if (project / "pyproject.toml").exists() or (project / "setup.py").exists():
        code, log = run(["python3", "-m", "venv", ".replay-venv"], project, env)
        if code:
            raise ReplayError(log)
        python = str(project / ".replay-venv/bin/python")
        dependencies = ["pytest"]
        extras = "test"
        config = project / "pyproject.toml"
        if config.exists():
            data = tomllib.loads(config.read_text())
            optional = data.get("project", {}).get("optional-dependencies", {})
            extras = next((key for key in ("test", "tests", "testing") if key in optional), "")
            groups = data.get("dependency-groups", {})
            def expand(name, seen=()):
                if name in seen:
                    raise ReplayError("Cyclic test dependency group.")
                for item in groups.get(name, []):
                    if isinstance(item, str):
                        dependencies.append(item)
                    elif isinstance(item, dict) and "include-group" in item:
                        expand(item["include-group"], (*seen, name))
                    else:
                        raise ReplayError("Unsupported test dependency group entry.")
            for key in ("test", "tests", "testing"):
                if key in groups:
                    expand(key)
                    break
        for name in ("requirements/tests.txt", "requirements/test.txt", "requirements-test.txt", "tests/requirements.txt"):
            if (project / name).is_file():
                dependencies.extend(["-r", name])
                break
        package = f".[{extras}]" if extras else "."
        code, log = run([python, "-m", "pip", "install", "-e", package, *dependencies], project, env)
        if code:
            raise ReplayError(log)
        return [python, "-m", "pytest", "-q"]
    if (project / "package.json").exists():
        code, log = run(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], project, env)
        if code:
            raise ReplayError(log)
        return ["npm", "test"]
    raise ReplayError("No supported Python or npm project configuration.")


def install_tests(source, merge, target, names, env):
    # Restore exact maintainer test blobs, never trust agent edits to these files.
    for name in names:
        destination = target / name
        if destination.is_symlink() or any(p.is_symlink() for p in destination.parents if p != target.parent):
            raise ReplayError("Test path contains a symlink.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "-C", str(source), "show", f"{merge}:{name}"], capture_output=True, env=env)
        if result.returncode:
            raise ReplayError("Could not read maintainer test blob.")
        destination.write_bytes(result.stdout)


def judge(changed, agent_code, baseline_code, reference_code, final_code, status="attempted"):
    if status == "question":
        return "no attempt or asked a question"
    if reference_code != 0 or baseline_code != 1:
        return "could not set up"
    if not changed:
        return "no attempt or asked a question"
    return "fixed" if final_code == 0 and agent_code == 0 else "wrong fix"


def replay_case(repo, case, model, timeout, cache, auth):
    result = {**case, "outcome": "could not set up", "minutes": 0, "diff": "", "changed_files": [], "overlap_files": [], "reason": ""}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ccc-replay-") as tmp:
        root = Path(tmp)
        env = isolated_env(root / "home", auth)
        source = Path(cache) / "git" / repo.replace("/", "--") / case["merge"]
        source.mkdir(parents=True, exist_ok=True)
        try:
            must(["git", "init", "-q"], source, env)
            if run(["git", "cat-file", "-e", case["merge"] + "^1"], source, env)[0]:
                must(["git", "fetch", "-q", "--depth=2", f"https://github.com/{repo}.git", case["merge"]], source, env)
            parent = must(["git", "rev-parse", case["merge"] + "^1"], source, env).strip()
            result["parent"] = parent
            # Ground truth is held outside the fixing checkout, which has no remote.
            for name, rev in (("before", parent), ("reference", case["merge"]), ("agent", parent)):
                snapshot(source, rev, root / name, env)
            commands = {}
            for name in ("before", "reference", "agent"):
                commands[name] = setup(root / name, env)
            must(["git", "add", "--all"], root / "agent", env)
            run(["git", "-c", "user.name=Replay", "-c", "user.email=replay@example.invalid", "commit", "-qm", "Prepared environment"], root / "agent", env)
            initial = must(["git", "rev-parse", "HEAD"], root / "agent", env).strip()
            names = case["test_files"]
            def test(name):
                cmd = commands[name]
                if "pytest" in cmd:
                    cmd = cmd + [n for n in names if n.endswith(".py")]
                return run(cmd, root / name, env, timeout=120)
            install_tests(source, case["merge"], root / "before", names, env)
            baseline, baseline_log = test("before")
            reference, reference_log = test("reference")
            result.update(baseline_exit=baseline, reference_exit=reference, baseline_log=baseline_log[-12000:], reference_log=reference_log[-12000:])
            if baseline != 1 or reference != 0:
                raise ReplayError("Maintainer tests did not fail before and pass after the real fix.")
            prompt = (_GUARD + "\nFix this report in the current repository. Do not look up the issue or fix online. Do not contact anyone or change remotes. Do not access files outside this checkout. If you need a question answered, stop and state it.\n----- report -----\n" + _quoted(case["title"]) + "\n" + _quoted(case["body"]) + "\n----- end report -----")
            schema = root / "home" / "response-schema.json"
            schema.write_text(json.dumps({"type": "object", "properties": {"status": {"type": "string", "enum": ["attempted", "question", "no_attempt"]}, "summary": {"type": "string"}}, "required": ["status", "summary"], "additionalProperties": False}))
            answer = root / "home" / "answer.json"
            agent_start = time.monotonic()
            code, log = run(["codex", "exec", "-m", model, "-c", "model_reasoning_effort=medium", "-c", 'web_search="disabled"', "--sandbox", "workspace-write", "--output-schema", str(schema), "--output-last-message", str(answer), "--skip-git-repo-check", prompt], root / "agent", env, timeout)
            result["minutes"] = (time.monotonic() - agent_start) / 60
            result["agent_exit"] = code
            try:
                response = json.loads(answer.read_text())
            except (OSError, ValueError):
                response = {"status": "attempted", "summary": "Agent did not return a structured summary."}
            if not isinstance(response, dict):
                response = {"status": "attempted", "summary": "Invalid agent response."}
            result["agent_response"] = response
            result["agent_log"] = log[-16000:]
            # Compare to the immutable initial snapshot even if the agent commits.
            must(["git", "add", "--all"], root / "agent", env)
            result["diff"] = must(["git", "diff", "--binary", "--cached", initial, "--", ".", ":!.replay-venv", ":!node_modules", ":!*.egg-info"], root / "agent", env)
            result["changed_files"] = must(["git", "diff", "--cached", "--name-only", initial, "--", ".", ":!.replay-venv", ":!node_modules", ":!*.egg-info"], root / "agent", env).splitlines()
            result["overlap_files"] = sorted(set(result["changed_files"]) & set(case["maintainer_files"]))
            install_tests(source, case["merge"], root / "agent", names, env)
            final, final_log = test("agent")
            result.update(final_exit=final, final_log=final_log[-12000:])
            result["outcome"] = judge(result["changed_files"], code, baseline, reference, final, response.get("status"))
        except (ReplayError, OSError, subprocess.SubprocessError, ValueError) as exc:
            result["reason"] = str(exc)[-4000:]
            if "agent_exit" in result:
                result["outcome"] = "wrong fix"
    result["total_minutes"] = (time.monotonic() - started) / 60
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ccc oncall replay")
    parser.add_argument("repo")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--output", type=Path, default=Path("oncall-replay-results"))
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) or args.days < 1 or not 1 <= args.max <= 30 or args.timeout < 1:
        parser.error("Use OWNER/REPO, positive days and timeout, and --max between 1 and 30.")
    # The CLI must not import the running server to read a preview flag.
    flag = os.environ.get("CCC_FF_ONCALL")
    if flag is None:
        state = Path(os.environ.get("CCC_STATE_DIR", str(Path.home() / ".claude/command-center")))
        try:
            enabled = json.loads((state / "feature-flags.json").read_text()).get("oncall") is True
        except (OSError, ValueError, AttributeError):
            enabled = False
    else:
        enabled = flag.strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        parser.error("On-call preview is off. Set CCC_FF_ONCALL=1 for this command.")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be new or empty.")
    output.mkdir(parents=True, exist_ok=True)
    cache = Path.home() / ".cache/ccc/oncall-replay"
    data = {"repo": args.repo, "days": args.days, "model": args.model, "effort": "medium", "cases": [], "selection_error": "", "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    from .replay_report import render_report
    def save():
        (output / "results.json").write_text(json.dumps(data, indent=2))
        (output / "report.html").write_text(render_report(data))
    try:
        cases = select_cases(GitHub(cache), args.repo, args.days, args.max)
        data["selected"] = len(cases)
        save()
        auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
        for case in cases:
            result = replay_case(args.repo, case, args.model, args.timeout, cache, auth)
            data["cases"].append(result)
            save()
            print(f"#{case['number']}: {result['outcome']} ({result['minutes']:.2f} minutes)", flush=True)
    except (ReplayError, OSError) as exc:
        data["selection_error"] = str(exc)
        save()
        print(str(exc))
        return 1
    print(f"Saved {output / 'report.html'}")
    return 0
