"""Pop-up approvals: server and browser lists agree, every pop-up is gated."""

import re
from pathlib import Path

from ccc_server import popups

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "popups.js").read_text()


def _js_list(name):
    m = re.search(r"var %s = \[(.*?)\];" % name, JS, re.S)
    assert m, name
    return sorted(re.findall(r"'([a-z-]+)'", m.group(1)))


def test_lists_match_between_server_and_browser():
    assert _js_list("ALL") == sorted(popups.ALL)
    assert _js_list("APPROVED") == sorted(popups.APPROVED)
    assert popups.APPROVED <= set(popups.ALL)


def test_every_popup_has_a_gate():
    gates = {
        "moment-zero": "static/onboarding/onboarding.js",
        "savings-milestone": "static/savings.js",
        "notify-permission": "static/notify.js",
        "star-ask": "static/star-ask.js",
        "router-detected": "static/router-detected.js",
        "limit-failover": "static/limit-failover.js",
        "fleet-limit": "static/fleet-failover.js",
        "leftover-offer": "static/leftover.js",
        "leftover-notification": "static/leftover.js",
    }
    assert set(gates) | {"notify-task", "notify-digest", "notify-milestone",
                         "notify-other"} == set(popups.ALL)
    for popup_id, path in gates.items():
        src = (ROOT / path).read_text()
        assert re.search(r"cccPopups\.allowed\(['\"]%s['\"]\)" % popup_id, src), path
    assert "cccPopups.notifyAllowed(kind)" in (ROOT / "static/notify.js").read_text()


def test_gate_loads_before_the_popups():
    html = (ROOT / "static" / "index.html").read_text()
    first = html.index('src="/static/popups.js"')
    for name in ("limit-failover", "fleet-failover", "onboarding/onboarding", "savings", "notify",
                 "router-detected", "star-ask", "leftover"):
        assert first < html.index('src="/static/%s.js"' % name), name


def test_notify_kinds_map_to_popups():
    assert popups.notify_kind_id("task") == "notify-task"
    assert popups.notify_kind_id("needs_input") == "notify-task"
    assert popups.notify_kind_id("digest") == "notify-digest"
    assert popups.notify_kind_id("milestone") == "notify-milestone"
    assert popups.notify_kind_id("info") == "notify-other"
    assert popups.notify_kind_id("leftover") == "leftover-notification"
    assert "leftover: 'leftover-notification'" in JS
    assert popups.notify_allowed("leftover") is False
