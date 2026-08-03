"""Static regression checks for the in-app update progress overlay."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def _z_index(selector: str) -> int:
    rule = re.search(
        re.escape(selector) + r"\s*\{(?P<body>[^}]+)\}",
        APP_CSS,
        flags=re.DOTALL,
    )
    assert rule, f"missing CSS rule for {selector}"
    value = re.search(r"z-index:\s*(\d+)", rule.group("body"))
    assert value, f"missing z-index for {selector}"
    return int(value.group(1))


def test_loading_overlay_stacks_above_update_and_system_modals():
    loading_layer = _z_index(".ccc-loading-overlay")
    modal_layers = (
        _z_index(".upd-overlay"),
        _z_index("#sysModal"),
        _z_index(".ann-ux-preview-modal"),
        _z_index(".uxq-td-overlay"),
    )

    assert loading_layer > max(modal_layers)
