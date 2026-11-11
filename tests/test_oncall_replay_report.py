"""Portable replay reports preserve failures and treat input as text."""

from html import escape
from html.parser import HTMLParser

import pytest

from ccc_server.oncall.replay_report import render_report


OUTCOMES = (
    "fixed", "wrong fix", "no attempt or asked a question", "could not set up",
)


class Elements(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def case(outcome="fixed", **values):
    return dict(title="Example issue", url="https://github.com/example/repo/issues/1",
                outcome=outcome, minutes=3.5, diff="-before\n+after", reason="Verified",
                changed_files=["app.py"], overlap_files=["app.py"], **values)


def test_counts_include_every_outcome_but_exclude_setup_from_denominator():
    cases = [case(outcome) for outcome in OUTCOMES] + [case(), case("could not set up")]
    html = render_report(dict(repo="example/repo", days=7, model="example", cases=cases))
    assert "2 fixed; 1 wrong fix; 1 no attempt or asked a question; 2 could not set up" in html
    assert "2 / 4 evaluated cases fixed." in html
    assert "6 total cases; 4 evaluated." in html
    assert "Setup failures are excluded" in html
    elements = Elements(html)
    assert sum(tag == "details" for tag, _ in elements.tags) == 6
    assert {attrs["class"] for tag, attrs in elements.tags if tag == "td" and "class" in attrs} == {
        "outcome fixed", "outcome wrong-fix", "outcome no-attempt", "outcome setup",
    }
    assert "3.5" in html and "-before\n+after" in html and "Verified" in html
    assert "Changed files" in html and "Overlap files" in html and "app.py" in html


@pytest.mark.parametrize("outcomes", [[], ["could not set up"]])
def test_no_evaluated_cases_has_no_misleading_fixed_rate(outcomes):
    html = render_report({"cases": [case(outcome) for outcome in outcomes]})
    assert "No evaluated cases; fixed rate unavailable." in html
    assert "0 evaluated." in html
    assert "0 / 0" not in html
    if not outcomes:
        assert "No cases." in html
        for outcome in OUTCOMES:
            assert f"0 {outcome}" in html


def test_all_untrusted_text_is_escaped_including_attributes():
    attack = '<script>alert("x")</script>&\' '
    item = case()
    for key in ("title", "minutes", "diff", "reason"):
        item[key] = attack
    item.update(changed_files=[attack], overlap_files=[attack],
                url='https://github.com/example/repo?q=" onmouseover="bad&x=1')
    html = render_report(dict(repo=attack, days=attack, model=attack, cases=[item]))
    assert html.count(escape(attack, quote=True)) == 10
    assert attack not in html
    assert all(tag != "script" for tag, _ in Elements(html).tags)
    # Whitespace in this URL disqualifies it even though its host is GitHub.
    assert all(tag != "a" for tag, _ in Elements(html).tags)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "http://github.com/example/repo", "//github.com/example/repo",
    "https://github.com.evil.example/repo", "https://github.com@evil.example/repo",
    "https://evil.example@github.com/repo", "https://github.com:443/repo",
    "https://github.com\\@evil.example/repo", "https://github.com/\nrepo",
    "https://github.com/\trepo", " https://github.com/repo", "https://[broken",
    None, 42,
])
def test_unsafe_urls_leave_plain_case_titles(url):
    item = case()
    item["url"] = url
    html = render_report({"cases": [item]})
    assert not any(tag == "a" for tag, _ in Elements(html).tags)
    assert "Example issue" in html


def test_safe_github_url_has_escaped_attribute():
    url = 'https://github.com/example/repo/issues/1?q="quoted"&page=2'
    item = case()
    item["url"] = url
    html = render_report({"cases": [item]})
    assert f'href="{escape(url, quote=True)}"' in html
    assert [attrs for tag, attrs in Elements(html).tags if tag == "a"] == [{"href": url}]


def test_report_is_self_contained_with_light_and_dark_outcome_colors():
    html = render_report({"cases": [case(outcome) for outcome in OUTCOMES]})
    assert "@media (prefers-color-scheme: dark)" in html
    assert "color-scheme: light dark" in html
    for variable in ("background", "text", "muted", "surface", "border", "link",
                     "fixed", "wrong", "no-attempt", "setup"):
        assert html.count(f"--{variable}:") == 2
        assert f"var(--{variable})" in html
    elements = Elements(html)
    assert not any(tag in {"script", "link", "img", "iframe"} for tag, _ in elements.tags)
    assert "@import" not in html and "url(" not in html


def test_unknown_outcome_cannot_silently_inflate_evaluated_count():
    with pytest.raises(ValueError, match="Unknown replay outcome"):
        render_report({"cases": [case("unknown")]})
