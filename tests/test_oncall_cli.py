"""`ccc oncall`: the commands a stranger types, run against the real API code.

The CLI's get/post are bound straight to ``api.handle`` (no server process),
with the preview flag behaving as it does over HTTP: off means 404.
"""

import io as _io
import urllib.error

import pytest

from ccc_server.oncall import api, cli
from ccc_server.oncall import engine as engine_mod
from tests.test_oncall_api import FakeWT, Vault


class Box:
    """A CCC that answers /api/oncall/* and the preview-flag switch."""

    def __init__(self, root, monkeypatch):
        self.flag = False
        self.wt = FakeWT()
        self.vault = Vault()
        monkeypatch.setitem(__import__("sys").modules, "ccc_server.vault", self.vault)
        import ccc_server
        monkeypatch.setattr(ccc_server, "vault", self.vault, raising=False)
        self.eng = engine_mod.Engine(root, wt=self.wt, flag_on=lambda: self.flag)
        self.lines, self.asked, self.answers, self.secrets = [], [], [], []

    def get(self, base, path, timeout=10):
        if not self.flag:
            raise urllib.error.HTTPError(base + path, 404, "Not Found", {}, _io.BytesIO(b"{}"))
        code, res = api.handle("GET", path, eng=self.eng)
        if code != 200:
            raise urllib.error.HTTPError(base + path, code, "x", {}, _io.BytesIO(b"{}"))
        return res

    def post(self, base, path, payload, timeout=60):
        if path == "/api/features/flag":
            self.flag = bool(payload.get("on"))
            return 200, {"ok": True}
        if not self.flag:
            return 404, {"ok": False, "error": "On-call is a preview and it is off. Turn it on with: ccc oncall setup"}
        return api.handle("POST", path, payload, eng=self.eng)

    def run(self, *argv, answers=(), secrets=(), interactive=False, stdin="", base="http://127.0.0.1:1"):
        self.answers, self.secrets = list(answers), list(secrets)

        def ask(prompt):
            self.asked.append(prompt)
            return self.answers.pop(0) if self.answers else ""

        def secret(prompt):
            self.asked.append(prompt)
            return self.secrets.pop(0) if self.secrets else ""

        io = cli.IO(ask=ask, secret=secret, out=lambda line="": self.lines.append(str(line)),
                    stdin=_io.StringIO(stdin), interactive=interactive)
        return cli.run(list(argv), base=base, get=self.get, post=self.post, io=io)

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def box(tmp_path, monkeypatch):
    return Box(tmp_path / "oncall", monkeypatch)


def test_every_command_says_how_to_turn_the_preview_on(box, capsys):
    for argv in (["status"], ["test"], ["on"], ["report", "x"], ["channel", "list"], ["decisions"]):
        assert box.run(*argv) == 1
        assert "ccc oncall setup" in capsys.readouterr().err


def test_no_running_ccc_is_one_clear_line(box, capsys):
    assert box.run("status", base=None) == 1
    assert capsys.readouterr().err.strip() == "ccc oncall: CCC is not running. Start it with `ccc`, then run this again."


def test_setup_with_flags_needs_no_prompts(box, tmp_path):
    sent = tmp_path / "sent.txt"
    code = box.run("setup", "--yes", "--queue", "shop", "--repo", str(tmp_path), "--channel", "command",
                   "--set", f"send=sh -c 'cat >> {sent}'", "--set", "owner=me@example.com")
    assert code == 0, box.text
    assert box.flag is True and box.asked == []
    assert box.eng.active()
    assert box.wt.configured == [("SHOP", str(tmp_path))]
    assert sent.read_text().startswith("This is a test from CCC On-call.")
    assert "On-call is on." in box.text
    assert f"Bug tickets go to the queue SHOP, worked on in {tmp_path}." in box.text


def test_interactive_setup_asks_only_what_it_needs(box, tmp_path, monkeypatch):
    repo = tmp_path / "storefront"
    repo.mkdir()
    monkeypatch.chdir(repo)
    sent = tmp_path / "sent.txt"
    # Accept the preview and folder, then pick "your own command".
    code = box.run("setup", interactive=True,
                   answers=["y", "", "7", f"sh -c 'cat >> {sent}'"])
    assert code == 0, box.text
    assert box.eng.config()["queue"] == "STOREFRONT"
    assert box.eng.config()["repo"] == str(repo)
    assert [a for a in box.asked if "queue" in a] == []
    assert sent.exists()


def test_setup_stays_off_when_the_test_message_fails(box, tmp_path, capsys):
    code = box.run("setup", "--yes", "--queue", "shop", "--repo", str(tmp_path), "--channel", "command",
                   "--set", "send=false")
    assert code == 1
    assert "On-call is off" in capsys.readouterr().err
    assert not box.eng.active()


def test_a_secret_is_never_an_argument(box, capsys):
    box.flag = True
    assert box.run("channel", "add", "telegram", "--set", "token_vault=abc") == 1
    assert "no setting called token_vault" in capsys.readouterr().err
    assert box.run("channel", "add", "telegram") == 1
    assert "--secret-stdin token" in capsys.readouterr().err


def test_telegram_through_the_cli_with_a_typed_token(box, monkeypatch):
    from tests import test_oncall_api as shared
    import threading
    from http.server import ThreadingHTTPServer
    shared._Telegram.updates, shared._Telegram.sent = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), shared._Telegram)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        box.flag = True
        web = f"http://127.0.0.1:{server.server_address[1]}"

        def ask(prompt):
            # The person messages the bot, then presses Enter.
            shared._Telegram.updates = [{"update_id": 1, "message": {
                "message_id": 1, "text": "hi", "chat": {"id": 4242, "type": "private", "first_name": "Dana"}}}]
            return ""

        io = cli.IO(ask=ask, secret=lambda prompt: shared.TOKEN,
                    out=lambda line="": box.lines.append(str(line)), interactive=True)
        code = cli.run(["channel", "add", "telegram", "--set", f"api_base={web}"],
                       base="http://x", get=box.get, post=box.post, io=io)
        assert code == 0, box.text
        assert "Open your bot in Telegram, send it any message." in box.text
        assert "Added Telegram (4242)." in box.text
        assert box.vault.entries == {"oncall-channel-telegram-token": shared.TOKEN}
        assert shared.TOKEN not in box.text
    finally:
        server.shutdown()
        server.server_close()


def test_secret_from_stdin_and_from_an_existing_vault_entry(box):
    box.flag = True
    box.vault.entries["my-ntfy-token"] = "tk_test_XXXX"
    # ntfy needs the network for its check, so only the request shape is asserted here.
    seen = []
    real_post = box.post
    box.post = lambda base, path, payload, timeout=60: (seen.append((path, payload)) or (200, {"ok": True, "channel": payload["channel"]}))
    assert box.run("channel", "add", "ntfy", "--set", "topic=t1", "--vault", "token=my-ntfy-token") == 0
    assert seen[-1][1] == {"channel": {"type": "ntfy", "topic": "t1", "token_vault": "my-ntfy-token"}, "secrets": {}}
    assert box.run("channel", "add", "ntfy", "--set", "topic=t2", "--secret-stdin", "token", stdin="tk_test_YYYY\n") == 0
    assert seen[-1][1]["secrets"] == {"token_vault": "tk_test_YYYY"}
    box.post = real_post


def test_day_two_commands(box, tmp_path):
    sent = tmp_path / "sent.txt"
    assert box.run("setup", "--yes", "--queue", "shop", "--repo", str(tmp_path), "--channel", "command",
                   "--set", f"send=sh -c 'cat >> {sent}'", "--set", "owner=me@example.com") == 0
    box.lines.clear()

    assert box.run("report", "Checkout button does nothing on Safari") == 0
    assert box.lines[-1] == "Filed as SHOP-1. An agent will pick it up."

    assert box.run("customer", "add", "dana@example.com", "--name", "Dana") == 0
    assert box.run("customer", "list") == 0
    assert box.lines[-1] == "dana@example.com  Dana"

    assert box.run("minor", "on") == 0
    assert ("SHOP", True) in box.wt.policies
    assert box.run("decisions") == 0
    assert box.lines[-1] == "SHOP-3  applied  git revert abc1234"
    box.wt.find = lambda ref: {"history": [
        {"event": "block", "question": "Name it bindCheckout or attachCheckoutHandler?"},
        {"event": "auto_decision", "recommend": "bindCheckout", "undo": "git revert abc1234"}]}
    assert box.run("decisions") == 0
    assert box.lines[-4:] == ["SHOP-3  Name it bindCheckout or attachCheckoutHandler?",
                              "  Decided: bindCheckout", "  Undo: git revert abc1234", "  Applied."]

    box.lines.clear()
    assert box.run("status") == 0
    assert box.lines[0] == "On-call is on. Queue: SHOP."
    assert "Nothing is waiting on you." in box.text

    assert box.run("off") == 0 and not box.eng.active()
    assert box.run("on") == 0 and box.eng.active()
    assert box.run("channel", "rm", "command") == 0
    assert box.run("channel", "list") == 0 and box.lines[-1] == "No channel yet."


def test_ask_without_a_reporter_explains_itself(box, tmp_path, capsys):
    assert box.run("setup", "--yes", "--queue", "shop", "--repo", str(tmp_path), "--channel", "command",
                   "--set", "send=true") == 0
    box.run("report", "It is broken")
    assert box.run("ask", "SHOP-1", "Which browser?") == 1
    assert "no reporter" in capsys.readouterr().err


def test_failed_setup_turns_previously_enabled_oncall_off(box, tmp_path, capsys):
    assert box.run("setup", "--yes", "--queue", "shop", "--repo", str(tmp_path),
                   "--channel", "command", "--set", "send=true") == 0
    cfg = box.eng.config()
    cfg["channels"][0]["send"] = "false"
    box.eng.save_config(cfg)
    assert box.run("setup", "--yes") == 1
    assert not box.eng.active()
    assert "On-call is off" in capsys.readouterr().err


def test_ntfy_topic_is_generated_without_another_question():
    from ccc_server.oncall.channels import required_fields
    args = cli.build_parser().parse_args(['setup'])
    asked = []
    io = cli.IO(ask=lambda prompt: asked.append(prompt) or '', interactive=True)
    entry, secrets = cli._collect('channel', 'ntfy', required_fields('ntfy'), args, io)
    assert entry['topic'].startswith('ccc-oncall-')
    assert len(entry['topic']) == len('ccc-oncall-') + 24
    assert asked == [] and secrets == {}


def test_status_distinguishes_untested_send_only_and_missing_monitoring():
    lines = []
    cli._print_status({'channels': [{'id': 'command', 'enabled': True}], 'wt': True},
                      cli.IO(out=lines.append))
    assert 'not tested; send only' in '\n'.join(lines)
    assert 'No monitoring source configured.' in '\n'.join(lines)
