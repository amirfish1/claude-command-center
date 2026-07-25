"""Static contract test for the composer's context ring (KIMI-FIXES-10).

Extracts _contextRingSvg from static/app.js and executes it in node with a
tiny escapeHtml-free harness, asserting the SVG contract: arc math (offset =
C*(1-pct/100)), pct clamping, and the warm/hot threshold classes.
"""
import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def _ring_source():
    source = APP_JS.read_text(encoding="utf-8")
    marker = "function _contextRingSvg(pct) {"
    start = source.index(marker)
    end = source.index("\n  }", start) + len("\n  }")
    return source[start:end]


def _run(expr):
    script = f"""
{_ring_source()}
process.stdout.write(JSON.stringify({expr}));
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def test_ring_arc_math_and_clamping():
    # pct drives stroke-dashoffset = C*(1-p): 0% -> full offset, 100% -> zero.
    for pct in (0, 50, 100, 140, -5):
        svg = _run(f"_contextRingSvg({pct})")
        r = 7
        import math
        c = 2 * math.pi * r
        want = c * (1 - min(100, max(0, pct)) / 100)
        assert f'stroke-dashoffset="{want:.2f}"' in svg, (pct, svg)
        assert 'class="ctx-ring' in svg


def test_ring_threshold_classes():
    assert 'is-hot' not in _run("_contextRingSvg(59)")
    assert 'is-warm' in _run("_contextRingSvg(60)")
    assert 'is-hot' in _run("_contextRingSvg(85)")
    assert 'is-warm' not in _run("_contextRingSvg(85)")
