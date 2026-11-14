"""The On-call home screen: its data, its answers, and who may reach it."""

import pytest

from ccc_server.oncall import api
from ccc_server.oncall import engine as engine_mod
from ccc_server.oncall import store as st
from tests.test_oncall_http import _call, port  # noqa: F401  (fixture)

NOW = 1_800_000_000.0


class WT:
    def __init__(self):
        self.answers = []

    def available(self):
        return True

    def answer(self, ref, text):
        self.answers.append((ref, text))


class Deps:
    def __init__(self):
        self.injected, self.approved = [], []

    def inject(self, session_id, text):
        self.injected.append((session_id, text))
        return True, ""

    def approve(self, session_id, choice, meta):
        self.approved.append((session_id, choice))
        return True, ""


@pytest.fixture
def eng(tmp_path):
    wt, deps = WT(), Deps()
    e = engine_mod.Engine(tmp_path / "oncall", wt=wt, deps=deps, clock=lambda: NOW, flag_on=lambda: True)
    cfg = e.config()
    cfg.update(enabled=True, queue="SHOP", quiet_hours={"start": "23:00", "end": "07:00", "tz": "UTC"},
               sources=[{"id": "gh", "type": "github", "enabled": True, "repo": "example/shop"},
                        {"id": "build", "type": "ci", "enabled": False}])
    st.save_config(e.root, cfg)
    e.fake_wt, e.fake_deps = wt, deps
    return e


def _get(eng):
    code, res = api.handle("GET", "/api/oncall/home", eng=eng)
    assert code == 200, res
    return res


def _answer(eng, alert_id, text):
    return api.handle("POST", "/api/oncall/answer", {"id": alert_id, "text": text}, eng=eng)


def test_off_says_only_that_it_is_off(tmp_path):
    eng = engine_mod.Engine(tmp_path / "oncall", wt=WT(), flag_on=lambda: True)
    assert _get(eng) == {"ok": True, "enabled": False}


def test_the_three_lists_read_like_the_phone_without_numbers_or_instructions(eng):
    store = eng.store
    report, _ = store.report_upsert("manual", "a", title="Export button does nothing", text="x", now=NOW - 1300)
    store.report_update(report["id"], state="filed", ticket_ref="SHOP-1")
    issue, _ = store.report_upsert("source:gh", "b", title="Login page blank", text="x", now=NOW - 5000)
    store.report_update(issue["id"], state="closed", ticket_ref="SHOP-2")
    mail, _ = store.report_upsert("mail", "c", title="Invoice off by a cent", text="x",
                                  reporter_name="Dana", reporter_address="dana@example.com", now=NOW - 60)
    store.report_update(mail["id"], state="filed", ticket_ref="SHOP-3")
    store.alert_upsert("q1", kind="plain_question", title="Export fix", session_id="s1",
                       body="The old export button is unused. Remove it, or keep it?",
                       meta={"choices": [["Remove it", "Remove the button."], ["Keep it", "Keep the button."]]},
                       now=NOW - 600)
    store.alert_upsert("d1", kind="decision", title="Rename the column?", body="It breaks old exports.",
                       ref="SHOP-1", meta={"recommend": "Keep the name"}, now=NOW - 500)
    store.alert_upsert("p1", kind="permission", title="Export fix", body="Run: npm test", session_id="s1", now=NOW - 400)
    store.alert_upsert("f1", kind="fixed", title="Login page blank", body="The page loads again.\n\nDetail.",
                       ref="SHOP-2", needs_reply=False, now=NOW - 3000)
    store.alert_upsert("f0", kind="fixed", title="Old one", body="", ref="SHOP-0", needs_reply=False,
                       now=NOW - 8 * 86400)

    res = _get(eng)
    assert res["enabled"] is True and res["paused"] is False
    q, d, p = res["needs_you"]
    assert q["headline"] == "Question from Export fix"
    assert q["detail"].startswith("The old export button is unused. Remove it, or keep it?")
    assert "Accepted replies:" in q["detail"]
    assert "option number" in q["reply_help"]
    assert q["choices"] == [{"label": "Remove it", "reply": "Remove the button."},
                            {"label": "Keep it", "reply": "Keep the button."}]
    assert q["free_text"] is True
    assert d["headline"] == "SHOP-1 — Rename the column? needs a decision" and "Agent recommends: Keep the name" in d["detail"]
    assert d["choices"] == [] and d["free_text"] is True
    # A permission prompt is shown but only its session can answer it.
    assert p["session_only"] is True and p["choices"] == [] and p["session_id"] == "s1"
    for row in res["needs_you"]:
        assert "#" not in row["headline"] and "Reply " not in row["detail"]

    assert [(w["title"], w["origin"], w["waiting_on_you"]) for w in res["working"]] == [
        ("Invoice off by a cent", "Reported by Dana", False),
        ("Export button does nothing", "Reported by you", True)]
    assert res["working_count"] == 2
    # Only this week's fixes, each with how long it took from the report.
    assert res["fixed_count"] == 1
    assert res["fixed"] == [{"title": "Login page blank", "ref": "SHOP-2", "at": NOW - 3000,
                             "took": 2000.0, "code_change": True}]
    assert res["sources"] == ["github"] and res["quiet"] == {"start": "23:00", "end": "07:00"}


def test_a_fixed_notice_with_nothing_to_decide_is_not_listed_as_needing_you(eng):
    eng.store.alert_upsert("f1", kind="fixed", title="Done", body="", ref="SHOP-2", now=NOW)
    eng.store.alert_upsert("f2", kind="fixed", title="Done", body="", ref="SHOP-3",
                           meta={"notify": True, "name": "Dana"}, now=NOW)
    res = _get(eng)
    assert [n["choices"] for n in res["needs_you"]] == [
        [{"label": "Tell them", "reply": "yes"}, {"label": "Skip", "reply": "no"}]]


def test_home_reads_each_secret_once_and_refreshes_it_for_the_next_response(eng, monkeypatch):
    from ccc_server import vault

    reads = []
    current = ["synthetic-private-first"]
    monkeypatch.setattr(vault, "vault_list_entries", lambda: [{"name": "fixture"}])

    def read(name):
        reads.append(name)
        return current[0]

    monkeypatch.setattr(vault, "vault_get_value", read)
    for i in range(3):
        eng.store.alert_upsert(str(i), kind="plain_question", title=current[0],
                               body=current[0], session_id="s1",
                               meta={"choices": [["Safe", "continue"], ["Private", current[0]]]},
                               now=NOW)
    response = _get(eng)
    assert reads == ["fixture"]
    assert current[0] not in str(response)
    assert all(row["choices"] == [{"label": "Safe", "reply": "continue"}]
               for row in response["needs_you"])

    current[0] = "synthetic-private-rotated"
    eng.store.alert_upsert("rotated", kind="question", title=current[0], body=current[0], now=NOW)
    response = _get(eng)
    assert reads == ["fixture", "fixture"]
    assert current[0] not in str(response)

    current[0] = None
    code, response = api.handle("GET", "/api/oncall/home", eng=eng)
    assert code == 400
    assert "Cannot check outgoing content" in response["error"]


def test_an_answer_reaches_the_session_and_closes_the_alert_once(eng):
    alert, _ = eng.store.alert_upsert("q1", kind="plain_question", title="Export fix", session_id="s1",
                                      body="Remove it, or keep it?", now=NOW)
    code, res = _answer(eng, alert["id"], "Remove the button.")
    assert code == 200 and res["note"] == "sent to the session", res
    assert eng.fake_deps.injected == [("s1", "Remove the button.")]
    assert eng.store.alert(alert["id"])["state"] == "answered"
    assert _get(eng)["needs_you"] == []
    code, res = _answer(eng, alert["id"], "Keep it.")
    assert code == 400 and res["error"] == "That one was already handled."
    assert len(eng.fake_deps.injected) == 1


def test_a_decision_answer_goes_to_the_ticket(eng):
    alert, _ = eng.store.alert_upsert("d1", kind="decision", title="Rename?", body="", ref="SHOP-1", now=NOW)
    assert _answer(eng, alert["id"], "Keep the name")[0] == 200
    assert eng.fake_wt.answers == [("SHOP-1", "Keep the name")]


def test_a_permission_prompt_cannot_be_answered_from_the_home_screen(eng):
    alert, _ = eng.store.alert_upsert("p1", kind="permission", title="Export fix", body="Run: npm test",
                                      session_id="s1", now=NOW)
    code, res = _answer(eng, alert["id"], "allow")
    assert code == 400 and res["error"] == "Answer a permission prompt in its session."
    assert eng.fake_deps.approved == [] and eng.store.alert(alert["id"])["state"] == "open"


def test_an_answer_that_is_not_understood_can_be_given_again(eng):
    report, _ = eng.store.report_upsert("mail", "c", title="Spam?", text="x", now=NOW)
    alert, _ = eng.store.alert_upsert("r1", kind="report", title="Spam?", body="x",
                                      meta={"report_id": report["id"]}, now=NOW)
    code, res = _answer(eng, alert["id"], "hmm")
    assert code == 400 and res["error"].startswith("Reply go or no")
    code, res = _answer(eng, alert["id"], "no")
    assert code == 200 and res["note"] == "dropped the report", res


def test_bad_requests_get_a_sentence(eng):
    assert _answer(eng, "x", "yes") == (400, {"ok": False, "error": "Say which alert this answers."})
    assert _answer(eng, 1, " ") == (400, {"ok": False, "error": "The answer is empty."})
    assert _answer(eng, 99, "yes") == (400, {"ok": False, "error": "That alert was not found."})


# -- who may reach it ---------------------------------------------------------

REMOTE = {"X-Forwarded-For": "100.64.0.9", "Host": "box.example.ts.net"}


def test_the_home_screen_follows_the_dashboard_boundary_and_setup_stays_local(port, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CCC_FF_ONCALL", "1")
    # A trusted remote view of the dashboard may read the home screen and answer.
    assert _call(port, "GET", "/api/oncall/home", headers=REMOTE) == (200, {"ok": True, "enabled": False})
    code, res = _call(port, "POST", "/api/oncall/answer", {"id": 1, "text": "yes"}, headers=REMOTE)
    assert code == 400 and res["error"] == "On-call is off."
    # Setup, channels and secrets are still refused from off the machine.
    for method, path in (("GET", "/api/oncall/status"), ("POST", "/api/oncall/config"),
                         ("POST", "/api/oncall/channel"), ("POST", "/api/oncall/report")):
        code, res = _call(port, method, path, {} if method == "POST" else None, headers=REMOTE)
        assert code == 403 and res["error"] == "On-call settings are local-only", (method, path)
    # Another site's page is refused on both.
    foreign = {"Origin": "https://attacker.invalid"}
    assert _call(port, "GET", "/api/oncall/home", headers=foreign)[0] == 403
    assert _call(port, "POST", "/api/oncall/answer", {"id": 1, "text": "yes"}, headers=foreign)[0] == 403


def test_the_home_screen_is_404_until_the_preview_flag_is_on(port):  # noqa: F811
    assert _call(port, "GET", "/api/oncall/home")[0] == 404
    assert _call(port, "POST", "/api/oncall/answer", {"id": 1, "text": "yes"})[0] == 404
