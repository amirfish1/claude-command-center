"""Browser-encoded native subagent IDs reach the reader and stream intact."""

import json
import threading
import urllib.error
import urllib.request

import pytest

import server


@pytest.fixture
def conversation_routes(monkeypatch):
    calls = []

    def forward(conv_id, query, accept):
        calls.append(("open", conv_id, query))
        return {"body": json.dumps({"id": conv_id}).encode()}

    class Handler(server.CommandCenterHandler):
        def _check_proxy_secret(self):
            return True

        def _phone_pin_blocked(self, path):
            return False

        def _stream_conversation(self, conv_id, after):
            calls.append(("stream", conv_id, after))
            self.send_json({"id": conv_id})

    monkeypatch.setattr(server._conv_reader, "forward", forward)
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def get(suffix):
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/conversations/" + suffix
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        yield get, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("separator", [":", "%3A", "%3a"])
def test_native_subagent_open_and_stream_use_same_decoded_id(conversation_routes, separator):
    get, calls = conversation_routes
    native_id = "12345678-abcd:agent-a1"
    encoded = "12345678-abcd" + separator + "agent-a1"
    assert get(encoded + "?tail=50") == (200, {"id": native_id})
    assert get(encoded + "/stream?after=3") == (200, {"id": native_id})
    assert calls == [("open", native_id, "tail=50"), ("stream", native_id, 3)]


@pytest.mark.parametrize("encoded", ["%2f", "%5C", "%00"])
def test_decoding_cannot_introduce_path_separators(conversation_routes, encoded):
    get, calls = conversation_routes
    assert get("parent" + encoded + "agent")[0] == 400
    assert get("parent" + encoded + "agent/stream")[0] == 400
    assert calls == []
