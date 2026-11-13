"""WhatsApp channel: message the owner's own self chat through the local bridge.

Config (all under "whatsapp" in app-channels.json):
    enabled   true to turn it on (off by default)
    dry_run   true (default) logs what would be sent and sends nothing
    chat_id   the owner's own chat JID, e.g. 15551234567@s.whatsapp.net
    bridge    bridge base URL, default http://127.0.0.1:3000

It only ever sends to the one configured `chat_id`; group (@g.us), broadcast
and newsletter chats are refused.
"""
import json
import urllib.request

NAME = "whatsapp"
DEFAULT_BRIDGE = "http://127.0.0.1:3000"
_ALLOWED_SUFFIXES = ("@s.whatsapp.net", "@lid")
MAX_LEN = 1500


def _chat_ok(chat_id):
    return isinstance(chat_id, str) and chat_id.endswith(_ALLOWED_SUFFIXES) \
        and chat_id.split("@")[0].isalnum()


def configured(cfg):
    return _chat_ok(cfg.get("chat_id"))


def format_message(title, body, url):
    text = f"*{title}*" + (f"\n{body}" if body else "") + (f"\n{url}" if url else "")
    return text[:MAX_LEN]


def send(cfg, title, body, url):
    chat_id = cfg.get("chat_id")
    if not _chat_ok(chat_id):
        return False, "chat_id must be the owner's own chat (no groups)"
    text = format_message(title, body, url)
    if cfg.get("dry_run", True) is not False:
        return True, None  # dry run: nothing leaves the machine
    base = str(cfg.get("bridge") or DEFAULT_BRIDGE).rstrip("/")
    req = urllib.request.Request(
        base + "/send", data=json.dumps({"chatId": chat_id, "message": text}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read() or b"{}")
    except Exception as e:
        return False, f"bridge: {e}"[:200]
    return (True, None) if data.get("success") else (False, str(data.get("error") or "send failed")[:200])
