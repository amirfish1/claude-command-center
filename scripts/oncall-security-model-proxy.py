import json, http.client, os, hmac
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler


class H(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if not hmac.compare_digest(
            self.headers.get('Authorization', ''),
            'Bearer ' + os.environ['PROOF_PROXY_KEY'],
        ):
            self.send_error(403)
            return
        n = int(self.headers.get('Content-Length', '0'))
        if n < 1 or n > 10000000:
            self.send_error(400)
            return
        body = self.rfile.read(n)
        try:
            data = json.loads(body)
        except Exception:
            self.send_error(400)
            return
        if self.path != '/v1/responses' or data.get('model') != 'gpt-6.1-terra':
            self.send_error(403)
            return
        auth = json.loads(Path('/tmp/auth.json').read_text())['tokens']
        headers = {
            'Authorization': 'Bearer ' + auth['access_token'],
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'chatgpt-account-id': auth['account_id'],
            'originator': 'codex_cli_rs',
            'User-Agent': 'codex_cli_rs',
        }
        try:
            c = http.client.HTTPSConnection('chatgpt.com', timeout=180)
            c.request('POST', '/backend-api/codex/responses', body, headers)
            r = c.getresponse()
            print('model response', r.status, flush=True)
            self.send_response(r.status)
            if r.getheader('Content-Type'):
                self.send_header('Content-Type', r.getheader('Content-Type'))
            self.end_headers()
            while True:
                b = r.read1(16384)
                if not b:
                    break
                self.wfile.write(b)
                self.wfile.flush()
            c.close()
        except Exception as e:
            print('proxy error', type(e).__name__, str(e), flush=True)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8780), H).serve_forever()
