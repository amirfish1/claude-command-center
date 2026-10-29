import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import leaderboard_suite as suite

# A reference solution per task: (path, content) writes that must make the
# task's checker pass. Proves every checker is satisfiable and deterministic.
SOLUTIONS = {
    "edit_file": {"greet.py": 'print("Hello, world!")\n'},
    "fix_test": {"calc.py": "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"},
    "add_function": {"string_tools.py": 'def shout(t):\n    return t.upper() + "!"\n\ndef whisper(t):\n    return t.lower()\n'},
    "tool_read_write": {"answer.txt": "plum\n"},
    "multi_step": {"sum.txt": "42\n"},
    "fizzbuzz": {"fb.py": (
        "def fizzbuzz(n):\n"
        "    return 'FizzBuzz' if n % 15 == 0 else 'Fizz' if n % 3 == 0 "
        "else 'Buzz' if n % 5 == 0 else str(n)\n")},
    "off_by_one": {"series.py": "def sum_to(n):\n    return sum(range(n + 1))\n"},
    "rename_symbol": {"shapes.py": "def rectangle_area(w, h):\n    return w * h\n"},
    "json_edit": {"config.json": '{"name": "demo", "debug": true, "tags": ["a", "b"], "port": 8080}'},
    "count_errors": {"count.txt": "7\n"},
    "palindrome": {"pal.py": (
        "def is_palindrome(text):\n"
        "    s = [c.lower() for c in text if c.isalnum()]\n"
        "    return s == s[::-1]\n")},
    "handle_bad_input": {"parse.py": (
        "def parse_int(text):\n"
        "    try:\n        return int(text.strip())\n"
        "    except (AttributeError, ValueError):\n        return None\n")},
    "new_module": {"slug.py": (
        "import re\n\ndef slugify(text):\n"
        "    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n")},
    "csv_total": {"west_total.txt": "370\n"},
    "dedupe_sort": {"unique.txt": "ava\neli\nliam\nmaya\nnoah\nzoe\n"},
}


class SuiteTaskTests(unittest.TestCase):
    def test_suite_has_fifteen_unique_tasks_with_solutions(self):
        ids = [t["id"] for t in suite.suite_tasks()]
        self.assertEqual(len(ids), 15)
        self.assertEqual(len(set(ids)), 15)
        self.assertEqual(set(ids), set(SOLUTIONS))

    def test_each_checker_fails_before_and_passes_after_reference_solution(self):
        for task in suite.suite_tasks():
            with self.subTest(task=task["id"]), tempfile.TemporaryDirectory() as wd:
                task["setup"](wd)
                ok, _detail = task["check"](wd)
                self.assertFalse(ok, "checker passes on untouched setup")
                for rel, content in SOLUTIONS[task["id"]].items():
                    (Path(wd) / rel).write_text(content, encoding="utf-8")
                ok, detail = task["check"](wd)
                self.assertTrue(ok, detail)

    def test_rename_rejects_leftover_old_name(self):
        task = next(t for t in suite.suite_tasks() if t["id"] == "rename_symbol")
        with tempfile.TemporaryDirectory() as wd:
            task["setup"](wd)
            Path(wd, "shapes.py").write_text(
                "def area_of(w, h):\n    return w * h\n\nrectangle_area = area_of\n", encoding="utf-8")
            ok, detail = task["check"](wd)
            self.assertFalse(ok)
            self.assertIn("area_of", detail)


class GraderIsolationTests(unittest.TestCase):
    def _task(self, task_id):
        return next(t for t in suite.suite_tasks() if t["id"] == task_id)

    def test_overwritten_grader_is_restored_before_checking(self):
        task = self._task("fizzbuzz")
        with tempfile.TemporaryDirectory() as wd:
            task["setup"](wd)
            Path(wd, "check_fb.py").write_text("print('ok')\n", encoding="utf-8")
            ok, _detail = task["check"](wd)
            self.assertFalse(ok, "a rewritten grader must not pass")

    def test_stdlib_shadow_module_cannot_fake_unittest(self):
        task = self._task("palindrome")
        with tempfile.TemporaryDirectory() as wd:
            task["setup"](wd)
            Path(wd, "unittest.py").write_text("def main(*a, **k):\n    pass\n", encoding="utf-8")
            Path(wd, "sitecustomize.py").write_text("import os\nos._exit(0)\n", encoding="utf-8")
            ok, _detail = task["check"](wd)
            self.assertFalse(ok)

    def test_checks_do_not_see_runner_secrets(self):
        task = self._task("new_module")
        with tempfile.TemporaryDirectory() as wd, \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-XXXX"}):
            task["setup"](wd)
            Path(wd, "slug.py").write_text(
                "import os, re\n"
                "assert 'OPENROUTER_API_KEY' not in os.environ\n"
                "def slugify(text):\n"
                "    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')\n",
                encoding="utf-8")
            ok, detail = task["check"](wd)
            self.assertTrue(ok, detail)

    def test_each_task_gets_its_own_time_budget(self):
        deadlines = []

        def fake_run_task(chat, wire, model, task, wd, deadline):
            deadlines.append(deadline)
            return {"task": task["id"], "passed": True, "detail": "ok", "error": None,
                    "latency_ms": 1, "requests": 1, "median_request_ms": 1, "tool_calls": 1,
                    "input_tokens": 1, "output_tokens": 1}

        with mock.patch.object(suite, "run_task", fake_run_task), \
                mock.patch.object(suite.time, "monotonic", side_effect=range(0, 10_000, 100)):
            suite.run_model({"name": "fake", "chat": None, "wire": "openai"}, "m",
                            log=lambda line: None)
        self.assertEqual(len(deadlines), 15)
        self.assertEqual(deadlines[0], suite.TASK_WALL_BUDGET_S)
        self.assertEqual(deadlines[1], 100 + suite.TASK_WALL_BUDGET_S)


class ZeroPriceTests(unittest.TestCase):
    def test_zero_price_requires_every_listed_price_to_be_zero(self):
        self.assertTrue(suite.is_zero_price({"prompt": "0", "completion": "0", "request": "0"}))
        self.assertFalse(suite.is_zero_price({"prompt": "0", "completion": "0.000001"}))
        self.assertFalse(suite.is_zero_price({"prompt": "0", "completion": "0", "request": "0.01"}))
        self.assertFalse(suite.is_zero_price({"prompt": "0"}))
        self.assertFalse(suite.is_zero_price({}))
        self.assertFalse(suite.is_zero_price(None))
        self.assertFalse(suite.is_zero_price({"prompt": "free", "completion": "0"}))

    def test_openrouter_filter_keeps_only_free_tool_models(self):
        free = {"prompt": "0", "completion": "0"}
        catalog = [
            {"id": "b/model:free", "pricing": free, "supported_parameters": ["tools"]},
            {"id": "a/model:free", "pricing": free, "supported_parameters": ["tools", "tool_choice"]},
            {"id": "c/no-tools:free", "pricing": free, "supported_parameters": ["temperature"]},
            {"id": "d/paid", "pricing": {"prompt": "0.000001", "completion": "0.000002"},
             "supported_parameters": ["tools"]},
            {"id": "e/priced:free", "pricing": {"prompt": "0.1", "completion": "0"},
             "supported_parameters": ["tools"]},
            {"id": "f/zero-not-tagged", "pricing": free, "supported_parameters": ["tools"]},
        ]
        self.assertEqual(suite.openrouter_free_models(catalog), ["a/model:free", "b/model:free"])


def _openai_reply(calls, prompt_tokens=10, completion_tokens=5):
    return 200, {
        "choices": [{"message": {"content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(calls)]}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }, None, 7


def _anthropic_reply(calls):
    return 200, {
        "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": args}
                    for i, (name, args) in enumerate(calls)],
        "usage": {"input_tokens": 20, "output_tokens": 3},
    }, None, 9


class LoopTests(unittest.TestCase):
    def _task(self, task_id):
        return next(t for t in suite.suite_tasks() if t["id"] == task_id)

    def test_openai_loop_executes_tools_and_sums_tokens(self):
        script = [
            _openai_reply([("read_file", {"path": "data/notes.txt"})]),
            _openai_reply([("write_file", {"path": "answer.txt", "content": "plum"}),
                           ("run_checks", {})], 30, 8),
        ]
        sent = []

        def chat(model, messages):
            sent.append([dict(m) for m in messages])
            return script[len(sent) - 1]

        with tempfile.TemporaryDirectory() as wd:
            result = suite.run_task(chat, "openai", "m", self._task("tool_read_write"), wd)
        self.assertTrue(result["passed"], result)
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (40, 13))
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["tool_calls"], 3)
        tool_msgs = [m for m in sent[1] if m.get("role") == "tool"]
        self.assertEqual(tool_msgs[0]["tool_call_id"], "c0")
        self.assertIn("plum", tool_msgs[0]["content"])

    def test_anthropic_loop_passes_with_tool_results(self):
        replies = iter([
            _anthropic_reply([("write_file", {"path": "sum.txt", "content": "42"})]),
            _anthropic_reply([("run_checks", {})]),
        ])
        with tempfile.TemporaryDirectory() as wd:
            result = suite.run_task(lambda m, msgs: next(replies), "anthropic", "m",
                                    self._task("multi_step"), wd)
        self.assertTrue(result["passed"], result)
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (40, 6))

    def test_rate_limit_is_reported_as_infra_error(self):
        def chat(model, messages):
            return 429, None, "rate limited upstream", 5

        backend = {"name": "fake", "chat": chat, "wire": "openai"}
        summary = suite.run_model(backend, "m", log=lambda line: None)
        self.assertEqual(summary["passed"], 0)
        self.assertEqual(summary["tasks"], 15)
        self.assertEqual(summary["infra_errors"], 15)
        self.assertTrue(summary["per_task"][0]["error"].startswith("rate limited"))

    def test_text_only_reply_is_a_plain_failure(self):
        def chat(model, messages):
            return 200, {"choices": [{"message": {"content": "I would edit the file."}}]}, None, 5

        with tempfile.TemporaryDirectory() as wd:
            result = suite.run_task(chat, "openai", "m", self._task("edit_file"), wd)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["error"])


class RenderTests(unittest.TestCase):
    def test_rank_and_markdown_table(self):
        def model(name, rate, ms):
            return {"backend": "openrouter", "model": name, "passed": int(rate * 15), "tasks": 15,
                    "pass_rate": rate, "median_request_ms": ms, "median_task_s": 4.2,
                    "input_tokens": 1000, "output_tokens": 200, "infra_errors": 0, "per_task": []}

        results = {"run_at": "2026-10-09T01:00:00+00:00", "suite_version": 1, "cost_usd": 0.0,
                   "tasks": [{"id": "edit_file", "title": "Fix a typo in a file"}],
                   "skipped": ["github: unreachable"],
                   "models": [model("slow:free", 0.8, 9000), model("fast:free", 0.8, 1200),
                              model("best:free", 1.0, None)]}
        ranked = [m["model"] for m in suite.rank(results["models"])]
        self.assertEqual(ranked, ["best:free", "fast:free", "slow:free"])
        md = suite.render_markdown(results)
        self.assertIn("| 1 | `best:free` | openrouter | 100% | 15/15 | n/a | 4.2s | 1,000 | 200 | 0 |", md)
        self.assertIn("cost $0.00", md)
        self.assertIn("- github: unreachable", md)

    def test_models_with_only_infra_errors_are_listed_as_not_measured(self):
        dead = suite.summarize("openrouter", "dead:free", [
            {"task": f"t{i}", "passed": False, "detail": "", "error": "rate limited: upstream",
             "latency_ms": 5, "requests": 1, "median_request_ms": 5, "tool_calls": 0,
             "input_tokens": 0, "output_tokens": 0} for i in range(3)])
        slow = suite.summarize("openrouter", "loops:free", [
            {"task": "t0", "passed": False, "detail": "", "error": "too many tool turns",
             "latency_ms": 5, "requests": 8, "median_request_ms": 5, "tool_calls": 8,
             "input_tokens": 10, "output_tokens": 2}])
        self.assertEqual(dead["infra_errors"], 3)
        self.assertEqual(slow["infra_errors"], 0)
        md = suite.render_markdown({"run_at": "x", "suite_version": 1, "cost_usd": 0.0,
                                    "tasks": [], "models": [dead, slow]})
        self.assertIn("| 1 | `loops:free` |", md)
        self.assertNotIn("| `dead:free` |", md)
        self.assertIn("- `dead:free` (openrouter): rate limited: upstream", md)


if __name__ == "__main__":
    unittest.main()
