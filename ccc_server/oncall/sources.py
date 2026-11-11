# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""On-call sources: where bug reports come from besides a message channel.

Each source is polled on a timer and returns findings. This module only
detects. Another module turns a finding into a ticket, deduped by
``(source id, fingerprint)``, so a fingerprint stays the same for one problem
and changes when the problem comes back as a new episode.

    github    new open issues in one repository (a stranger's issue waits for the owner)
    sentry    new unresolved issues in one project
    posthog   exception groups in the last N minutes
    http      a URL that stopped answering as expected
    command   your own check: a command that exits non-zero

A source never holds a secret: ``token_vault`` names a CCC Vault entry that is
resolved on every poll. Stdlib-only; nothing runs at import.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import store as _store

MIN_EVERY_S = 30
FILED_KEEP = 2000
ENRICH_MAX = 5          # latest-event lookups per Sentry poll
STACK_LINES = 30
PAGE_LIMIT = 25
PAGES_PER_POLL = 4
MAX_BODY = 4 * 1024 * 1024
USER_AGENT = "ccc-oncall/1"

_FP_BAD = re.compile(r"[^A-Za-z0-9:._-]")
_SECRET_QUERY = re.compile(r"(token|key|secret|password|passwd|auth|sig)", re.I)


@dataclass
class Finding:
    fingerprint: str     # stable per problem episode
    title: str           # one line: what is wrong
    text: str            # the evidence an agent needs to start
    url: str = ""
    severity: str = ""   # "", "low", "high"
    sender: str = ""     # set when a person wrote the text (a GitHub issue)
    hold: bool = False   # written by a stranger: wait for the owner's go
    reporter: dict = field(default_factory=dict)


class _Problem(Exception):
    """A poll that could not finish. The message is safe to show."""


class _HttpStatus(_Problem):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# -- small helpers ----------------------------------------------------------

def _one_line(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _finding(fingerprint, title, text, url="", severity="", sender="", hold=False):
    text = str(text or "").strip()
    if len(text) > 6000:
        text = text[:5980].rstrip() + "\n[truncated]"
    return Finding(fingerprint=_FP_BAD.sub("_", str(fingerprint))[:120],
                   title=_one_line(title, 120) or "Problem reported by monitoring",
                   text=text, url=str(url or ""),
                   severity=severity if severity in ("", "low", "high") else "",
                   sender=_one_line(sender, 120), hold=bool(hold))


def _stamp(ts):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(float(ts)))


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(ts)))


def _need(cfg, name):
    value = cfg.get(name)
    text = "" if value is None or isinstance(value, bool) else str(value).strip()
    if not text:
        raise ValueError(f"Missing required field: {name}")
    return text


def _text(cfg, name, default=""):
    value = cfg.get(name)
    if value is None or isinstance(value, bool):
        return default
    return str(value).strip() or default


def _int(cfg, name, default, lo, hi):
    raw = cfg.get(name)
    if raw is None or raw == "":
        return default
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a whole number.")
    if value < lo or value > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}.")
    return value


def _bool(cfg, name, default=False):
    raw = cfg.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(raw)


def _web_url(cfg, name, default=""):
    """An http(s) URL from cfg without a trailing slash, or ValueError."""
    value = _text(cfg, name, default)
    if not value:
        raise ValueError(f"Missing required field: {name}")
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if parts is None or parts.scheme.lower() not in ("http", "https") or not host:
        raise ValueError(f"{name} must be an http or https URL.")
    return value


def _display_url(url):
    """The URL as it may be shown: no password, no secret-looking query value."""
    try:
        parts = urllib.parse.urlsplit(url)
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = "&".join(
            (pair.split("=", 1)[0] + "=***") if ("=" in pair and _SECRET_QUERY.search(pair.split("=", 1)[0]))
            else pair
            for pair in parts.query.split("&")) if parts.query else ""
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except ValueError:
        return "(unreadable URL)"


def _as_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _json(body):
    try:
        return json.loads(body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body)
    except (ValueError, TypeError, AttributeError):
        raise _Problem("bad-json")


def _first_item(raw):
    """PostHog stores exception types and messages as JSON lists; take the first."""
    text = str(raw or "").strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            items = None
        if isinstance(items, list):
            return next((str(i).strip() for i in items if str(i or "").strip()), "")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return str(json.loads(text)).strip()
        except ValueError:
            pass
    return text


class _MemoryKV:
    def __init__(self):
        self._data = {}

    def kv_get(self, key, default=None):
        return self._data.get(key, default)

    def kv_set(self, key, value):
        self._data[key] = json.loads(json.dumps(value))


# -- default HTTP -----------------------------------------------------------

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow http(s) redirects only, and never carry a token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise urllib.error.HTTPError(newurl, code, "Redirect to a non-web URL refused", headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old = urllib.parse.urlsplit(req.full_url)
            target = urllib.parse.urlsplit(newurl)
            if (target.scheme.lower(), target.netloc.lower()) != (old.scheme.lower(), old.netloc.lower()):
                for store_ in (new.headers, new.unredirected_hdrs):
                    for name in [h for h in store_ if h.lower() == "authorization"]:
                        del store_[name]
        return new


class HttpReply(tuple):
    """Two-value transport result with optional response metadata."""
    def __new__(cls, status, body, headers=None):
        result = super().__new__(cls, (status, body))
        result.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        return result


def _default_http(method, url, *, headers=None, data=None, timeout=20):
    """``(status, body)`` for any HTTP answer; OSError when there is none."""
    if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        raise OSError("Only http and https URLs are allowed")
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        opener = urllib.request.build_opener(_SafeRedirect())
        with opener.open(req, timeout=timeout) as resp:
            return HttpReply(int(resp.status), resp.read(MAX_BODY), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_BODY)
        except Exception:
            body = b""
        return HttpReply(int(exc.code), body, dict(exc.headers))
    except OSError:
        raise
    except (http.client.HTTPException, ValueError) as exc:
        raise OSError(f"{type(exc).__name__}") from exc


# -- base -------------------------------------------------------------------

class Source:
    type = ""
    default_every_s = 300

    def __init__(self, cfg, *, secret=None, kv=None, http=None, clock=None, run=None):
        if not isinstance(cfg, dict):
            raise ValueError("A source must be an object.")
        self.cfg = dict(cfg)
        self._id = _need(cfg, "id")
        self._secret = secret or _store.vault_secrets
        self._kv = kv if kv is not None else _MemoryKV()
        self._http = http or _default_http
        self._clock = clock or time.time
        self._run = run or subprocess.run
        self.every_s = _int(cfg, "every_s", self.default_every_s, MIN_EVERY_S, 7 * 86400)
        self.last_error = ""
        self._last_poll = None
        self._redact = set()
        self._setup(self.cfg)

    def _setup(self, cfg):
        """Read and validate the type's own fields. ValueError names the field."""

    @property
    def id(self):
        return self._id

    # -- state --
    def _key(self, name):
        return f"src:{self._id}:{name}"

    def _get(self, name, default=None):
        return self._kv.kv_get(self._key(name), default)

    def _set(self, name, value):
        self._kv.kv_set(self._key(name), value)

    def _filed(self):
        raw = self._get("filed", [])
        return [str(i) for i in raw] if isinstance(raw, list) else []

    def _mark_filed(self, new_ids):
        filed = self._filed()
        have = set(filed)
        for item in new_ids:
            if item not in have:
                filed.append(item)
                have.add(item)
        self._set("filed", filed[-FILED_KEEP:])

    # -- safe text --
    def _safe(self, text):
        text = str(text or "")
        for value in self._redact:
            if value:
                text = text.replace(value, "***")
        return _one_line(text, 300)

    def _token(self, label):
        entry = _text(self.cfg, "token_vault")
        try:
            value = str(self._secret(entry) or "").strip()
        except Exception:
            value = ""
        if not value:
            raise _Problem(f"No {label} token is saved in the Vault entry named {entry!r}.")
        self._redact.add(value)
        return value

    def _request(self, method, url, **kwargs):
        reply = self._http(method, url, **kwargs)
        self._response_headers = getattr(reply, "headers", {})
        status, body = reply
        if int(status) in (429, 503):
            value = self._response_headers.get("retry-after", "")
            try:
                if value.isdigit():
                    delay = int(value)
                else:
                    target = parsedate_to_datetime(value).timestamp()
                    server_date = self._response_headers.get("date")
                    origin = parsedate_to_datetime(server_date).timestamp() if server_date else self._clock()
                    delay = max(0, target - origin)
                self._set("retry_at", float(self._clock()) + delay)
            except (ValueError, TypeError, OverflowError):
                pass
        return status, body

    def _cooling_down(self):
        try:
            return float(self._get("retry_at", 0) or 0) > float(self._clock())
        except Exception:
            return False

    def _api(self, label, method, url, *, token, data=None, hint=""):
        """One authenticated JSON call. Returns the parsed body or raises _Problem."""
        if self._cooling_down():
            raise _Problem(f"{label} asked to wait before the next request (Retry-After).")
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, body = self._request(method, url, headers=headers, data=data, timeout=20)
            status = int(status)
        except _Problem:
            raise
        except Exception as exc:
            raise _Problem(f"Could not reach {label} ({type(exc).__name__}).")
        if status in (401, 403):
            what = "rejected the token" if token else "refused the request"
            raise _Problem(f"{label} {what} (HTTP {status}). {hint}".strip())
        if status == 404:
            raise _HttpStatus(status, f"{label} did not find that project (HTTP 404). Check the names.")
        if status == 429:
            raise _Problem(f"{label} is rate limiting this token (HTTP 429). It will be tried again.")
        if status < 200 or status >= 300:
            raise _HttpStatus(status, f"{label} answered HTTP {status}.")
        try:
            return _json(body)
        except _Problem:
            raise _Problem(f"{label} sent an answer that is not JSON.")

    # -- public --
    def due(self):
        if self._cooling_down():
            return False
        try:
            last = self._get("last_poll")
        except Exception:
            last = None
        marks = [float(v) for v in (last, self._last_poll) if isinstance(v, (int, float))]
        if not marks:
            return True
        elapsed = float(self._clock()) - max(marks)
        return elapsed >= self.every_s or elapsed < 0

    def check(self):
        try:
            detail = self._check()
            return True, self._safe(detail)
        except _Problem as exc:
            return False, self._safe(exc)
        except Exception as exc:
            return False, self._safe(f"Check failed ({type(exc).__name__}).")

    def poll(self):
        if self._cooling_down():
            return []
        try:
            self._last_poll = float(self._clock())
            self._set("last_poll", self._last_poll)
        except Exception:
            pass
        try:
            findings = list(self._poll() or [])
            for finding in findings:
                for value in self._redact:
                    finding.title = finding.title.replace(value, "***")
                    finding.text = finding.text.replace(value, "***")
                    finding.url = finding.url.replace(value, "***")
            self.last_error = ""
            return findings
        except _Problem as exc:
            self.last_error = self._safe(exc) or "The check could not finish."
        except Exception as exc:
            self.last_error = self._safe(f"The check could not finish ({type(exc).__name__}).")
        return []

    def _check(self):
        raise _Problem("This source cannot be checked.")

    def _poll(self):
        return []

    # -- outage episodes (http, command) --
    def _episode(self, ok, make):
        """Count failures; ``make(started, first_failure)`` builds the one finding.

        A finding is returned once, when the failure count reaches
        ``fail_after``. Nothing more is returned until a success ends the
        episode, so the next outage gets a new fingerprint.
        """
        raw = self._get("state")
        state = raw if isinstance(raw, dict) else {}
        fails, down = _as_int(state.get("fails")), bool(state.get("down"))
        if ok:
            if fails or down:
                self._set("state", {"fails": 0, "down": False, "since": 0, "first_fail": 0})
            return []
        now = float(self._clock())
        first = float(state.get("first_fail") or 0) if fails else 0
        first = first or now
        fails += 1
        if down or fails < self.fail_after:
            self._set("state", {"fails": fails, "down": down, "first_fail": first,
                                "since": state.get("since") or 0})
            return []
        finding = make(now, first)
        self._set("state", {"fails": fails, "down": True, "since": now, "first_fail": first})
        return [finding]


# -- Sentry -----------------------------------------------------------------

class SentrySource(Source):
    """New unresolved issues in one Sentry project."""
    type = "sentry"
    default_every_s = 300
    _hint = "It needs the event:read scope."

    def _setup(self, cfg):
        self.org = _need(cfg, "org")
        self.project = _need(cfg, "project")
        _need(cfg, "token_vault")
        self.base = _web_url(cfg, "base", "https://sentry.io").rstrip("/")
        self.environment = _text(cfg, "environment")
        self.min_events = _int(cfg, "min_events", 1, 1, 10 ** 9)
        self.backfill = _bool(cfg, "backfill")

    def _org_url(self):
        return f"{self.base}/api/0/organizations/{urllib.parse.quote(self.org, safe='')}"

    def _list(self, token, limit, cursor=""):
        """Unresolved issues, newest first, one page."""
        params = [("query", "is:unresolved"), ("sort", "new"), ("limit", str(limit)),
                  ("statsPeriod", "14d")]
        if self.environment:
            params.append(("environment", self.environment))
        if cursor:
            params.append(("cursor", cursor))
        query = urllib.parse.urlencode([("project", self.project)] + params)
        try:
            rows = self._api("Sentry", "GET", f"{self._org_url()}/issues/?{query}",
                             token=token, hint=self._hint)
        except _HttpStatus as exc:
            if exc.status not in (400, 422):
                raise
            # The project-scoped list accepts a slug. GlitchTip 4 also uses
            # its model field name for newest-first sorting (HTTP 422).
            if exc.status == 422:
                params = [(key, "-first_seen" if key == "sort" else value)
                          for key, value in params]
            path = "/".join(urllib.parse.quote(p, safe="") for p in (self.org, self.project))
            rows = self._api("Sentry", "GET",
                             f"{self.base}/api/0/projects/{path}/issues/?{urllib.parse.urlencode(params)}",
                             token=token, hint=self._hint)
        self._next_cursor = ""
        for part in getattr(self, "_response_headers", {}).get("link", "").split(","):
            if 'rel="next"' not in part or 'results="false"' in part:
                continue
            match = re.search(r'<([^>]+)>', part)
            if match:
                # Extract only the opaque cursor. Never follow a supplied host
                # or path with the bearer token.
                values = urllib.parse.parse_qs(urllib.parse.urlsplit(match[1]).query)
                candidate = (values.get("cursor") or [""])[0]
                if len(candidate) <= 1000 and candidate != cursor:
                    self._next_cursor = candidate
        if not isinstance(rows, list):
            raise _Problem("Sentry sent an answer in an unexpected shape.")
        issues = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("id") or "").strip():
                continue
            project = row.get("project")
            if isinstance(project, dict) and (project.get("slug") or project.get("id")):
                if self.project not in (str(project.get("slug") or ""), str(project.get("id") or "")):
                    continue
            issues.append(row)
        return issues

    def _check(self):
        self._list(self._token("Sentry"), 1)
        return f"Sentry answered for {self.org}/{self.project}."

    def _poll(self):
        token = self._token("Sentry")
        cursor = str(self._get("scan_cursor", "") or "")
        issues = self._list(token, PAGE_LIMIT, cursor)
        if not self._get("baselined") and not self.backfill:
            if len(issues) >= PAGE_LIMIT:
                # Compare server timestamps with server timestamps, not the
                # host clock. The latest observed issue is a safe fallback for
                # compatible servers that omit Date.
                stamps = []
                for issue in issues:
                    try:
                        stamps.append(datetime.fromisoformat(str(issue.get("firstSeen", "")).replace("Z", "+00:00")).timestamp())
                    except (ValueError, TypeError, OverflowError):
                        pass
                try:
                    stamps.append(parsedate_to_datetime(self._response_headers.get("date", "")).timestamp())
                except (ValueError, TypeError, AttributeError, OverflowError):
                    pass
                if not stamps:
                    raise _Problem("Sentry did not provide a server date or first-seen timestamps. "
                                   "Cannot safely baseline a full page.")
                self._set("baseline_before", max(stamps))
            self._mark_filed([str(i["id"]).strip() for i in issues])
            self._set("baselined", True)
            return []
        for _ in range(PAGES_PER_POLL - 1):
            cursor = self._next_cursor
            if not cursor:
                break
            issues += self._list(token, PAGE_LIMIT, cursor)
        next_cursor = self._next_cursor
        filed = set(self._filed())
        fresh = [i for i in issues
                 if str(i["id"]).strip() not in filed and self._events(i) >= self.min_events
                 and not self._before_baseline(i)]
        fresh = list({str(issue["id"]): issue for issue in fresh}.values())
        findings = []
        for index, issue in enumerate(fresh):
            stack = self._latest_event(token, str(issue["id"]).strip()) if index < ENRICH_MAX else []
            findings.append(self._to_finding(issue, stack))
        self._mark_filed([str(i["id"]).strip() for i in fresh])
        self._set("scan_cursor", next_cursor)
        if not self._get("baselined"):
            self._set("baselined", True)
        return findings

    def _before_baseline(self, issue):
        cutoff = self._get("baseline_before")
        if cutoff is None:
            return False
        try:
            first = datetime.fromisoformat(str(issue.get("firstSeen", "")).replace("Z", "+00:00"))
            if first.tzinfo is None:
                first = first.replace(tzinfo=timezone.utc)
            return first.timestamp() <= float(cutoff)
        except (ValueError, TypeError, OverflowError):
            # Without a timestamp we cannot safely label an unseen issue new.
            return True

    @staticmethod
    def _events(issue):
        count = issue.get("count")
        if count is None and isinstance(issue.get("lifetime"), dict):
            count = issue["lifetime"].get("count")
        return _as_int(count)

    def _latest_event(self, token, issue_id):
        """The latest event's exception and top frames. Empty when unavailable."""
        url = f"{self._org_url()}/issues/{urllib.parse.quote(issue_id, safe='')}/events/latest/"
        try:
            event = self._api("Sentry", "GET", url, token=token)
        except _Problem:
            return []
        return _sentry_stack(event) if isinstance(event, dict) else []

    def _to_finding(self, issue, stack):
        issue_id = str(issue["id"]).strip()
        level = str(issue.get("level") or "").lower()
        users = _as_int(issue.get("userCount"))
        link = str(issue.get("permalink") or "") or (
            f"{self.base}/organizations/{urllib.parse.quote(self.org, safe='')}/issues/"
            f"{urllib.parse.quote(issue_id, safe='')}/")
        meta = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
        title = issue.get("title") or meta.get("title") or meta.get("type") or f"Sentry issue {issue_id}"
        short = str(issue.get("shortId") or issue_id)
        lines = [f"Sentry issue {short} in {self.org}/{self.project}",
                 f"Where: {_one_line(issue.get('culprit'), 300) or 'not reported'}",
                 f"Level: {level or 'not reported'}",
                 f"Events: {self._events(issue)}",
                 f"Users affected: {users}",
                 f"First seen: {issue.get('firstSeen') or 'not reported'}",
                 f"Last seen: {issue.get('lastSeen') or 'not reported'}",
                 f"Link: {link}"]
        if self.environment:
            lines.insert(1, f"Environment: {self.environment}")
        if stack:
            lines += ["", "Latest event:"] + stack
        return _finding(f"sentry:{issue_id}", title, "\n".join(lines), url=link,
                        severity="high" if (level == "fatal" or users >= 10) else "")


def _sentry_stack(event, limit=STACK_LINES):
    """Exception type and value, then the top in-app frames, newest call first."""
    values = []
    for entry in event.get("entries") or []:
        if isinstance(entry, dict) and entry.get("type") == "exception":
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            values = [v for v in (data.get("values") or []) if isinstance(v, dict)]
            break
    lines = []
    # Chained exceptions and frames are both listed oldest first.
    for exc in reversed(values):
        if len(lines) >= limit:
            break
        head = ": ".join(p for p in (_one_line(exc.get("type"), 120), _one_line(exc.get("value"), 400)) if p)
        lines.append(head or "Exception")
        trace = exc.get("stacktrace") if isinstance(exc.get("stacktrace"), dict) else {}
        frames = [f for f in (trace.get("frames") or []) if isinstance(f, dict)]
        chosen = [f for f in frames if f.get("inApp")] or frames
        for frame in reversed(chosen):
            if len(lines) >= limit:
                break
            where = frame.get("filename") or frame.get("module") or frame.get("absPath") or "?"
            line_no = frame.get("lineNo")
            spot = f"{where}:{line_no}" if line_no is not None else str(where)
            lines.append("  " + _one_line(f"{spot} in {frame.get('function') or '?'}", 240))
    if not lines:
        message = _one_line(event.get("message") or event.get("title"), 400)
        if message:
            lines.append(message)
    return lines[:limit]


# -- PostHog ----------------------------------------------------------------

_POSTHOG_COLUMNS = ("group_key", "event_count", "user_count", "sample_types", "sample_values",
                    "sample_url", "issue_id", "first_seen", "last_seen")


class PostHogSource(Source):
    """Exception groups PostHog saw in the last ``window_min`` minutes."""
    type = "posthog"
    default_every_s = 600
    _hint = "It must be a personal API key with the Query Read permission."

    def _setup(self, cfg):
        self.project_id = _need(cfg, "project_id")
        _need(cfg, "token_vault")
        self.host = _web_url(cfg, "host", "https://us.posthog.com").rstrip("/")
        self.min_events = _int(cfg, "min_events", 3, 1, 10 ** 9)
        self.window_min = _int(cfg, "window_min", 60, 1, 7 * 24 * 60)
        self.backfill = _bool(cfg, "backfill")

    def hogql(self):
        return (
            "SELECT "
            "coalesce(nullIf(ifNull(toString(properties.$exception_fingerprint), ''), ''), "
            "concat('tm:', ifNull(toString(properties.$exception_types), ''), '|', "
            "ifNull(toString(properties.$exception_values), ''))) AS group_key, "
            "count() AS event_count, "
            "count(DISTINCT distinct_id) AS user_count, "
            "any(ifNull(toString(properties.$exception_types), '')) AS sample_types, "
            "any(ifNull(toString(properties.$exception_values), '')) AS sample_values, "
            "any(ifNull(toString(properties.$current_url), '')) AS sample_url, "
            "any(ifNull(toString(properties.$exception_issue_id), '')) AS issue_id, "
            "min(timestamp) AS first_seen, "
            "max(timestamp) AS last_seen "
            "FROM events "
            f"WHERE event = '$exception' AND timestamp >= now() - INTERVAL {int(self.window_min)} MINUTE "
            "GROUP BY group_key "
            f"HAVING count() >= {int(self.min_events)} "
            "ORDER BY group_key "
            "LIMIT 100"
        )

    def _query(self, token, sql, name):
        url = f"{self.host}/api/projects/{urllib.parse.quote(self.project_id, safe='')}/query/"
        body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}, "name": name}).encode("utf-8")
        answer = self._api("PostHog", "POST", url, token=token, data=body, hint=self._hint)
        if not isinstance(answer, dict) or not isinstance(answer.get("results"), list):
            raise _Problem("PostHog sent an answer in an unexpected shape.")
        columns = answer.get("columns")
        names = [str(c) for c in columns] if isinstance(columns, list) and columns else list(_POSTHOG_COLUMNS)
        rows = []
        for row in answer["results"]:
            if isinstance(row, dict):
                rows.append(row)
            elif isinstance(row, (list, tuple)):
                rows.append(dict(zip(names, row)))
        return rows

    def _check(self):
        self._query(self._token("PostHog"), "SELECT 1", "ccc on-call check")
        return f"PostHog answered for project {self.project_id}."

    def _page(self, issue_id=""):
        base = f"{self.host}/project/{urllib.parse.quote(self.project_id, safe='')}/error_tracking"
        return f"{base}/{urllib.parse.quote(issue_id, safe='')}" if issue_id else base

    def _poll(self):
        token = self._token("PostHog")
        sql = self.hogql()
        baseline = not self._get("baselined") and not self.backfill
        offset = 0 if baseline else _as_int(self._get("scan_offset"))
        rows = self._query(token, sql + (f" OFFSET {offset}" if offset else ""), "ccc on-call exceptions")
        if baseline:
            page = list(rows)
            while len(page) == 100:
                if len(rows) > FILED_KEEP:
                    raise _Problem("PostHog has too many existing groups to baseline safely. "
                                   "Narrow window_min or raise min_events.")
                page = self._query(token, sql + f" OFFSET {len(rows)}", "ccc on-call baseline")
                rows += page
            if len(rows) > FILED_KEEP:
                raise _Problem("PostHog has too many existing groups to baseline safely. "
                               "Narrow window_min or raise min_events.")
        next_offset = 0
        if not baseline:
            page = list(rows)
            for _ in range(PAGES_PER_POLL - 1):
                if len(page) < 100:
                    break
                page = self._query(token, sql + f" OFFSET {offset + len(rows)}", "ccc on-call exceptions")
                rows += page
            if len(page) == 100:
                next_offset = offset + len(rows)
        day = time.strftime("%Y%m%d", time.gmtime(float(self._clock())))
        groups = []
        for row in rows:
            key = str(row.get("group_key") or "").strip()
            if not key or _as_int(row.get("event_count")) < self.min_events:
                continue
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            groups.append((f"posthog:{digest}:{day}", row))
        if not self._get("baselined") and not self.backfill:
            self._mark_filed([fp for fp, _ in groups])
            self._set("baselined", True)
            return []
        filed = set(self._filed())
        findings, fresh = [], []
        for fingerprint, row in groups:
            if fingerprint in filed:
                continue
            filed.add(fingerprint)
            fresh.append(fingerprint)
            findings.append(self._to_finding(fingerprint, row))
        self._mark_filed(fresh)
        self._set("scan_offset", next_offset)
        if not self._get("baselined"):
            self._set("baselined", True)
        return findings

    def _to_finding(self, fingerprint, row):
        kind = _one_line(_first_item(row.get("sample_types")), 120)
        message = _one_line(_first_item(row.get("sample_values")), 1000)
        events, users = _as_int(row.get("event_count")), _as_int(row.get("user_count"))
        page = _one_line(row.get("sample_url"), 500)
        issue_id = str(row.get("issue_id") or "").strip()
        link = self._page(issue_id if re.fullmatch(r"[A-Za-z0-9-]{1,64}", issue_id) else "")
        title = ": ".join(p for p in (kind, message) if p) or "Exception reported by PostHog"
        lines = [f"PostHog exceptions in project {self.project_id}",
                 f"Events: {events} in the last {self.window_min} minutes",
                 f"Users affected: {users}",
                 f"First seen: {row.get('first_seen') or 'not reported'}",
                 f"Last seen: {row.get('last_seen') or 'not reported'}",
                 f"Type: {kind or 'not reported'}",
                 f"Message: {message or 'not reported'}",
                 f"Page: {page or 'not reported'}",
                 f"Link: {link}",
                 f"All errors: {self._page()}"]
        return _finding(fingerprint, title, "\n".join(lines), url=link,
                        severity="high" if users >= 10 else "")


# -- URL check --------------------------------------------------------------

class HttpSource(Source):
    """A URL that must keep answering as expected."""
    type = "http"
    default_every_s = 300

    def _setup(self, cfg):
        self.url = _web_url(cfg, "url")
        self.expect_status = _int(cfg, "expect_status", 200, 100, 599)
        raw = cfg.get("contains")
        self.contains = "" if raw is None or isinstance(raw, bool) else str(raw)
        self.timeout_s = _int(cfg, "timeout_s", 10, 1, 300)
        self.fail_after = _int(cfg, "fail_after", 2, 1, 1000)

    def _expected(self):
        want = f"HTTP {self.expect_status}"
        if self.contains:
            want += f" and a body that contains {_one_line(self.contains, 200)!r}"
        return want

    def _probe(self):
        """``(ok, what was received)`` for one request. Never raises."""
        try:
            status, body = self._request("GET", self.url, headers={"User-Agent": USER_AGENT},
                                      timeout=self.timeout_s)
            status = int(status)
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or exc).replace(self.url, _display_url(self.url))
            reason = _one_line(reason, 160)
            name = type(exc).__name__
            return False, f"no answer ({name}: {reason})" if reason else f"no answer ({name})"
        if status != self.expect_status:
            return False, f"HTTP {status}"
        if self.contains:
            text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
            if self.contains not in text:
                start = "".join(c if (c.isprintable() or c in "\n\t") else " " for c in text[:300]).strip()
                return False, (f"HTTP {status}, but the body does not contain the expected text. "
                               f"Body starts with:\n{start or '(empty body)'}")
        return True, f"HTTP {status}"

    def _check(self):
        ok, got = self._probe()
        if not ok:
            raise _Problem(f"Expected {self._expected()}. Received {got}")
        return f"{_display_url(self.url)} answered {got}."

    def _poll(self):
        ok, got = self._probe()
        shown = _display_url(self.url)

        def make(started, first_failure):
            lines = [f"URL: {shown}",
                     f"Expected: {self._expected()}",
                     f"Received: {got}",
                     f"Failing since: {_when(first_failure)}",
                     f"Failed checks in a row: {self.fail_after}",
                     f"Checked every {self.every_s} seconds"]
            host = urllib.parse.urlsplit(shown).netloc or shown
            return _finding(f"http:{self.id}:{_stamp(started)}", f"{host} is not answering as expected",
                            "\n".join(lines), url=shown, severity="high")

        return self._episode(ok, make)


# -- command check ----------------------------------------------------------

class CommandSource(Source):
    """Your own check: a command that exits non-zero when something is wrong."""
    type = "command"
    default_every_s = 3600

    def _setup(self, cfg):
        self.cmd = _need(cfg, "cmd")
        try:
            self.argv = shlex.split(self.cmd)
        except ValueError:
            raise ValueError("cmd has an unclosed quote.")
        if not self.argv:
            raise ValueError("Missing required field: cmd")
        self.cwd = os.path.expanduser(_text(cfg, "cwd"))
        self.timeout_s = _int(cfg, "timeout_s", 300, 1, 6 * 3600)
        self.fail_after = _int(cfg, "fail_after", 1, 1, 1000)
        self.title = _text(cfg, "title", f"Check failed: {self.argv[0]}")

    def _check(self):
        if self.cwd and not os.path.isdir(self.cwd):
            raise _Problem("The folder in cwd does not exist.")
        program = self.argv[0]
        if os.sep in program and not os.path.isabs(program) and self.cwd:
            program = os.path.join(self.cwd, program)
        if not shutil.which(program):
            raise _Problem(f"Command not found: {self.argv[0]}")
        return f"Found {self.argv[0]}. It runs every {self.every_s} seconds."

    def _poll(self):
        timed_out, code, output = False, None, ""
        try:
            done = self._run(self.argv, cwd=self.cwd or None, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout_s)
            code = _as_int(getattr(done, "returncode", 1), 1)
            output = getattr(done, "stdout", "")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            output = exc.output if exc.output is not None else getattr(exc, "stdout", "")
        except OSError as exc:
            raise _Problem(f"Could not start the command ({type(exc).__name__}): {self.argv[0]}")
        if isinstance(output, (bytes, bytearray)):
            output = output.decode("utf-8", "replace")
        output = str(output or "")
        ok = not timed_out and code == 0

        def make(started, first_failure):
            tail = "\n".join(output.splitlines()[-60:])[-4000:].strip()
            result = (f"timed out after {self.timeout_s} seconds" if timed_out else f"exit code {code}")
            lines = [f"Command: {self.cmd}"]
            if self.cwd:
                lines.append(f"Folder: {self.cwd}")
            lines += [f"Result: {result}",
                      f"Failing since: {_when(first_failure)}",
                      "",
                      "Last lines of output:",
                      tail or "(no output)"]
            return _finding(f"cmd:{self.id}:{_stamp(started)}", self.title, "\n".join(lines))

        return self._episode(ok, make)


# -- GitHub issues ----------------------------------------------------------

_GITHUB_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+")
_GITHUB_INSIDERS = ("OWNER", "MEMBER", "COLLABORATOR")
_GITHUB_OVERLAP_S = 120


class GitHubSource(Source):
    """New open issues in one GitHub repository.

    Anyone can open an issue on a public repository, so an issue from someone
    who is not the owner, a member or a collaborator is marked ``hold``: the
    owner says go before an agent reads it as work.
    """
    type = "github"
    default_every_s = 300

    def _setup(self, cfg):
        repo = _need(cfg, "repo").strip()
        repo = re.sub(r"^(https?://)?(www\.)?github\.com/", "", repo, flags=re.I).strip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not _GITHUB_REPO.fullmatch(repo):
            raise ValueError("Repository must look like owner/name.")
        self.repo = repo
        self.base = _web_url(cfg, "base", "https://api.github.com").rstrip("/")
        self.label = _text(cfg, "label")
        self.backfill = _bool(cfg, "backfill")

    def _maybe_token(self):
        return self._token("GitHub") if _text(self.cfg, "token_vault") else ""

    def _list(self, token, limit):
        """Open issues, newest first, one page. Pull requests are left out."""
        params = [("state", "open"), ("sort", "created"), ("direction", "desc"),
                  ("per_page", str(limit))]
        if self.label:
            params.append(("labels", self.label))
        url = f"{self.base}/repos/{self.repo}/issues?{urllib.parse.urlencode(params)}"
        hint = ("The token needs read access to issues." if token
                else "Add a token to read a private repository or to raise the hourly limit.")
        try:
            rows = self._api("GitHub", "GET", url, token=token, hint=hint)
        except _HttpStatus as exc:
            if exc.status != 404:
                raise
            raise _Problem(f"GitHub did not find {self.repo}. A private repository needs a token.")
        if not isinstance(rows, list):
            raise _Problem("GitHub sent an answer in an unexpected shape.")
        return [row for row in rows
                if isinstance(row, dict) and _as_int(row.get("number")) > 0
                and "pull_request" not in row]

    def _check(self):
        self._list(self._maybe_token(), 1)
        return f"GitHub answered for {self.repo}."

    def _poll(self):
        issues = self._list(self._maybe_token(), PAGE_LIMIT)
        ids = [str(_as_int(i.get("number"))) for i in issues]
        if not self._get("baselined") and not self.backfill:
            self._mark_filed(ids)
            self._set("baselined", True)
            return []
        filed = set(self._filed())
        fresh = [i for i in issues if str(_as_int(i.get("number"))) not in filed]
        findings = [self._to_finding(issue) for issue in fresh]
        self._mark_filed([str(_as_int(i.get("number"))) for i in fresh])
        if not self._get("baselined"):
            self._set("baselined", True)
        return findings

    def _to_finding(self, issue):
        number = _as_int(issue.get("number"))
        user = issue.get("user") if isinstance(issue.get("user"), dict) else {}
        login = _one_line(user.get("login"), 60) or "someone"
        labels = [_one_line(lb.get("name") if isinstance(lb, dict) else lb, 40)
                  for lb in issue.get("labels") or []]
        link = str(issue.get("html_url") or "")
        if not link.lower().startswith(("https://", "http://")):
            link = ""
        lines = [f"GitHub issue #{number} in {self.repo}",
                 f"Opened by: {login}",
                 f"Labels: {', '.join(lb for lb in labels if lb) or 'none'}",
                 f"Opened: {issue.get('created_at') or 'not reported'}"]
        if link:
            lines.append(f"Link: {link}")
        body = str(issue.get("body") or "").strip()
        if body:
            lines += ["", body]
        insider = str(issue.get("author_association") or "").upper() in _GITHUB_INSIDERS
        finding = _finding(f"github:{self.repo}#{number}", issue.get("title") or f"Issue #{number}",
                        "\n".join(lines), url=link, sender=f"{login} on GitHub", hold=not insider)
        finding.reporter = {"channel": f"github:{self.id}", "address": login,
                            "thread": f"github:{self.id}:{self.repo}:{number}",
                            "author_id": user.get("id"), "author_type": user.get("type", "User")}
        return finding

    def send(self, msg):
        """Reporter transport; called only by the existing customer send path."""
        prefix = f"github:{self.id}:{self.repo}:"
        number = msg.thread.removeprefix(prefix)
        if not msg.thread.startswith(prefix) or not number.isdigit():
            return _store.Receipt(ok=False, error="The GitHub issue destination is invalid.")
        try:
            token = self._maybe_token()
            if not token:
                raise _Problem("Save a GitHub token with write access to issues before sending.")
            row = self._api("GitHub", "POST",
                            f"{self.base}/repos/{self.repo}/issues/{number}/comments",
                            token=token, data=json.dumps({"body": msg.text}).encode(),
                            hint="The token needs write access to issues.")
            return _store.Receipt(ok=True, external_id=str(row["id"]))
        except (_Problem, KeyError, TypeError) as exc:
            return _store.Receipt(ok=False, error=self._safe(exc))

    def receive_replies(self, deliver):
        """One repository-wide comment page per source tick, with a durable cursor.

        Keep the scan's since value fixed while paging. Advance it only after
        the last page; overlapping scans are deduped by the engine's store.
        """
        try:
            token = self._maybe_token()
            if not token:
                return
            # Fail closed if the posting account cannot be identified.
            own = self._api("GitHub", "GET", f"{self.base}/user", token=token)
            if not own.get("login"):
                raise _Problem("GitHub did not identify the posting account.")
            cursor = self._get("comments", {})
            # Start each scan a little before this machine's "now": its clock and
            # GitHub's differ, and a repeated comment is dropped by its id anyway.
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock() - _GITHUB_OVERLAP_S))
            if not cursor:
                self._set("comments", {"since": now, "page": 1})
                return
            start = cursor.get("start") or now
            params = urllib.parse.urlencode({"since": cursor["since"], "sort": "created",
                                            "direction": "asc", "per_page": 100,
                                            "page": cursor.get("page", 1)})
            rows = self._api("GitHub", "GET",
                             f"{self.base}/repos/{self.repo}/issues/comments?{params}", token=token)
            if not isinstance(rows, list):
                raise _Problem("GitHub sent comments in an unexpected shape.")
            for row in rows:
                user = row.get("user") or {}
                login = str(user.get("login") or "")
                if (not login or user.get("type") != "User" or login.lower().endswith("[bot]")
                        or login.lower() == str(own.get("login") or "").lower()):
                    continue
                prefix = f"{self.base}/repos/{self.repo}/issues/"
                issue = str(row.get("issue_url") or "")
                number = issue.removeprefix(prefix)
                if not issue.startswith(prefix) or not number.isdigit() or not row.get("id"):
                    continue
                deliver(_store.Inbound(channel=f"github:{self.id}",
                        external_id=str(row["id"]), sender=login, text=str(row.get("body") or ""),
                        thread=f"github:{self.id}:{self.repo}:{number}", raw={"author_id": user.get("id")}))
            self._set("comments", {"since": cursor["since"], "start": start,
                                    "page": cursor.get("page", 1) + 1} if len(rows) == 100 else
                                   {"since": start, "page": 1})
        except _Problem as exc:
            self.last_error = self._safe(exc)
        except Exception as exc:
            self.last_error = self._safe(f"Comment check failed ({type(exc).__name__}).")


# -- registry ---------------------------------------------------------------

_TYPES = {cls.type: cls for cls in (GitHubSource, SentrySource, PostHogSource, HttpSource,
                                    CommandSource)}


def build(cfg, **kw):
    """The source for one config entry. ValueError names what is wrong."""
    if not isinstance(cfg, dict):
        raise ValueError("A source must be an object.")
    kind = str(cfg.get("type") or "").strip()
    if kind == "ci":
        from .ci_source import CISource
        return CISource(cfg, **kw)
    if kind not in _TYPES:
        raise ValueError(f"Unknown source type: {kind or '(none)'}. Use one of: {', '.join(_store.SOURCE_TYPES)}.")
    return _TYPES[kind](cfg, **kw)


def _field(name, label, placeholder="", *, secret=False, optional=False):
    return {"name": name, "label": label, "secret": secret, "optional": optional,
            "placeholder": placeholder}


_FIELDS = {
    "ci": [
        _field("repo", "Repository", "example-org/storefront"),
        _field("token_vault", "Token", secret=True, optional=True),
        _field("base", "API address", "https://api.github.com", optional=True),
        _field("branches", "Branches (comma separated)", optional=True),
        _field("every_s", "Seconds between checks", "300", optional=True),
    ],
    "github": [
        _field("repo", "Repository", "example-org/storefront"),
        _field("token_vault", "Token", "", secret=True, optional=True),
        _field("label", "Only this label", "bug", optional=True),
        _field("base", "Server address", "https://api.github.com", optional=True),
        _field("every_s", "Seconds between checks", "300", optional=True),
        _field("backfill", "File existing issues", "no", optional=True),
    ],
    "sentry": [
        _field("org", "Organization", "example-org"),
        _field("project", "Project", "storefront"),
        _field("token_vault", "Token", "", secret=True),
        _field("environment", "Environment", "production", optional=True),
        _field("min_events", "Minimum events", "1", optional=True),
        _field("base", "Server address", "https://sentry.io", optional=True),
        _field("every_s", "Seconds between checks", "300", optional=True),
        _field("backfill", "File existing issues", "no", optional=True),
    ],
    "posthog": [
        _field("project_id", "Project ID", "12345"),
        _field("token_vault", "Personal API key", "", secret=True),
        _field("host", "Server address", "https://us.posthog.com", optional=True),
        _field("min_events", "Minimum events", "3", optional=True),
        _field("window_min", "Minutes to count", "60", optional=True),
        _field("every_s", "Seconds between checks", "600", optional=True),
        _field("backfill", "File existing errors", "no", optional=True),
    ],
    "http": [
        _field("url", "Address", "https://example.com/health"),
        _field("expect_status", "Expected status", "200", optional=True),
        _field("contains", "Expected text", "ok", optional=True),
        _field("fail_after", "Failures before filing", "2", optional=True),
        _field("timeout_s", "Timeout in seconds", "10", optional=True),
        _field("every_s", "Seconds between checks", "300", optional=True),
    ],
    "command": [
        _field("cmd", "Command", "./scripts/check.sh"),
        _field("title", "Ticket title", "Nightly check failed", optional=True),
        _field("cwd", "Folder", "~/projects/example", optional=True),
        _field("fail_after", "Failures before filing", "1", optional=True),
        _field("timeout_s", "Timeout in seconds", "300", optional=True),
        _field("every_s", "Seconds between checks", "3600", optional=True),
    ],
}


def required_fields(source_type):
    """The fields a setup form asks for, in order. Optional ones come last."""
    kind = str(source_type or "").strip()
    if kind not in _FIELDS:
        raise ValueError(f"Unknown source type: {kind or '(none)'}.")
    return [dict(f) for f in _FIELDS[kind]]
