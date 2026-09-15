"""Pipeline Canvas (ccc_server/pipeline_canvas.py).

Pure-core tests: archetype classification, convention-edge derivation, the
state payload's config+health merge, and the layout document's validation /
load / save contract. No subprocess, no server.py, no real fleet.
"""

import importlib
import json
import pathlib
import tempfile
import unittest

pc = importlib.import_module("ccc_server.pipeline_canvas")


def _configs():
    return {
        "BECKY-DESIGN": {"auto_drain": True, "engine": "claude",
                         "model": "claude-opus-5", "effort": "high",
                         "desired_workers": 2, "repo_path": "/r/app"},
        "BECKY": {"auto_drain": False, "engine": "claude",
                  "model": "claude-sonnet-5", "backend": "github",
                  "github_repo": "owner/repo", "product_gate": True},
        "FLEET-VERIFY": {"auto_drain": True, "engine": "kimi"},
    }


def _health():
    return [
        {"queue": "BECKY", "state": "backlog", "depth": 5, "claimable": 3,
         "in_progress": 1, "workers": 0, "auto_drain": False, "stuck": False,
         "staffing_alarm": False, "configured": True,
         "worker_plan": {"engine": "claude", "engine_source": "queue",
                         "model": "claude-sonnet-5", "effort": ""}},
        {"queue": "GHOST", "state": "backlog", "depth": 2, "claimable": 2,
         "workers": 0, "auto_drain": False},
    ]


class ArchetypeClassification(unittest.TestCase):
    def test_design_suffix_is_planner(self):
        self.assertEqual(pc.archetype_for_queue("BECKY-DESIGN"), "planner")
        self.assertEqual(pc.archetype_for_queue("app_design"), "planner")

    def test_verify_review_names_are_reviewer(self):
        self.assertEqual(pc.archetype_for_queue("FLEET-VERIFY"), "reviewer")
        self.assertEqual(pc.archetype_for_queue("CODE-REVIEW"), "reviewer")

    def test_default_is_executor(self):
        self.assertEqual(pc.archetype_for_queue("BECKY"), "executor")
        self.assertEqual(pc.archetype_for_queue(""), "executor")

    def test_planner_base_name(self):
        self.assertEqual(pc.planner_base_name("BECKY-DESIGN"), "BECKY")
        self.assertEqual(pc.planner_base_name("BECKY"), "")


class ConventionEdges(unittest.TestCase):
    def test_planner_edges_to_existing_base_queue(self):
        edges = pc.derive_convention_edges(["BECKY", "BECKY-DESIGN"], _configs())
        conv = [e for e in edges if e["kind"] == "convention"]
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv[0]["source"], "queue:BECKY-DESIGN")
        self.assertEqual(conv[0]["target"], "queue:BECKY")
        self.assertIn("BECKY", conv[0]["contract"])
        self.assertEqual(conv[0]["filing_label"], "watchtower:BECKY")

    def test_planner_without_base_has_no_convention_edge(self):
        edges = pc.derive_convention_edges(["ORPHAN-DESIGN"], {"ORPHAN-DESIGN": {}})
        self.assertEqual([e for e in edges if e["kind"] == "convention"], [])

    def test_planner_always_signs_off_at_the_gate(self):
        edges = pc.derive_convention_edges(["ORPHAN-DESIGN"], {"ORPHAN-DESIGN": {}})
        gate = [e for e in edges if e["kind"] == "gate"]
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]["target"], pc.GATE_NODE_ID)
        self.assertEqual(gate[0]["label"], "design sign-off")

    def test_product_gate_config_edges_to_gate(self):
        edges = pc.derive_convention_edges(["BECKY"], _configs())
        gate = [e for e in edges if e["source"] == "queue:BECKY"]
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]["label"], "product gate")

    def test_no_config_means_no_gate_edge(self):
        edges = pc.derive_convention_edges(["PLAIN"], {})
        self.assertEqual(edges, [])


class CanvasState(unittest.TestCase):
    def test_nodes_merge_config_and_health(self):
        st = pc.canvas_state(configs=_configs(), health_rows=_health(),
                             now=1_790_000_000)
        self.assertTrue(st["ok"])
        nodes = {n["id"]: n for n in st["nodes"]}
        becky = nodes["queue:BECKY"]
        self.assertEqual(becky["archetype"], "executor")
        self.assertEqual(becky["engine"], "claude")
        self.assertEqual(becky["model"], "claude-sonnet-5")
        self.assertFalse(becky["auto_drain"])          # shown honestly
        self.assertEqual(becky["depth"], 5)
        self.assertEqual(becky["claimable"], 3)
        self.assertEqual(becky["github_repo"], "owner/repo")
        design = nodes["queue:BECKY-DESIGN"]
        self.assertEqual(design["archetype"], "planner")
        self.assertEqual(design["desired_workers"], 2)
        self.assertIsNone(design["depth"])             # idle queue: no invented counts

    def test_health_only_queue_renders_unconfigured(self):
        st = pc.canvas_state(configs=_configs(), health_rows=_health())
        nodes = {n["id"]: n for n in st["nodes"]}
        ghost = nodes["queue:GHOST"]
        self.assertFalse(ghost["configured"])
        self.assertEqual(ghost["depth"], 2)

    def test_gate_node_is_always_present(self):
        st = pc.canvas_state(configs={}, health_rows=[])
        gate = [n for n in st["nodes"] if n["kind"] == "gate"]
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0]["id"], pc.GATE_NODE_ID)
        self.assertEqual(gate[0]["url"], "/decision-inbox.html")

    def test_edges_come_along(self):
        st = pc.canvas_state(configs=_configs(), health_rows=_health())
        kinds = {(e["source"], e["kind"]) for e in st["edges"]}
        self.assertIn(("queue:BECKY-DESIGN", "convention"), kinds)
        self.assertIn(("queue:BECKY-DESIGN", "gate"), kinds)
        self.assertIn(("queue:BECKY", "gate"), kinds)

    def test_empty_fleet_still_ok(self):
        st = pc.canvas_state(configs={}, health_rows=[])
        self.assertTrue(st["ok"])
        self.assertEqual(st["edges"], [])
        self.assertEqual(len(st["nodes"]), 1)  # just the gate


class LayoutValidation(unittest.TestCase):
    def test_round_trip_keeps_good_document(self):
        doc = {
            "version": 1,
            "nodes": {
                "queue:BECKY": {"x": 100, "y": -40.5},
                "designed:abc": {"x": 0, "y": 0, "kind": "designed",
                                 "archetype": "planner", "label": "My planner",
                                 "config": {"engine": "claude", "model": "claude-opus-5",
                                            "desired_workers": 2, "junk": "dropped"}},
            },
            "edges": [{"source": "designed:abc", "target": "queue:BECKY",
                       "label": "files builds to"}],
            "viewport": {"x": 10, "y": 20, "zoom": 1.5},
        }
        clean, err = pc.validate_layout(doc)
        self.assertIsNone(err)
        self.assertEqual(clean["nodes"]["queue:BECKY"], {"x": 100.0, "y": -40.5})
        designed = clean["nodes"]["designed:abc"]
        self.assertEqual(designed["archetype"], "planner")
        self.assertEqual(designed["config"]["engine"], "claude")
        self.assertNotIn("junk", designed["config"])
        self.assertEqual(clean["edges"][0]["id"], "user:designed:abc->queue:BECKY")
        self.assertEqual(clean["viewport"]["zoom"], 1.5)

    def test_rejects_non_object(self):
        self.assertEqual(pc.validate_layout([1, 2])[0], None)
        self.assertEqual(pc.validate_layout("x")[0], None)
        _, err = pc.validate_layout({"nodes": [1]})
        self.assertIn("nodes", err)
        _, err = pc.validate_layout({"edges": {}})
        self.assertIn("edges", err)

    def test_drops_bad_entries_keeps_good_ones(self):
        doc = {
            "nodes": {
                "good": {"x": 1, "y": 2},
                "nan": {"x": float("nan"), "y": 0},
                "inf": {"x": float("inf"), "y": 0},
                "stringy": {"x": "not-a-number", "y": 0},
                "nopos": "nope",
                "": {"x": 0, "y": 0},
            },
            "edges": [
                {"source": "good", "target": "good"},        # self-loop dropped
                {"source": "", "target": "good"},            # empty dropped
                {"source": "good", "target": "also-good"},   # kept
            ],
        }
        clean, err = pc.validate_layout(doc)
        self.assertIsNone(err)
        self.assertEqual(set(clean["nodes"]), {"good"})
        self.assertEqual(len(clean["edges"]), 1)

    def test_clamps_coordinates_and_zoom(self):
        doc = {"nodes": {"far": {"x": 1e9, "y": -1e9}},
               "edges": [],
               "viewport": {"x": 0, "y": 0, "zoom": 99}}
        clean, _ = pc.validate_layout(doc)
        self.assertEqual(clean["nodes"]["far"]["x"], pc.MAX_COORD)
        self.assertEqual(clean["nodes"]["far"]["y"], -pc.MAX_COORD)
        self.assertEqual(clean["viewport"]["zoom"], pc.ZOOM_MAX)

    def test_caps_collections(self):
        doc = {"nodes": {f"n{i}": {"x": i, "y": 0} for i in range(pc.MAX_NODES + 50)},
               "edges": [{"source": f"n{i}", "target": f"n{i+1}"}
                         for i in range(pc.MAX_EDGES + 50)]}
        clean, _ = pc.validate_layout(doc)
        self.assertEqual(len(clean["nodes"]), pc.MAX_NODES)
        self.assertEqual(len(clean["edges"]), pc.MAX_EDGES)


class LayoutIO(unittest.TestCase):
    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            doc = pc.load_layout(pathlib.Path(td) / "nope.json")
        self.assertEqual(doc, pc.default_layout())

    def test_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "canvas-layout.json"
            p.write_text("{not json", encoding="utf-8")
            doc = pc.load_layout(p)
        self.assertEqual(doc, pc.default_layout())

    def test_structurally_invalid_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "canvas-layout.json"
            p.write_text(json.dumps({"nodes": [1, 2]}), encoding="utf-8")
            doc = pc.load_layout(p)
        self.assertEqual(doc, pc.default_layout())

    def test_save_is_atomic_and_reloadable(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "canvas-layout.json"
            doc, _ = pc.validate_layout({"nodes": {"queue:X": {"x": 3, "y": 4}},
                                         "edges": []})
            result = pc.save_layout(doc, p)
            self.assertTrue(result["ok"])
            self.assertEqual(pc.load_layout(p)["nodes"]["queue:X"], {"x": 3.0, "y": 4.0})
            # No temp file left behind.
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_env_state_dir_override(self):
        # layout_path honors the same COMMAND_CENTER_STATE_DIR the other
        # state-dir documents use (via _core, falling back to ~/.claude).
        with tempfile.TemporaryDirectory() as td:
            p = pc.layout_path(pathlib.Path(td) / "canvas-layout.json")
            self.assertTrue(str(p).endswith("canvas-layout.json"))


if __name__ == "__main__":
    unittest.main()
