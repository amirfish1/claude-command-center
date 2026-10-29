"""ticket-prose.js lifts inline options out of a dense `wt block --question`.

Workers write options as "(a)", "(A)", "(1)" or "(A, recommended)"; all of
them must become a scannable list, not one wall of text (STRAMP-215).
"""
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROSE_JS = ROOT / "static" / "ticket-prose.js"


def _render(text):
    script = (
        "global.window = {};\n"
        + PROSE_JS.read_text(encoding="utf-8")
        + f"\nprocess.stdout.write(JSON.stringify(window.CCCTicketProse.render({json.dumps(text)})));"
    )
    done = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(done.stdout)


def test_uppercase_options_with_a_note_become_a_list():
    html = _render(
        "[Q533:host-amenity-review] Hosts do NOT receive it. Should I build it this way? "
        "(A, recommended) Add a panel (outward-facing, so I need your yes). "
        "(B) Only the dashboard panel, no email. (C) Leave it admin-only."
    )
    assert html.count("<li") == 3
    assert '<li class="tp-opt-rec"><span class="tp-opt-k">A</span><strong class="tp-opt-note">recommended</strong> Add a panel' in html
    assert "(outward-facing, so I need your yes)" in html
    assert html.startswith('<div class="tp-body"><p class="tp-p">[Q533:host-amenity-review] Hosts do NOT receive it.')


def test_lowercase_and_numbered_options_still_lift():
    assert _render("Which way? (a) keep it, (b) drop it.").count("<li") == 2
    assert _render("Which way? (1) keep it (2) drop it (3) ask.").count("<li") == 3


def test_out_of_sequence_labels_stay_prose():
    assert "<li" not in _render("See (B) first, then (C) maybe.")
    assert "<li" not in _render("Mixed (a) one (2) two.")
