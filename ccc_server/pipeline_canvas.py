"""Pipeline Canvas — read-only fleet topology for the canvas surface.

Spec: docs/superpowers/specs/2026-09-15-pipeline-canvas-design.md.

The canvas renders REAL WatchTower truth — queue definitions from
``queue-config.json`` and live per-queue health from
``compute_queues_health()`` — plus exactly one piece of canvas-owned state:
the *view* document (node positions, viewport, designed nodes, user-drawn
edges) at ``~/.claude/command-center/canvas-layout.json``. No parallel
document of queue facts exists; a node renders config + health or it does
not render.

Mutation contract: this module never writes queue-config, never calls
``wt``, never nudges the reconciler. The only write anywhere is the layout
document (atomic tmp+replace, validated hard).

Every server name is reached through ``_core`` at call time, and every
entry point takes explicit inputs (health rows, config dict, path) so tests
run the whole module without server.py, subprocesses, or a real fleet.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from ccc_server import core as _core

LAYOUT_FILE_NAME = "canvas-layout.json"
LAYOUT_VERSION = 1

# Validation bounds for the layout document. Generous on purpose — the
# canvas is infinite — but capped so a corrupt or hostile POST cannot grow
# the file without bound or park a node at 1e300.
MAX_NODES = 500
MAX_EDGES = 1000
MAX_DESIGNED = 200
MAX_COORD = 100000.0
MAX_LABEL = 200
MAX_ID = 120
ZOOM_MIN = 0.15
ZOOM_MAX = 3.0

# The five first-class archetypes (see the spec's archetype contract).
ARCHETYPES = ("planner", "executor", "reviewer", "stream", "gate")

# Runtime classification hints. queue-config.json has no `kind` field, so a
# runtime node's archetype is a presentation hint derived from the queue
# NAME — the config stays the only truth and the hint never writes back.
# A queue named `X-DESIGN` runs the planner pattern (design-only workers,
# files builds to X, ends at a human gate); a queue named for verify/review
# runs the independent-reviewer pattern; everything else is an executor.
PLANNER_SUFFIXES = ("-DESIGN", "_DESIGN")
REVIEWER_HINTS = ("VERIFY", "REVIEW")
# Stream filers are scheduled producers that live OUTSIDE queue-config
# (launchd, cron, external repos) — v1 renders them as designed nodes only,
# never as runtime nodes.

GATE_NODE_ID = "gate:decision-inbox"


def _norm(name):
    return str(name or "").strip().upper()


def archetype_for_queue(name):
    """planner / reviewer / executor from the queue name (see module docstring)."""
    q = _norm(name)
    if any(q.endswith(sfx) for sfx in PLANNER_SUFFIXES):
        return "planner"
    if any(hint in q for hint in REVIEWER_HINTS):
        return "reviewer"
    return "executor"


def planner_base_name(name):
    """`X-DESIGN` → `X`, else ''. The executor a planner files builds to."""
    q = _norm(name)
    for sfx in PLANNER_SUFFIXES:
        if q.endswith(sfx):
            return q[: -len(sfx)]
    return ""


def derive_convention_edges(queue_names, configs=None):
    """Edges the fleet's filing conventions imply, from queue names + config.

    - planner → executor: `X-DESIGN` files build tickets to `X` (only when
      the base queue actually exists — a dangling convention is no edge).
    - planner → human gate: the planner pattern's terminal state is a human
      sign-off, so every planner edges to the Decision Inbox gate node.
    - executor → human gate: queues configured with ``product_gate`` park
      finished work for a human decision.

    Each edge carries its filing contract (``contract``) for the tooltip.
    """
    names = {_norm(n) for n in (queue_names or []) if _norm(n)}
    configs = configs if isinstance(configs, dict) else {}
    conf_by_norm = {}
    for raw_name, conf in configs.items():
        if isinstance(conf, dict):
            conf_by_norm[_norm(raw_name)] = conf
    edges = []
    for q in sorted(names):
        base = planner_base_name(q)
        if base:
            if base in names:
                edges.append({
                    "id": f"conv:{q}->{base}",
                    "source": f"queue:{q}",
                    "target": f"queue:{base}",
                    "kind": "convention",
                    "label": "files builds to",
                    "contract": f"completed designs become build tickets on {base}",
                    "filing_label": f"watchtower:{base}",
                })
            edges.append({
                "id": f"gate:{q}->DECISIONS",
                "source": f"queue:{q}",
                "target": GATE_NODE_ID,
                "kind": "gate",
                "label": "design sign-off",
                "contract": "a finished design parks for a human decision",
            })
            continue
        conf = conf_by_norm.get(q) or {}
        if conf.get("product_gate"):
            edges.append({
                "id": f"gate:{q}->DECISIONS",
                "source": f"queue:{q}",
                "target": GATE_NODE_ID,
                "kind": "gate",
                "label": "product gate",
                "contract": "finished work waits for owner sign-off",
            })
    return edges


def _int_or_none(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _queue_node(name, conf, health_row):
    """One runtime node: the config entry merged with its live health row."""
    q = _norm(name)
    conf = conf if isinstance(conf, dict) else {}
    hr = health_row if isinstance(health_row, dict) else {}
    plan = hr.get("worker_plan") if isinstance(hr.get("worker_plan"), dict) else {}
    engine = str(conf.get("engine") or plan.get("engine") or "").strip()
    model = str(conf.get("model") or plan.get("model") or "").strip()
    effort = str(conf.get("effort") or plan.get("effort") or "").strip()
    desired = _int_or_none(conf.get("desired_workers")) or _int_or_none(hr.get("desired_workers"))
    node = {
        "id": f"queue:{q}",
        "kind": "queue",
        "queue": q,
        "archetype": archetype_for_queue(q),
        "configured": bool(conf) or bool(hr.get("configured")),
        "engine": engine,
        "model": model,
        "effort": effort,
        "engine_source": str(plan.get("engine_source") or ("queue" if conf.get("engine") else "")),
        # Same honesty as engine_source: a value the queue itself pinned vs a
        # resolved default must not read as a deliberate choice.
        "effort_source": str(plan.get("effort_source") or ("queue" if conf.get("effort") else "engine_default")),
        "auto_drain": bool(conf.get("auto_drain", hr.get("auto_drain", False))),
        "desired_workers": desired or 1,
        "desired_workers_source": "queue" if desired else "default",
        "backend": str(conf.get("backend") or hr.get("backend") or "").strip(),
        "repo_path": str(conf.get("repo_path") or hr.get("repo_path") or "").strip(),
        "github_repo": str(conf.get("github_repo") or hr.get("github_repo") or "").strip(),
        "product_gate": bool(conf.get("product_gate", False)),
        # Live health (absent → None, never invented).
        "state": hr.get("state"),
        "stuck": bool(hr.get("stuck", False)),
        "staffing_alarm": bool(hr.get("staffing_alarm", False)),
        "depth": _int_or_none(hr.get("depth")),
        "claimable": _int_or_none(hr.get("claimable")),
        "in_progress": _int_or_none(hr.get("in_progress")),
        "closed": _int_or_none(hr.get("closed")),
        "total": _int_or_none(hr.get("total")),
        "gated": _int_or_none(hr.get("gated")),
        "workers": _int_or_none(hr.get("workers")),
        "effective_workers": _int_or_none(hr.get("effective_workers")),
        "last_activity_seconds": _int_or_none(hr.get("last_activity_seconds")),
    }
    return node


def _gate_node(open_cards=None):
    node = {
        "id": GATE_NODE_ID,
        "kind": "gate",
        "archetype": "gate",
        "label": "Decision Inbox",
        "url": "/decision-inbox.html",
        "open_cards": _int_or_none(open_cards),
    }
    return node


def _decision_inbox_open_cards():
    """Open decision-card count for the gate node, when cheaply available.

    decision_inbox_api_payload() is a cache read (a poll never triggers a
    scan), so this adds no scan and no analyst call. Any failure → None:
    the gate node renders without a count rather than failing the payload.
    """
    try:
        payload = _core.decision_inbox_api_payload()
        cards = payload.get("cards") if isinstance(payload, dict) else None
        if isinstance(cards, list):
            return sum(1 for c in cards if isinstance(c, dict) and not c.get("closed"))
    except Exception:
        pass
    return None


def canvas_state(configs=None, health_rows=None, now=None):
    """GET /api/canvas/state payload: runtime nodes + convention edges + gate.

    Inputs are injectable for tests; the defaults read live truth through
    ``_core`` (queue-config.json and the memoized per-queue health rollup).
    A queue present in config but without tickets still renders (idle is
    truth); a queue with tickets but no config entry renders unconfigured.
    """
    if configs is None:
        try:
            configs = _core._wt_read_config()
        except Exception:
            configs = {}
    configs = configs if isinstance(configs, dict) else {}
    if health_rows is None:
        try:
            health_rows = _core.compute_queues_health()
        except Exception:
            health_rows = []
    health_rows = health_rows if isinstance(health_rows, list) else []

    conf_by_q = {}
    for raw_name, conf in configs.items():
        q = _norm(raw_name)
        if q and isinstance(conf, dict):
            conf_by_q[q] = conf
    health_by_q = {}
    for row in health_rows:
        if not isinstance(row, dict):
            continue
        q = _norm(row.get("queue"))
        if q:
            health_by_q[q] = row

    nodes = []
    for q in sorted(set(conf_by_q) | set(health_by_q)):
        nodes.append(_queue_node(q, conf_by_q.get(q), health_by_q.get(q)))
    nodes.append(_gate_node(open_cards=_decision_inbox_open_cards()))

    edges = derive_convention_edges(conf_by_q.keys() | health_by_q.keys(), configs)
    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
        "nodes": nodes,
        "edges": edges,
    }


# ── layout (the only canvas-owned state) ─────────────────────────────────────

def layout_path(path=None):
    if path is not None:
        return Path(path)
    try:
        base = Path(_core.COMMAND_CENTER_STATE_DIR)
    except Exception:
        base = Path.home() / ".claude" / "command-center"
    return base / LAYOUT_FILE_NAME


def default_layout():
    return {"version": LAYOUT_VERSION, "nodes": {}, "edges": [], "viewport": None}


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _clean_float(value, lo=-MAX_COORD, hi=MAX_COORD):
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return _clamp(num, lo, hi)


def _clean_text(value, cap):
    text = str(value or "").strip()[:cap]
    return text


def validate_layout(payload):
    """(doc, error) — strict validation of a layout document.

    Unknown keys are dropped, numbers are coerced/clamped, collections are
    capped. Anything structurally wrong (not a dict, nodes not a dict,
    edges not a list) rejects the whole document with an error string so a
    torn or hostile POST can never replace a good layout.
    """
    if not isinstance(payload, dict):
        return None, "layout must be a JSON object"
    raw_nodes = payload.get("nodes", {})
    raw_edges = payload.get("edges", [])
    raw_viewport = payload.get("viewport")
    if not isinstance(raw_nodes, dict):
        return None, "nodes must be an object"
    if not isinstance(raw_edges, list):
        return None, "edges must be an array"

    doc = default_layout()
    designed_count = 0
    for node_id, pos in raw_nodes.items():
        if len(doc["nodes"]) >= MAX_NODES:
            break
        node_id = _clean_text(node_id, MAX_ID)
        if not node_id or not isinstance(pos, dict):
            continue
        x = _clean_float(pos.get("x"))
        y = _clean_float(pos.get("y"))
        if x is None or y is None:
            continue
        entry = {"x": x, "y": y}
        kind = _clean_text(pos.get("kind"), 20)
        if kind in ("queue", "designed", "gate"):
            entry["kind"] = kind
        arch = _clean_text(pos.get("archetype"), 20)
        if arch in ARCHETYPES:
            entry["archetype"] = arch
        label = _clean_text(pos.get("label"), MAX_LABEL)
        if label:
            entry["label"] = label
        # Designed nodes may sketch config intent (engine/model/effort/
        # desired_workers). It is design data, never written anywhere real.
        conf = pos.get("config")
        if isinstance(conf, dict):
            clean_conf = {}
            for key in ("engine", "model", "effort", "repo_path", "github_repo"):
                val = _clean_text(conf.get(key), MAX_LABEL)
                if val:
                    clean_conf[key] = val
            dw = conf.get("desired_workers")
            if isinstance(dw, int) and not isinstance(dw, bool) and 1 <= dw <= 32:
                clean_conf["desired_workers"] = dw
            if conf.get("auto_drain") is True:
                clean_conf["auto_drain"] = True
            if clean_conf:
                entry["config"] = clean_conf
        if entry.get("kind") == "designed":
            designed_count += 1
            if designed_count > MAX_DESIGNED:
                continue
        doc["nodes"][node_id] = entry

    for edge in raw_edges:
        if len(doc["edges"]) >= MAX_EDGES:
            break
        if not isinstance(edge, dict):
            continue
        source = _clean_text(edge.get("source"), MAX_ID)
        target = _clean_text(edge.get("target"), MAX_ID)
        if not source or not target or source == target:
            continue
        entry = {
            "id": _clean_text(edge.get("id"), MAX_ID) or f"user:{source}->{target}",
            "source": source,
            "target": target,
        }
        label = _clean_text(edge.get("label"), MAX_LABEL)
        if label:
            entry["label"] = label
        doc["edges"].append(entry)

    if isinstance(raw_viewport, dict):
        vx = _clean_float(raw_viewport.get("x"))
        vy = _clean_float(raw_viewport.get("y"))
        vz = _clean_float(raw_viewport.get("zoom"), ZOOM_MIN, ZOOM_MAX)
        if vx is not None and vy is not None and vz is not None:
            doc["viewport"] = {"x": vx, "y": vy, "zoom": vz}
    return doc, None


def load_layout(path=None):
    """The stored layout, or defaults when absent/corrupt. Never raises."""
    try:
        raw = json.loads(layout_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_layout()
    doc, err = validate_layout(raw)
    return doc if doc is not None else default_layout()


def save_layout(doc, path=None):
    """Atomic tmp+replace, same pattern as the other state-dir documents."""
    target = layout_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return {"ok": True, "path": str(target)}
