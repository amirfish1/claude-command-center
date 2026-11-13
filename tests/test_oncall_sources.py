# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""On-call sources: GitHub issues, Sentry, PostHog, URL and command checks.

No network and no real accounts: every source gets a fake ``http`` callable, a
fake clock and (for commands) a fake ``run``. State lives in a temp Store.
"""
import json
import re
import subprocess
import sys
import urllib.parse

import pytest

from ccc_server.oncall import sources, store

TOKEN = "test-token-XXXX-do-not-leak"
T0 = 1794300000.0  # 2026-11-10 08:40:00 UTC


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class FakeHttp:
    """Records every call and answers from a handler ``(method, url) -> reply``."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, method, url, *, headers=None, data=None, timeout=20):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "data": data, "timeout": timeout})
        reply = self.handler(method, url)
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return status, body

    def urls(self, needle=""):
        return [c["url"] for c in self.calls if needle in c["url"]]


@pytest.fixture
def kv(tmp_path):
    s = store.Store(tmp_path)
    yield s
    s.close()


@pytest.fixture
def clock():
    return Clock()


def secret(name):
    return TOKEN if name == "monitor-token" else ""


def issue(issue_id, *, count=1, users=0, level="error", title=None):
    return {"id": str(issue_id), "shortId": f"STOREFRONT-{issue_id}",
            "title": title or f"TypeError: problem {issue_id}",
            "culprit": "app/checkout.py in submit_order", "level": level,
            "count": str(count), "userCount": users,
            "firstSeen": "2026-11-10T08:00:00Z", "lastSeen": "2026-11-10T08:30:00Z",
            "permalink": f"https://sentry.io/organizations/example-org/issues/{issue_id}/",
            "project": {"id": "42", "slug": "storefront", "name": "Storefront"},
            "metadata": {"type": "TypeError", "value": f"problem {issue_id}"}}


EVENT = {"eventID": "abc", "entries": [
    {"type": "breadcrumbs", "data": {"values": []}},
    {"type": "exception", "data": {"values": [{
        "type": "TypeError", "value": "cannot read total of None",
        "stacktrace": {"frames": [
            {"filename": "lib/framework/handler.py", "lineNo": 10, "function": "dispatch", "inApp": False},
            {"filename": "app/views.py", "lineNo": 88, "function": "checkout", "inApp": True},
            {"filename": "app/checkout.py", "lineNo": 42, "function": "submit_order", "inApp": True},
        ]}}]}}]}


class Sentry:
    """A fake Sentry: ``issues`` is what the list endpoint returns right now."""

    def __init__(self, issues=()):
        self.issues = list(issues)
        self.list_status = 200

    def __call__(self, method, url):
        if "/events/latest/" in url:
            return 200, EVENT
        if self.list_status != 200:
            return self.list_status, {"detail": f"refused {TOKEN}"}
        return 200, self.issues


def sentry_source(kv, clock, fake, **extra):
    cfg = {"id": "errors", "type": "sentry", "org": "example-org", "project": "storefront",
           "token_vault": "monitor-token"}
    cfg.update(extra)
    http = FakeHttp(fake)
    return sources.build(cfg, secret=secret, kv=kv, http=http, clock=clock), http


def assert_valid(finding):
    assert re.fullmatch(r"[A-Za-z0-9:._-]{1,120}", finding.fingerprint)
    assert finding.title and "\n" not in finding.title and len(finding.title) <= 120
    assert finding.text and len(finding.text) <= 6000
    assert finding.severity in ("", "low", "high")
    assert TOKEN not in finding.text + finding.title + finding.url
    assert chr(0x2014) not in finding.text + finding.title


# -- due() ------------------------------------------------------------------

def test_due_first_call_then_waits_for_every_s(kv, clock):
    src, _ = sentry_source(kv, clock, Sentry(), every_s=120)
    assert src.id == "errors" and src.type == "sentry"
    assert src.due() is True
    assert src.poll() == []
    assert src.due() is False
    assert kv.kv_get("src:errors:last_poll") == T0
    clock.tick(119)
    assert src.due() is False
    clock.tick(1)
    assert src.due() is True


def test_due_counts_failed_attempts_and_survives_a_restart(kv, clock):
    fake = Sentry()
    fake.list_status = 500
    src, _ = sentry_source(kv, clock, fake)
    assert src.poll() == [] and src.last_error
    assert src.due() is False  # an attempt, even a failed one, resets the timer
    again, _ = sentry_source(kv, clock, fake)
    assert again.due() is False
    clock.tick(sources.SentrySource.default_every_s)
    assert again.due() is True


def test_default_intervals_and_memory_state_without_a_store(clock):
    assert sources.SentrySource.default_every_s == 300
    assert sources.PostHogSource.default_every_s == 600
    assert sources.HttpSource.default_every_s == 300
    assert sources.CommandSource.default_every_s == 3600
    src = sources.build({"id": "up", "type": "http", "url": "https://example.com/health"},
                        http=FakeHttp(lambda m, u: (200, b"ok")), clock=clock)
    assert src.due() and src.poll() == [] and not src.due()


# -- Sentry -----------------------------------------------------------------

def test_sentry_baseline_then_new_issue_then_no_duplicate(kv, clock):
    fake = Sentry([issue(1), issue(2)])
    src, http = sentry_source(kv, clock, fake, environment="production")
    assert src.poll() == [] and src.last_error == ""
    assert http.urls("/events/latest/") == []  # the backlog costs no extra requests

    call = http.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    parts = urllib.parse.urlsplit(call["url"])
    assert parts.netloc == "sentry.io" and parts.path == "/api/0/organizations/example-org/issues/"
    query = urllib.parse.parse_qs(parts.query)
    assert query["project"] == ["storefront"] and query["query"] == ["is:unresolved"]
    assert query["sort"] == ["new"] and query["limit"] == ["25"]
    assert query["environment"] == ["production"]

    fake.issues.insert(0, issue(3, count=7, users=2))
    found = src.poll()
    assert [f.fingerprint for f in found] == ["sentry:3"]
    finding = found[0]
    assert_valid(finding)
    assert finding.title == "TypeError: problem 3"
    assert finding.url == "https://sentry.io/organizations/example-org/issues/3/"
    assert finding.severity == ""
    for needle in ("app/checkout.py in submit_order", "Level: error", "Events: 7", "Users affected: 2",
                   "First seen: 2026-11-10T08:00:00Z", "Last seen: 2026-11-10T08:30:00Z",
                   finding.url, "TypeError: cannot read total of None",
                   "app/checkout.py:42 in submit_order", "app/views.py:88 in checkout"):
        assert needle in finding.text, needle
    assert "handler.py" not in finding.text  # in-app frames only
    assert finding.text.index("checkout.py:42") < finding.text.index("views.py:88")  # newest call first
    assert http.urls("/events/latest/") == [
        "https://sentry.io/api/0/organizations/example-org/issues/3/events/latest/"]

    assert src.poll() == []
    assert len(http.urls("/events/latest/")) == 1


def test_sentry_backfill_files_what_is_already_there(kv, clock):
    src, _ = sentry_source(kv, clock, Sentry([issue(1), issue(2)]), backfill=True)
    assert sorted(f.fingerprint for f in src.poll()) == ["sentry:1", "sentry:2"]
    assert src.poll() == []


def test_sentry_issue_crossing_min_events_later_is_filed_once(kv, clock):
    fake = Sentry([issue(1, count=50)])
    src, _ = sentry_source(kv, clock, fake, min_events=5)
    assert src.poll() == []  # baseline
    fake.issues.insert(0, issue(2, count=2))
    assert src.poll() == []  # seen, but below the threshold
    assert src.poll() == []
    fake.issues[0] = issue(2, count=5)
    assert [f.fingerprint for f in src.poll()] == ["sentry:2"]
    fake.issues[0] = issue(2, count=90)
    assert src.poll() == []


def test_sentry_enrichment_is_capped_per_poll(kv, clock):
    fake = Sentry()
    src, http = sentry_source(kv, clock, fake)
    assert src.poll() == []
    fake.issues = [issue(n) for n in range(100, 108)]
    found = src.poll()
    assert len(found) == 8
    assert len(http.urls("/events/latest/")) == sources.ENRICH_MAX == 5
    assert sum("Latest event:" in f.text for f in found) == 5
    for f in found:
        assert_valid(f)


def test_sentry_severity_and_stack_bound(kv, clock):
    fake = Sentry()
    src, _ = sentry_source(kv, clock, fake)
    src.poll()
    fake.issues = [issue(1, level="fatal"), issue(2, users=10), issue(3, users=9)]
    by_fp = {f.fingerprint: f.severity for f in src.poll()}
    assert by_fp == {"sentry:1": "high", "sentry:2": "high", "sentry:3": ""}

    frames = [{"filename": f"app/m{n}.py", "lineNo": n, "function": f"f{n}", "inApp": True}
              for n in range(200)]
    event = {"entries": [{"type": "exception", "data": {"values": [
        {"type": "ValueError", "value": "x", "stacktrace": {"frames": frames}}]}}]}
    lines = sources._sentry_stack(event)
    assert len(lines) == sources.STACK_LINES == 30
    assert lines[0] == "ValueError: x" and lines[1] == "  app/m199.py:199 in f199"
    assert sources._sentry_stack({"entries": "nonsense"}) == []


@pytest.mark.parametrize("status", [401, 403])
def test_sentry_auth_failure_leaves_state_untouched(kv, clock, status):
    fake = Sentry([issue(1)])
    fake.list_status = status
    src, _ = sentry_source(kv, clock, fake)
    assert src.poll() == []
    assert "rejected the token" in src.last_error and str(status) in src.last_error
    assert TOKEN not in src.last_error
    assert kv.kv_get("src:errors:baselined") is None and kv.kv_get("src:errors:filed") is None
    ok, detail = src.check()
    assert ok is False and "rejected the token" in detail and TOKEN not in detail

    fake.list_status = 200
    assert src.poll() == [] and src.last_error == ""  # this one is the baseline
    fake.issues.insert(0, issue(2))
    assert [f.fingerprint for f in src.poll()] == ["sentry:2"]


def test_sentry_errors_never_count_as_a_baseline(kv, clock):
    replies = {"reply": (500, {"detail": "boom"})}
    src, _ = sentry_source(kv, clock, lambda m, u: replies["reply"])
    assert src.poll() == [] and "500" in src.last_error
    replies["reply"] = (200, b"<html>not json</html>")
    assert src.poll() == [] and "JSON" in src.last_error
    replies["reply"] = (200, {"detail": "an object, not a list"})
    assert src.poll() == [] and src.last_error
    replies["reply"] = OSError(f"connection reset while sending {TOKEN}")
    assert src.poll() == [] and "Could not reach Sentry" in src.last_error
    assert TOKEN not in src.last_error
    replies["reply"] = RuntimeError("an http seam that misbehaves")
    assert src.poll() == [] and src.last_error
    assert kv.kv_get("src:errors:baselined") is None


def test_sentry_missing_token_is_reported_without_a_request(kv, clock):
    http = FakeHttp(Sentry())
    src = sources.build({"id": "errors", "type": "sentry", "org": "example-org", "project": "storefront",
                         "token_vault": "not-saved"}, secret=secret, kv=kv, http=http, clock=clock)
    assert src.poll() == []
    assert "not-saved" in src.last_error and http.calls == []
    assert src.check()[0] is False


def test_sentry_check_is_one_cheap_read_and_touches_no_state(kv, clock):
    src, http = sentry_source(kv, clock, Sentry([issue(1)]))
    ok, detail = src.check()
    assert ok is True and "example-org/storefront" in detail
    assert len(http.calls) == 1 and http.calls[0]["method"] == "GET"
    assert "limit=1" in http.calls[0]["url"]
    assert kv.kv_get("src:errors:baselined") is None and kv.kv_get("src:errors:last_poll") is None


def test_sentry_older_server_falls_back_to_the_project_list(kv, clock):
    def fake(method, url):
        if "/organizations/example-org/issues/?" in url:
            return 400, {"detail": "Invalid project parameter. Values must be numbers."}
        return 200, [issue(1)]
    src, http = sentry_source(kv, clock, fake, base="https://sentry.example.com/", backfill=True)
    found = src.poll()
    assert [f.fingerprint for f in found] == ["sentry:1"] and src.last_error == ""
    assert http.calls[1]["url"].startswith(
        "https://sentry.example.com/api/0/projects/example-org/storefront/issues/?")


def test_sentry_ignores_issues_from_another_project_and_bounds_filed(kv, clock):
    other = issue(9)
    other["project"] = {"id": "77", "slug": "another", "name": "Another"}
    fake = Sentry()
    src, _ = sentry_source(kv, clock, fake)
    src.poll()
    fake.issues = [other, issue(1)]
    assert [f.fingerprint for f in src.poll()] == ["sentry:1"]

    kv.kv_set("src:errors:filed", [str(n) for n in range(5000, 5000 + sources.FILED_KEEP)])
    fake.issues = [issue(2)]
    assert [f.fingerprint for f in src.poll()] == ["sentry:2"]
    filed = kv.kv_get("src:errors:filed")
    assert len(filed) == sources.FILED_KEEP == 2000 and filed[-1] == "2" and "5000" not in filed


# -- PostHog ----------------------------------------------------------------

COLUMNS = ["group_key", "event_count", "user_count", "sample_types", "sample_values", "sample_url",
           "issue_id", "first_seen", "last_seen"]


def group(key, count, users=1, kind="TypeError", message="x is undefined",
          url="https://example.com/checkout", issue_id="0190a1b2-0000-7000-8000-000000000001"):
    return [key, count, users, json.dumps([kind]), json.dumps([message]), url, issue_id,
            "2026-11-10T08:05:00Z", "2026-11-10T08:35:00Z"]


class PostHog:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.status = 200

    def __call__(self, method, url):
        if self.status != 200:
            return self.status, {"type": "authentication_error", "detail": f"bad key {TOKEN}"}
        return 200, {"results": self.rows, "columns": COLUMNS, "types": []}


def posthog_source(kv, clock, fake, **extra):
    cfg = {"id": "ph", "type": "posthog", "project_id": "12345", "token_vault": "monitor-token"}
    cfg.update(extra)
    http = FakeHttp(fake)
    return sources.build(cfg, secret=secret, kv=kv, http=http, clock=clock), http


def test_posthog_request_body_shape(kv, clock):
    src, http = posthog_source(kv, clock, PostHog(), min_events=4, window_min=30)
    assert src.poll() == [] and src.last_error == ""
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://us.posthog.com/api/projects/12345/query/"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["headers"]["Content-Type"] == "application/json"
    body = json.loads(call["data"])
    assert set(body) == {"query", "name"} and body["name"]
    assert set(body["query"]) == {"kind", "query"} and body["query"]["kind"] == "HogQLQuery"
    sql = body["query"]["query"]
    assert "FROM events" in sql and "event = '$exception'" in sql
    assert "INTERVAL 30 MINUTE" in sql and "HAVING count() >= 4" in sql
    assert "GROUP BY group_key" in sql
    for prop in ("$exception_fingerprint", "$exception_types", "$exception_values",
                 "$exception_issue_id", "$current_url"):
        assert f"properties.{prop}" in sql
    assert sql.index("$exception_fingerprint") < sql.index("$exception_types")  # fingerprint first
    for wanted in ("count()", "count(DISTINCT distinct_id)", "min(timestamp)", "max(timestamp)"):
        assert wanted in sql
    assert TOKEN not in call["url"] and TOKEN not in sql


def test_posthog_baseline_grouping_and_once_per_day(kv, clock):
    fake = PostHog([group("fp-old", 9)])
    src, _ = posthog_source(kv, clock, fake)
    assert src.poll() == []  # baseline: what is failing now is not a backlog

    fake.rows = [group("fp-old", 12), group("fp-new", 5, users=12),
                 group("fp-quiet", 2),  # below min_events even if the server returns it
                 group("tm:[\"RangeError\"]|[\"too deep\"]", 3, kind="RangeError", message="too deep",
                       issue_id="")]
    found = src.poll()
    assert len(found) == 2
    for f in found:
        assert_valid(f)
        assert re.fullmatch(r"posthog:[0-9a-f]{16}:20261110", f.fingerprint)
    assert len({f.fingerprint for f in found}) == 2
    first, second = found
    assert first.title == "TypeError: x is undefined" and first.severity == "high"
    for needle in ("Events: 5 in the last 60 minutes", "Users affected: 12", "x is undefined",
                   "https://example.com/checkout", "2026-11-10T08:05:00Z", "2026-11-10T08:35:00Z",
                   "https://us.posthog.com/project/12345/error_tracking"):
        assert needle in first.text, needle
    assert first.url == ("https://us.posthog.com/project/12345/error_tracking/"
                         "0190a1b2-0000-7000-8000-000000000001")
    assert second.title == "RangeError: too deep" and second.severity == ""
    assert second.url == "https://us.posthog.com/project/12345/error_tracking"

    clock.tick(3600)
    assert src.poll() == []  # same day, same groups

    clock.tick(86400)
    fake.rows = [group("fp-new", 6)]
    again = src.poll()
    assert len(again) == 1 and again[0].fingerprint.endswith(":20261111")
    assert again[0].fingerprint.split(":")[1] == first.fingerprint.split(":")[1]
    assert src.poll() == []


def test_posthog_backfill_and_rows_without_columns(kv, clock):
    class Bare(PostHog):
        def __call__(self, method, url):
            return 200, {"results": self.rows}
    src, _ = posthog_source(kv, clock, Bare([group("fp-a", 3)]), backfill=True,
                            host="https://eu.posthog.com/")
    found = src.poll()
    assert len(found) == 1 and "https://eu.posthog.com/project/12345/error_tracking" in found[0].text


def test_posthog_failures_leave_state_untouched(kv, clock):
    fake = PostHog([group("fp-a", 9)])
    fake.status = 401
    src, _ = posthog_source(kv, clock, fake)
    assert src.poll() == []
    assert "PostHog rejected the token" in src.last_error and TOKEN not in src.last_error
    assert kv.kv_get("src:ph:baselined") is None and kv.kv_get("src:ph:filed") is None
    assert src.check() == (False, src.last_error)

    fake.status = 500
    assert src.poll() == [] and "500" in src.last_error
    fake.status = 200
    fake.rows = "not a list"
    assert src.poll() == [] and src.last_error
    assert kv.kv_get("src:ph:baselined") is None

    fake.rows = [group("fp-a", 9)]
    assert src.poll() == [] and src.last_error == ""  # the real baseline
    assert kv.kv_get("src:ph:baselined") is True
    ok, detail = src.check()
    assert ok is True and "12345" in detail


# -- URL check --------------------------------------------------------------

class Site:
    def __init__(self):
        self.reply = (200, b"all good")

    def __call__(self, method, url):
        return self.reply


def http_source(kv, clock, site, **extra):
    cfg = {"id": "up", "type": "http", "url": "https://example.com/health"}
    cfg.update(extra)
    http = FakeHttp(site)
    return sources.build(cfg, kv=kv, http=http, clock=clock), http


def test_http_episodes_one_finding_per_outage(kv, clock):
    site = Site()
    src, http = http_source(kv, clock, site)
    assert src.poll() == []
    assert http.calls[0]["method"] == "GET" and http.calls[0]["timeout"] == 10

    site.reply = (503, b"down for maintenance")
    clock.tick(300)
    first_failure = clock.now
    assert src.poll() == []  # one failure is not an outage yet
    clock.tick(300)
    found = src.poll()
    assert len(found) == 1
    finding = found[0]
    assert_valid(finding)
    assert finding.fingerprint == "http:up:" + sources._stamp(clock.now)
    assert re.fullmatch(r"http:up:\d{8}T\d{6}Z", finding.fingerprint)
    assert finding.severity == "high" and finding.url == "https://example.com/health"
    for needle in ("https://example.com/health", "Expected: HTTP 200", "Received: HTTP 503",
                   "Failing since: " + sources._when(first_failure)):
        assert needle in finding.text, needle
    assert src.last_error == ""

    for _ in range(5):  # still down: nothing more
        clock.tick(300)
        assert src.poll() == []

    site.reply = (200, b"all good")
    clock.tick(300)
    assert src.poll() == []

    site.reply = OSError("connection refused")
    clock.tick(300)
    assert src.poll() == []
    clock.tick(300)
    second = src.poll()
    assert len(second) == 1 and second[0].fingerprint != finding.fingerprint
    assert "no answer" in second[0].text and "connection refused" in second[0].text


def test_http_single_blip_resets_the_count(kv, clock):
    site = Site()
    src, _ = http_source(kv, clock, site, fail_after=3)
    for reply in [(500, b""), (500, b""), (200, b"ok"), (500, b""), (500, b"")]:
        site.reply = reply
        clock.tick(300)
        assert src.poll() == []
    clock.tick(300)
    assert len(src.poll()) == 1


def test_http_contains_failure_quotes_the_start_of_the_body(kv, clock):
    site = Site()
    site.reply = (200, ("<html>Welcome to nginx" + "x" * 900).encode())
    src, _ = http_source(kv, clock, site, contains="status: ok", fail_after=1, expect_status="200")
    found = src.poll()
    assert len(found) == 1
    text = found[0].text
    assert_valid(found[0])
    assert "status: ok" in text and "does not contain" in text and "<html>Welcome to nginx" in text
    assert "x" * 301 not in text and "x" * 200 in text  # only the first 300 characters
    ok, detail = src.check()
    assert ok is False and "does not contain" in detail

    site.reply = (200, b"status: ok")
    assert src.poll() == []
    assert src.check()[0] is True


def test_http_check_does_not_change_the_outage_state(kv, clock):
    site = Site()
    site.reply = (500, b"")
    src, _ = http_source(kv, clock, site)
    for _ in range(4):
        assert src.check()[0] is False
    assert kv.kv_get("src:up:state") is None
    assert src.poll() == []  # still only the first counted failure


def test_http_hides_credentials_in_the_address(kv, clock):
    site = Site()
    site.reply = (500, b"")
    src, _ = http_source(kv, clock, site, fail_after=1,
                         url="https://monitor:hunter2pass@example.com/health?api_key=abc123secret&page=2")
    finding = src.poll()[0]
    blob = finding.text + finding.title + finding.url
    assert "hunter2pass" not in blob and "abc123secret" not in blob
    assert "https://example.com/health?api_key=***&page=2" in finding.text


@pytest.mark.parametrize("url", ["file:///etc/hosts", "ftp://example.com/x", "example.com/health",
                                 "javascript:alert(1)", "https://"])
def test_http_refuses_other_schemes(url):
    with pytest.raises(ValueError, match="url"):
        sources.build({"id": "up", "type": "http", "url": url})


def test_default_http_refuses_non_web_urls_without_touching_the_network():
    with pytest.raises(OSError):
        sources._default_http("GET", "file:///etc/hosts")


def test_default_http_against_a_local_server():
    """The real urllib path: error statuses come back, a token stays on its host."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.headers.get("Host"), self.path, self.headers.get("Authorization")))
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/ok")
                self.end_headers()
                return
            status = 200 if self.path == "/ok" else 404
            body = b"fine" if status == 200 else b"nothing here"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    auth = {"Authorization": f"Bearer {TOKEN}"}
    try:
        assert sources._default_http("GET", base + "/ok", headers=auth, timeout=5) == (200, b"fine")
        assert sources._default_http("GET", base + "/missing", timeout=5) == (404, b"nothing here")
        assert seen[0][2] == f"Bearer {TOKEN}"
        del seen[:]
        try:
            status, _ = sources._default_http("GET", base + "/moved", headers=auth, timeout=5)
        except OSError:
            status = None  # "localhost" does not resolve to this server here
        assert seen[0][1] == "/moved" and seen[0][2] == f"Bearer {TOKEN}"
        if status is not None:
            assert status == 200 and seen[1][1] == "/ok"
            assert seen[1][2] is None  # another host never sees the token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    with pytest.raises(OSError):
        sources._default_http("GET", base + "/ok", timeout=2)  # nothing listens now


# -- command check ----------------------------------------------------------

def test_command_real_failing_command(kv, clock, tmp_path):
    script = tmp_path / "check.py"
    script.write_text(
        "import sys\n"
        "for n in range(100):\n"
        "    print('line', n)\n"
        "sys.stdout.flush()\n"
        "print('cannot reach the database', file=sys.stderr)\n"
        "sys.exit(3)\n")
    cmd = f"{sys.executable} {script}"
    src = sources.build({"id": "nightly", "type": "command", "cmd": cmd, "cwd": str(tmp_path)},
                        kv=kv, clock=clock)
    found = src.poll()
    assert len(found) == 1 and src.last_error == ""
    finding = found[0]
    assert_valid(finding)
    assert finding.fingerprint == "cmd:nightly:" + sources._stamp(T0)
    assert finding.title == f"Check failed: {sys.executable}"
    assert cmd in finding.text and "exit code 3" in finding.text
    assert "cannot reach the database" in finding.text  # stderr is included
    assert "line 99" in finding.text and "line 41" in finding.text and "line 40\n" not in finding.text
    assert src.check()[0] is True

    clock.tick(3600)
    assert src.poll() == []  # still failing: same episode

    script.write_text("print('fine')\n")
    clock.tick(3600)
    assert src.poll() == []
    script.write_text("raise SystemExit(1)\n")
    clock.tick(3600)
    again = src.poll()
    assert len(again) == 1 and again[0].fingerprint != finding.fingerprint


def test_command_timeout_and_fail_after_with_fake_run(kv, clock):
    calls = []
    outcome = {"mode": "timeout"}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if outcome["mode"] == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"still waiting\n")
        return subprocess.CompletedProcess(argv, 0, stdout=b"ok\n")

    src = sources.build({"id": "nightly", "type": "command", "cmd": "make 'smoke test' --fast",
                         "timeout_s": 45, "fail_after": 2, "title": "Smoke test is failing"},
                        kv=kv, clock=clock, run=run)
    assert src.poll() == []
    argv, kwargs = calls[0]
    assert argv == ["make", "smoke test", "--fast"]  # shlex, no shell
    assert kwargs["timeout"] == 45 and not kwargs.get("shell")
    assert kwargs["stderr"] == subprocess.STDOUT and kwargs["stdout"] == subprocess.PIPE
    clock.tick(3600)
    found = src.poll()
    assert len(found) == 1
    assert found[0].title == "Smoke test is failing" and found[0].severity == ""
    assert "timed out after 45 seconds" in found[0].text and "still waiting" in found[0].text
    assert_valid(found[0])

    outcome["mode"] = "ok"
    clock.tick(3600)
    assert src.poll() == []
    assert kv.kv_get("src:nightly:state")["down"] is False


def test_command_output_is_bounded(kv, clock):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, stdout=("y" * 500 + "\n") * 200)
    src = sources.build({"id": "nightly", "type": "command", "cmd": "check"}, kv=kv, clock=clock, run=run)
    finding = src.poll()[0]
    assert_valid(finding)
    assert finding.title == "Check failed: check"
    assert finding.text.count("y") <= 4000


def test_command_that_cannot_start_is_an_error_not_an_outage(kv, clock):
    src = sources.build({"id": "nightly", "type": "command", "cmd": "ccc-no-such-program-xyz --flag"},
                        kv=kv, clock=clock)
    assert src.poll() == []
    assert "Could not start" in src.last_error and "ccc-no-such-program-xyz" in src.last_error
    assert kv.kv_get("src:nightly:state") is None
    ok, detail = src.check()
    assert ok is False and "not found" in detail


# -- build() and required_fields() -------------------------------------------

@pytest.mark.parametrize("cfg, names", [
    ({"id": "a", "type": "sentry", "project": "p", "token_vault": "t"}, "org"),
    ({"id": "a", "type": "sentry", "org": "o", "token_vault": "t"}, "project"),
    ({"id": "a", "type": "sentry", "org": "o", "project": "p"}, "token_vault"),
    ({"id": "a", "type": "sentry", "org": "o", "project": "p", "token_vault": "t", "base": "ftp://x"}, "base"),
    ({"id": "a", "type": "sentry", "org": "o", "project": "p", "token_vault": "t", "min_events": "many"},
     "min_events"),
    ({"id": "a", "type": "posthog", "token_vault": "t"}, "project_id"),
    ({"id": "a", "type": "posthog", "project_id": "1"}, "token_vault"),
    ({"id": "a", "type": "posthog", "project_id": "1", "token_vault": "t", "window_min": 0}, "window_min"),
    ({"id": "a", "type": "http"}, "url"),
    ({"id": "a", "type": "http", "url": "https://example.com", "fail_after": 0}, "fail_after"),
    ({"id": "a", "type": "command"}, "cmd"),
    ({"id": "a", "type": "command", "cmd": "   "}, "cmd"),
    ({"id": "a", "type": "command", "cmd": "echo 'unclosed"}, "cmd"),
    ({"id": "a", "type": "command", "cmd": "true", "every_s": 1}, "every_s"),
    ({"type": "command", "cmd": "true"}, "id"),
])
def test_build_names_the_field_that_is_wrong(cfg, names):
    with pytest.raises(ValueError, match=names):
        sources.build(cfg)


def test_build_unknown_type_and_bad_input():
    with pytest.raises(ValueError, match="pagerduty"):
        sources.build({"id": "a", "type": "pagerduty"})
    with pytest.raises(ValueError):
        sources.build({"id": "a"})
    with pytest.raises(ValueError):
        sources.build("sentry")
    with pytest.raises(ValueError):
        sources.required_fields("pagerduty")


def test_build_accepts_form_strings_and_every_type():
    src = sources.build({"id": "a", "type": "posthog", "project_id": 12345, "token_vault": "t",
                         "min_events": "5", "window_min": "15", "every_s": "900", "backfill": "yes",
                         "enabled": True, "queue": "STOREFRONT"})
    assert (src.min_events, src.window_min, src.every_s, src.backfill) == (5, 15, 900, True)
    assert isinstance(src, sources.Source) and src.last_error == ""
    assert {sources.build(c).type for c in (
        {"id": "a", "type": "sentry", "org": "o", "project": "p", "token_vault": "t"},
        {"id": "b", "type": "posthog", "project_id": "1", "token_vault": "t"},
        {"id": "c", "type": "http", "url": "http://example.com"},
        {"id": "d", "type": "command", "cmd": "true"},
        {"id": "e", "type": "github", "repo": "example-org/storefront"},
        {"id": "f", "type": "ci", "repo": "example-org/storefront"},
        {"id": "g", "type": "logs", "path": "/tmp/example.log"},
        {"id": "h", "type": "docker", "container": "sample-app"},
        {"id": "i", "type": "alertmanager", "url": "http://localhost:9093"})} == set(store.SOURCE_TYPES)


@pytest.mark.parametrize("kind", store.SOURCE_TYPES)
def test_required_fields_describe_a_form_that_builds(kind):
    fields = sources.required_fields(kind)
    assert fields and fields is not sources.required_fields(kind)
    names = [f["name"] for f in fields]
    assert len(names) == len(set(names))
    for f in fields:
        assert set(f) == {"name", "label", "secret", "optional", "placeholder"}
        assert isinstance(f["secret"], bool) and isinstance(f["optional"], bool)
        assert isinstance(f["placeholder"], str)
        assert 1 <= len(f["label"].split()) <= 3 and "_" not in f["label"]
        assert f["secret"] == f["name"].endswith("_vault")
    required = [f for f in fields if not f["optional"]]
    assert fields[:len(required)] == required  # required ones are asked first

    cfg = {"id": "x", "type": kind}
    with pytest.raises(ValueError):
        sources.build(cfg)  # nothing filled in yet
    samples = {"org": "example-org", "project": "storefront", "token_vault": "monitor-token",
               "project_id": "12345", "url": "https://example.com/health", "cmd": "true",
               "repo": "example-org/storefront", "container": "sample-app"}
    cfg.update({f["name"]: samples[f["name"]] for f in required})
    if kind == "logs":
        # The form offers three alternative targets; fill exactly one.
        cfg["path"] = "/tmp/example.log"
    assert sources.build(cfg).type == kind
    # Every optional placeholder is itself an accepted value.
    cfg.update({f["name"]: f["placeholder"] for f in fields
                if f["optional"] and not (kind == "logs" and f["name"] == "path")})
    assert sources.build(cfg).type == kind


# -- secrets and safety -----------------------------------------------------

def test_secret_never_reaches_errors_or_findings(kv, clock):
    def leaky(method, url):
        if "/events/latest/" in url:
            return 500, {"detail": TOKEN}
        if "limit=1" in url:
            return 403, {"detail": f"token {TOKEN} lacks scope"}
        return 200, [issue(1, title=f"KeyError: {TOKEN[:4]}")]
    src, http = sentry_source(kv, clock, leaky, backfill=True)
    found = src.poll()
    assert len(found) == 1
    assert_valid(found[0])
    ok, detail = src.check()
    assert ok is False and TOKEN not in detail
    for call in http.calls:
        assert TOKEN not in call["url"]
    assert TOKEN not in json.dumps(kv._all("SELECT key, value FROM kv"))
    assert TOKEN not in repr(vars(src).get("cfg"))


def test_poll_never_raises_even_when_the_store_breaks(clock):
    class BrokenKV:
        def kv_get(self, key, default=None):
            raise RuntimeError("disk is gone")

        def kv_set(self, key, value):
            raise RuntimeError("disk is gone")

    for cfg in ({"id": "a", "type": "sentry", "org": "o", "project": "p", "token_vault": "monitor-token"},
                {"id": "b", "type": "posthog", "project_id": "1", "token_vault": "monitor-token"},
                {"id": "c", "type": "http", "url": "https://example.com"},
                {"id": "d", "type": "command", "cmd": "true"}):
        src = sources.build(cfg, secret=secret, kv=BrokenKV(), clock=clock,
                            http=FakeHttp(lambda m, u: (500, b"")),
                            run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout=b""))
        assert src.poll() == []
        assert src.last_error and TOKEN not in src.last_error
        assert src.due() is False  # the attempt is still remembered in memory


def test_module_has_no_em_dashes_and_starts_with_the_license():
    text = open(sources.__file__, encoding="utf-8").read()
    lines = text.splitlines()
    assert lines[0] == "# Copyright (c) 2026 Amir Fish. All rights reserved."
    assert lines[1] == "# SPDX-License-Identifier: FSL-1.1-MIT"
    assert chr(0x2014) not in text and chr(0x2013) not in text


# -- GitHub issues ----------------------------------------------------------

def gh_issue(number, *, login="dana", association="NONE", body="The button does nothing.", pr=False):
    row = {"number": number, "title": f"Checkout problem {number}", "body": body,
           "html_url": f"https://github.com/example-org/storefront/issues/{number}",
           "user": {"login": login}, "author_association": association,
           "labels": [{"name": "bug"}], "created_at": "2026-11-10T08:00:00Z"}
    if pr:
        row["pull_request"] = {"url": "https://api.github.com/x"}
    return row


def github_source(kv, clock, handler, **extra):
    cfg = {"id": "issues", "type": "github", "repo": "example-org/storefront"}
    cfg.update(extra)
    http = FakeHttp(handler)
    return sources.build(cfg, secret=secret, kv=kv, http=http, clock=clock), http


def test_github_baseline_then_new_issue_once_and_no_pull_requests(kv, clock):
    rows = [gh_issue(2), gh_issue(1)]
    src, http = github_source(kv, clock, lambda m, u: (200, rows), label="bug")
    assert src.check() == (True, "GitHub answered for example-org/storefront.")
    assert src.poll() == [] and src.last_error == ""   # what is already open is not filed

    call = http.calls[-1]
    assert "Authorization" not in call["headers"]       # a public repository needs no token
    parts = urllib.parse.urlsplit(call["url"])
    assert parts.netloc == "api.github.com" and parts.path == "/repos/example-org/storefront/issues"
    query = urllib.parse.parse_qs(parts.query)
    assert query["state"] == ["open"] and query["labels"] == ["bug"] and query["per_page"] == ["25"]

    rows.insert(0, gh_issue(3))
    rows.insert(0, gh_issue(4, pr=True))
    found = src.poll()
    assert [f.fingerprint for f in found] == ["github:example-org_storefront_3"]
    finding = found[0]
    assert_valid(finding)
    assert finding.title == "Checkout problem 3"
    assert finding.text.startswith("GitHub issue #3 in example-org/storefront\nOpened by: dana\nLabels: bug")
    assert finding.text.endswith("The button does nothing.")
    assert finding.url == "https://github.com/example-org/storefront/issues/3"
    assert finding.sender == "dana on GitHub" and finding.hold is True
    assert src.poll() == []                             # never twice


def test_github_issue_from_a_member_is_not_held(kv, clock):
    rows = []
    src, _ = github_source(kv, clock, lambda m, u: (200, rows))
    assert src.poll() == []
    rows[:] = [gh_issue(7, login="owner", association="OWNER"), gh_issue(8, association="CONTRIBUTOR")]
    found = {f.title: f.hold for f in src.poll()}
    assert found == {"Checkout problem 7": False, "Checkout problem 8": True}


def test_github_uses_a_saved_token_and_accepts_a_pasted_address(kv, clock):
    src, http = github_source(kv, clock, lambda m, u: (200, []), token_vault="monitor-token",
                              repo="https://github.com/example-org/storefront.git")
    assert src.repo == "example-org/storefront"
    assert src.check()[0] is True
    assert http.calls[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    with pytest.raises(ValueError, match="owner/name"):
        sources.build({"id": "x", "type": "github", "repo": "storefront"})


def test_github_errors_are_plain_and_never_leak_the_token(kv, clock):
    src, _ = github_source(kv, clock, lambda m, u: (404, {"message": "Not Found"}))
    assert src.check() == (False, "GitHub did not find example-org/storefront. "
                                  "A private repository needs a token.")
    src, _ = github_source(kv, clock, lambda m, u: (403, {"message": "rate limit"}))
    ok, detail = src.check()
    assert not ok and detail.startswith("GitHub refused the request (HTTP 403).") and "token" in detail
    src, _ = github_source(kv, clock, lambda m, u: (401, {"message": f"bad {TOKEN}"}),
                           token_vault="monitor-token")
    ok, detail = src.check()
    assert not ok and "rejected the token" in detail and TOKEN not in detail
    assert src.poll() == [] and src.last_error and TOKEN not in src.last_error


def test_github_is_a_listed_source_with_an_optional_token():
    assert "github" in store.SOURCE_TYPES
    fields = {f["name"]: f for f in sources.required_fields("github")}
    assert fields["repo"]["optional"] is False
    assert fields["token_vault"]["secret"] is True and fields["token_vault"]["optional"] is True
