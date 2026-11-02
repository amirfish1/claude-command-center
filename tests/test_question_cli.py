"""CLI contract checks against isolated stdlib HTTP and the real validator."""
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest

from ccc_server.decision_sessions import SessionQuestions, event_payload, session_payload


def uid():
    return str(uuid4())


@pytest.fixture
def cli():
    loader = importlib.machinery.SourceFileLoader("question_cli_test", str(Path(__file__).resolve().parents[1] / "ccc"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def api(tmp_path):
    calls = []
    state = {"failure": None, "remote_answers": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_POST(self):
            event = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, event))
            if state["failure"]:
                return self.reply({"ok": False, "error": "PRIVATE BODY"}, state["failure"])
            assert self.path == "/api/decision-inbox/session-events"
            self.reply(*event_payload(event, state_root=tmp_path))

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            calls.append(("GET", self.path, None))
            if state["failure"]:
                return self.reply({"ok": False, "error": "PRIVATE BODY"}, state["failure"])
            parsed = urlsplit(self.path)
            assert parsed.path == "/api/decision-inbox/session-questions"
            result, status = session_payload(parse_qs(parsed.query)["session_id"][0], state_root=tmp_path)
            result["remote_answers"] = state["remote_answers"]
            self.reply(result, status)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run(cli, api, capsys, *args):
    code = cli.main(["--server", api[0], "question", *args])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("stdin", [False, True])
def test_event_exact_json_and_replay(cli, api, tmp_path, monkeypatch, capsys, stdin):
    event = dict(event_id=uid(), operation="create", session_id=uid(), question_id=uid(),
                 generation=uid(), title="  Title  ", body="  line\r\n\tsecond\n\n", aliases=["[Q42:choice]"])
    text = json.dumps(event)
    filename = tmp_path / "event.json"
    filename.write_text(text)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))
    args = ("event", "--json-file", "-" if stdin else str(filename))
    code, result = run(cli, api, capsys, *args)
    assert code == 0
    assert api[1] == [("POST", "/api/decision-inbox/session-events", event)]
    assert result["event_id"] == event["event_id"]
    assert result["request"]["content"]["body"] == event["body"]
    assert result["request"]["revision"] == 1
    if stdin:
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))
    assert run(cli, api, capsys, *args)[1] == result


@pytest.mark.parametrize("stdin", [False, True])
def test_create_is_local_aliases_and_exact_body(cli, api, tmp_path, capsys, monkeypatch, stdin):
    body = "  first\r\nsecond\rthird\n\t  "
    filename = tmp_path / "body.txt"
    filename.write_bytes(body.encode())
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(body))
    code, result = run(cli, api, capsys, "create", "--session-id", uid(), "--title", " Title ",
                       "--body-file", "-" if stdin else str(filename),
                       "--alias", "[Q42:choice]", "--alias", "[Q43:other]")
    assert code == 0
    event = api[1][0][2]
    assert SessionQuestions._validate(event) == "create"
    for field in ("event_id", "question_id", "generation"):
        assert str(UUID(event[field])) == event[field]
    assert len({event[field] for field in ("event_id", "question_id", "generation")}) == 3
    assert event["body"] == body
    assert event["title"] == " Title "
    assert event["aliases"] == ["[Q42:choice]", "[Q43:other]"]
    assert result["request"]["content"]["visibility"] == "local"
    assert result["input_sent"] is False
    assert len(api[1]) == 1


def test_promote_plumbing(cli, api, tmp_path, capsys):
    filename = tmp_path / "body"
    filename.write_text("body")
    sid = uid()
    _, created = run(cli, api, capsys, "create", "--session-id", sid, "--title", "title", "--body-file", str(filename))
    request = created["request"]
    code, promoted = run(cli, api, capsys, "promote", "--session-id", sid,
                         "--request-id", request["request_id"], "--revision", "1", "--reason", "  need owner\n")
    assert code == 0
    event = api[1][-1][2]
    assert event == dict(event_id=promoted["event_id"], operation="promote", session_id=sid,
                         request_id=request["request_id"], expected_revision=1, reason="  need owner\n")
    assert SessionQuestions._validate(event) == "promote"
    assert promoted["request"]["revision"] == 2
    assert promoted["request"]["content"]["visibility"] == "global"
    assert run(cli, api, capsys, "promote", "--session-id", sid, "--request-id", request["request_id"],
               "--revision", "1", "--reason", "stale")[0] == 1


def test_consume_exact_plumbing(cli, monkeypatch, capsys):
    ids = {field: uid() for field in ("session_id", "answer_id", "request_id", "question_id", "generation")}
    calls = []

    def post(base, endpoint, payload):
        calls.append((base, endpoint, payload))
        assert SessionQuestions._validate(payload) == "consume"
        return 200, {"ok": True, "consumption": {"answer_id": payload["answer_id"], "consumed": True}}

    monkeypatch.setattr(cli, "_post_json", post)
    args = ["--server", "http://example.invalid", "question", "consume"]
    for field, value in ids.items():
        args += ["--" + field.replace("_", "-"), value]
    args += ["--source-message-id", " actual-transcript-id\n"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [("http://example.invalid", "/api/decision-inbox/session-events",
                      dict(ids, event_id=result["event_id"], operation="consume", source_message_id=" actual-transcript-id\n"))]


def test_inbox_read_only_and_preserves_remote_answers(cli, api, capsys):
    sid = uid()
    remote = dict(receipt={"answer_id": uid(), "status": "accepted"}, answer={"text": "  actual answer\n"},
                  source={"session_id": sid}, consumption=None, notification={"notification": "unknown"})
    api[2]["remote_answers"] = [remote]
    code, result = run(cli, api, capsys, "inbox", "--session-id", sid)
    assert code == 0
    assert result["remote_answers"] == [remote]
    assert api[1] == [("GET", "/api/decision-inbox/session-questions?session_id=" + sid, None)]


@pytest.mark.parametrize("operation", ["inbox", "event"])
@pytest.mark.parametrize("status", [400, 409, 503])
def test_http_failures_do_not_echo_private_body(cli, api, capsys, monkeypatch, operation, status):
    api[2]["failure"] = status
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO('{"body":"PRIVATE BODY"}'))
    args = ("inbox", "--session-id", uid()) if operation == "inbox" else ("event", "--json-file", "-")
    code, result = run(cli, api, capsys, *args)
    assert code == 1
    assert result["http_status"] == status
    assert "PRIVATE BODY" not in json.dumps(result)


@pytest.mark.parametrize("text", ['{"PRIVATE BODY":', '[]'])
def test_bad_json_is_private_and_does_not_post(cli, api, capsys, monkeypatch, text):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))
    code, result = run(cli, api, capsys, "event", "--json-file", "-")
    assert code == 1
    assert "PRIVATE BODY" not in json.dumps(result)
    assert api[1] == []


def test_missing_file_and_discovery_failures(cli, api, tmp_path, capsys, monkeypatch):
    assert run(cli, api, capsys, "event", "--json-file", str(tmp_path / "missing"))[0] == 1
    monkeypatch.setattr(cli, "_resolve_server", lambda value: None)
    assert run(cli, api, capsys, "inbox", "--session-id", uid())[0] == 1
    assert api[1] == []


@pytest.mark.parametrize("extra", [["--alias", "Q42"], ["--alias", "[Q42:Upper]"]])
def test_invalid_alias_rejected(cli, api, extra):
    with pytest.raises(SystemExit) as error:
        cli.main(["--server", api[0], "question", "create", "--session-id", uid(), "--title", "title",
                  "--body-file", "unused", *extra])
    assert error.value.code == 2
    assert api[1] == []


@pytest.mark.parametrize("operation,fields", [
    ("edit", {"title": " revised ", "body": " exact\r\nbody\n"}),
    ("withdraw", {"reason": " withdrawn\n"}),
    ("answer_local", {"text": " answer\n", "source_message_id": "transcript:actual"}),
])
def test_local_lifecycle_via_exact_event(cli, api, tmp_path, capsys, monkeypatch, operation, fields):
    filename = tmp_path / "body"
    filename.write_text("body")
    sid = uid()
    _, result = run(cli, api, capsys, "create", "--session-id", sid, "--title", "title", "--body-file", str(filename))
    assert result["request"]["content"]["aliases"] == []
    event = dict(event_id=uid(), operation=operation, session_id=sid,
                 request_id=result["request"]["request_id"], expected_revision=1, **fields)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    code, result = run(cli, api, capsys, "event", "--json-file", "-")
    assert code == 0
    assert api[1][-1][2] == event
    assert result["request"]["revision"] == 2
    assert result["input_sent"] is False


def test_invalid_event_rejected_by_real_validator(cli, api, capsys, monkeypatch):
    event = dict(event_id=uid(), session_id=uid(), operation="create", question_id=uid(),
                 generation=uid(), title="title", body="PRIVATE BODY", visibility="global")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(event)))
    code, result = run(cli, api, capsys, "event", "--json-file", "-")
    assert code == 1
    assert result["http_status"] == 400
    assert "PRIVATE BODY" not in json.dumps(result)


@pytest.mark.parametrize("response", [{"ok": False, "error": "PRIVATE BODY"}, [], {"unexpected": True}])
def test_unsuccessful_or_invalid_success_envelope(cli, monkeypatch, capsys, response):
    monkeypatch.setattr(cli, "_get_json", lambda *args: response)
    assert cli.main(["--server", "http://example.invalid", "question", "inbox", "--session-id", uid()]) == 1
    assert "PRIVATE BODY" not in capsys.readouterr().out


def test_transport_failure(cli, monkeypatch, capsys):
    import urllib.error

    def unavailable(*args):
        raise urllib.error.URLError("PRIVATE BODY")

    monkeypatch.setattr(cli, "_get_json", unavailable)
    assert cli.main(["--server", "http://example.invalid", "question", "inbox", "--session-id", uid()]) == 1
    assert "PRIVATE BODY" not in capsys.readouterr().out


def test_stdin_binary_preserves_crlf(cli, api, capsys, monkeypatch):
    body = "  first\r\nsecond\rthird\n"
    stream = io.TextIOWrapper(io.BytesIO(body.encode("utf-8")), encoding="utf-8", newline=None)
    monkeypatch.setattr(cli.sys, "stdin", stream)
    code, result = run(cli, api, capsys, "create", "--session-id", uid(), "--title", "title", "--body-file", "-")
    assert code == 0
    assert api[1][-1][2]["body"] == body
    assert result["request"]["content"]["body"] == body
