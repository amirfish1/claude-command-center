"""Disposable HTTP fixture: production report routes, isolated evidence and queue."""
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server

release = threading.Event()
queued = {}
calls = []


class Queue:
    def enqueue(self, **kwargs):
        calls.append(kwargs['dedupe_key'])
        release.wait(15)
        return queued.setdefault(kwargs['dedupe_key'], {'ref': 'DECISIONS-FIX-TEST'})


class Handler(server.CommandCenterHandler):
    def _do_GET(self):
        if self.path == '/fixture':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<!doctype html><title>Report receipt fixture</title>')
        elif self.path == '/fixture/stats':
            self.send_json({'calls': len(calls), 'tickets': len(queued)})
        elif self.path == '/fixture/release':
            release.set()
            self.send_json({'ok': True})
        else:
            super()._do_GET()


with tempfile.TemporaryDirectory() as directory:
    server.COMMAND_CENTER_STATE_DIR = Path(directory)
    server._q = Queue()
    server._WT_WORKERS_AVAILABLE = False
    server.conversation_transcript_path = lambda sid: ''
    server.parse_conversation = lambda *a, **kw: {'events': []}
    server._detect_session_engine = lambda sid: 'codex'
    server.find_session_cwd = lambda sid: '/workspace/example'
    server.create_annotation = lambda payload: {'annotation': {'screenshot_warning': 'Fixture capture omitted'}}
    listener = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    print(json.dumps({'port': listener.server_port}), flush=True)
    listener.serve_forever()
