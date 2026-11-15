#!/usr/bin/env python3
"""Dispatch CI to a Claude cloud task and wait for its commit status."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, token, body=None, headers=None):
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    # Never forward either credential to a redirect target. Do not log bodies:
    # API errors may echo request headers or other sensitive input.
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("Remote API returned HTTP %s" % error.code) from None
    except urllib.error.URLError:
        raise RuntimeError("Remote API could not be reached") from None


def dispatch_and_wait(config, request=request_json, clock=time.monotonic, sleep=time.sleep):
    fire_url = config["fire_url"]
    if not re.fullmatch(
        r"https://api\.anthropic\.com/v1/claude_code/routines/trig_[A-Za-z0-9]+/fire",
        fire_url,
    ):
        raise ValueError("CLAUDE_CLOUD_CI_FIRE_URL must be the official task /fire URL")
    repository = config["repository"]
    sha = config["sha"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Expected an immutable 40-character commit SHA")
    run_id, attempt = config["run_id"], config["attempt"]
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("Invalid workflow run identity")
    context = "claude-cloud-ci/%s/%s" % (run_id, attempt)
    payload = {
        "repository": repository,
        "sha": sha,
        "ref": config["ref"],
        "status_context": context,
        "workflow_run_url": "https://github.com/%s/actions/runs/%s" % (repository, run_id),
    }
    # A timeout after POST is ambiguous. Retrying could start a second agent;
    # leave the outcome unknown and let the operator inspect task history.
    result = request(
        fire_url,
        config["fire_token"],
        {"text": json.dumps(payload)},
        {
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
        },
    )
    session_id = result.get("claude_code_session_id", "")
    session_url = result.get("claude_code_session_url", "")
    if not re.fullmatch(r"session_[A-Za-z0-9]+", session_id):
        raise RuntimeError("Dispatch response did not contain a cloud session ID")
    if session_url != "https://claude.ai/code/" + session_id:
        raise RuntimeError("Dispatch response did not contain the expected cloud session URL")
    print("Cloud session: " + session_url, flush=True)
    summary = config.get("summary")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write("[Claude cloud CI](%s) tests `%s`\n" % (session_url, sha))
    deadline = clock() + config.get("timeout", 2700)
    # The context includes the run attempt: a green result from another run
    # or from a repair commit must never pass this workflow.
    endpoint = "https://api.github.com/repos/%s/commits/%s/statuses?per_page=100" % (repository, sha)
    while clock() < deadline:
        for page in range(1, 11):
            statuses = request(endpoint + "&page=%s" % page, config["github_token"])
            if not isinstance(statuses, list):
                raise RuntimeError("Unexpected GitHub status response")
            match = next((status for status in statuses if status.get("context") == context), None)
            if match:
                state = match.get("state")
                if state == "success":
                    print("Cloud checks passed for " + sha)
                    return 0
                if state in ("failure", "error"):
                    print("Cloud checks reported %s; see %s" % (state, session_url))
                    return 1
                if state != "pending":
                    raise RuntimeError("Unexpected cloud check state")
                break
            if len(statuses) < 100:
                break
        sleep(min(20, max(0, deadline - clock())))
    raise RuntimeError("Timed out waiting for cloud test status; the cloud session may still be running")


def main():
    names = {
        "fire_url": "CLAUDE_CLOUD_CI_FIRE_URL",
        "fire_token": "CLAUDE_CLOUD_CI_FIRE_TOKEN",
        "github_token": "GITHUB_TOKEN",
        "repository": "GITHUB_REPOSITORY",
        "sha": "CLOUD_CI_SHA",
        "ref": "CLOUD_CI_REF",
        "run_id": "GITHUB_RUN_ID",
        "attempt": "GITHUB_RUN_ATTEMPT",
    }
    missing = [name for name in names.values() if not os.environ.get(name)]
    if missing:
        print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
        return 1
    config = {key: os.environ[name] for key, name in names.items()}
    config["summary"] = os.environ.get("GITHUB_STEP_SUMMARY")
    return dispatch_and_wait(config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, TimeoutError):
        # Never print an exception containing a URL or a token-bearing body.
        print("Cloud CI dispatch or verification failed. Check configuration and cloud task history.", file=sys.stderr)
        sys.exit(1)
