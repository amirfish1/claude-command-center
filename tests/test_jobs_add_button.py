"""Jobs "+ Add" (CCC-1223) and POST /api/jobs/add + `ccc jobs` (CCC-1224).

The dialog, the CLI and agents all go through /api/jobs/add, which validates
the fields, composes the agent prompt server-side (ccc_server/jobs_add.py) and
spawns a session; CCC itself never writes systemd units or LaunchAgents.
"""

import importlib.machinery
import importlib.util
import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

from ccc_server import jobs_add

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIELDS = {"repo": "/srv/proj", "what": "Summarize failed CI runs", "when": "daily 7am"}


def _load_ccc_cli():
    loader = importlib.machinery.SourceFileLoader("ccc_cli_jobs", str(ROOT / "ccc"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ComposePromptTest(unittest.TestCase):
    def test_fields_and_common_contract(self):
        for host in ("hermes", "laptop"):
            p = jobs_add.compose_job_prompt(dict(FIELDS, host=host))
            self.assertIn("Summarize failed CI runs", p)
            self.assertIn("daily 7am", p)
            self.assertIn("/srv/proj", p)
            self.assertIn("CCC_OUTCOME:", p)
            self.assertIn("/api/jobs", p)
            self.assertIn("Pick a short kebab-case job name", p)

    def test_explicit_name(self):
        p = jobs_add.compose_job_prompt(dict(FIELDS, host="hermes", name="bym-release-watch"))
        self.assertIn("Name the job `bym-release-watch`.", p)
        self.assertNotIn("Pick a short", p)

    def test_hermes_uses_systemd_timer(self):
        p = jobs_add.compose_job_prompt(dict(FIELDS, host="hermes"))
        self.assertIn("ssh hermes", p)
        self.assertIn("/etc/systemd/system/<name>.timer", p)
        self.assertIn("User=hermes", p)
        self.assertIn("enable --now", p)
        self.assertNotIn("LaunchAgents", p)

    def test_laptop_uses_launchd(self):
        p = jobs_add.compose_job_prompt(dict(FIELDS, host="laptop"))
        self.assertIn("~/Library/LaunchAgents/", p)
        self.assertIn("StartCalendarInterval", p)
        self.assertIn("~/Library/Logs/<name>/", p)
        self.assertIn("launchctl bootstrap", p)
        self.assertNotIn("systemd", p)

    def test_session_name(self):
        self.assertEqual(jobs_add.job_session_name({"what": "Watch releases\nmore", "name": ""}),
                         "New job: Watch releases")
        self.assertEqual(jobs_add.job_session_name({"what": "x", "name": "bym-watch"}), "New job: bym-watch")


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name).resolve()
        self.repo = root / "BYM"
        self.repo.mkdir()
        self.known = [str(self.repo)]

    def _v(self, **kw):
        body = {"host": "laptop", "repo": str(self.repo), "what": "do it", "when": "hourly"}
        body.update(kw)
        return jobs_add.validate_job_add(body, self.known)

    def test_missing_fields(self):
        f, err = self._v(what="  ", when="")
        self.assertIsNone(f)
        self.assertEqual(err, "missing what, when")
        self.assertEqual(jobs_add.validate_job_add([], self.known)[1], "body must be a JSON object")

    def test_host_allow_list(self):
        self.assertIn("host must be one of", self._v(host="prod")[1])

    def test_laptop_path_and_folder_name(self):
        f, err = self._v()
        self.assertIsNone(err)
        self.assertEqual(f["repo"], str(self.repo))
        f, err = self._v(repo="BYM")
        self.assertEqual(f["repo"], str(self.repo))

    def test_laptop_unknown_repo(self):
        self.assertIn("not a known repo", self._v(repo="/nope/else")[1])
        self.assertIn("not a known repo", self._v(repo="Other")[1])

    def test_hermes_repo_forms(self):
        f, err = self._v(host="hermes", repo="BYM+Finie")
        self.assertIsNone(err)
        self.assertEqual(f["repo"], "/home/hermes/Apps/BYM+Finie")
        f, _ = self._v(host="hermes", repo="/home/hermes/Apps/BYM")
        self.assertEqual(f["repo"], "/home/hermes/Apps/BYM")
        # A known laptop repo maps to the same folder on the VM.
        f, _ = self._v(host="hermes", repo=str(self.repo))
        self.assertEqual(f["repo"], "/home/hermes/Apps/BYM")

    def test_hermes_rejects_escape_and_foreign_paths(self):
        for bad in ("/home/hermes/Apps/../etc", "/etc/passwd", "..", "/home/hermes/Apps/a/b"):
            self.assertIsNotNone(self._v(host="hermes", repo=bad)[1], bad)

    def test_name_slug(self):
        self.assertIsNone(self._v(name="bym-release-watch")[1])
        self.assertIn("kebab-case", self._v(name="Bad Name")[1])

    def test_spawn_cwd_and_body(self):
        f, _ = self._v(host="hermes", repo="BYM", model="opus", effort="high")
        self.assertEqual(jobs_add.spawn_cwd(f, self.known, "/fallback"), str(self.repo))
        g, _ = self._v(host="hermes", repo="Elsewhere")
        self.assertEqual(jobs_add.spawn_cwd(g, self.known, "/fallback"), "/fallback")
        body = jobs_add.build_spawn_body(f, {"report_to": "sid-1", "junk": 1}, "/x")
        self.assertEqual(body["cwd"], "/x")
        self.assertEqual(body["model"], "opus")
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["report_to"], "sid-1")
        self.assertNotIn("junk", body)
        self.assertIn("/home/hermes/Apps/BYM", body["prompt"])

    def test_handle_rejects_before_spawning(self):
        with mock.patch.object(jobs_add, "post_spawn") as post:
            res, status = jobs_add.handle_jobs_add({"host": "laptop"}, self.known, 1, "/x")
        self.assertEqual(status, 400)
        self.assertFalse(res["ok"])
        post.assert_not_called()


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ccc = _load_ccc_cli()

    def _parse(self, argv):
        captured = {}

        def grab(args):
            captured["args"] = args
            return 0

        with mock.patch.object(self.ccc, "cmd_jobs_add", grab), \
                mock.patch.object(self.ccc, "cmd_jobs_ls", grab):
            self.assertEqual(self.ccc.main(argv), 0)
        return captured["args"]

    def test_jobs_add_args(self):
        a = self._parse(["jobs", "add", "--host", "hermes", "--repo", "BYM+Finie",
                         "--what", "watch releases", "--when", "hourly",
                         "--name", "bym-release-watch", "--wait"])
        self.assertEqual((a.host, a.repo, a.what, a.when, a.name, a.wait),
                         ("hermes", "BYM+Finie", "watch releases", "hourly", "bym-release-watch", True))
        self.assertIsNone(a.model)

    def test_jobs_add_requires_fields_and_valid_host(self):
        for argv in (["jobs", "add", "--host", "hermes"],
                     ["jobs", "add", "--host", "prod", "--repo", "r", "--what", "w", "--when", "x"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                self.ccc.main(argv)

    def test_jobs_ls_args(self):
        a = self._parse(["jobs", "ls", "--host", "hermes"])
        self.assertEqual(a.host, "hermes")
        self.assertIsNone(self._parse(["jobs", "ls"]).host)

    def test_jobs_ls_output(self):
        payload = {"ok": True, "hosts": {"hermes": {"status": "online"}}, "jobs": [
            {"host": "hermes", "name": "bym-ship", "status": "ok",
             "last_run_at": "2026-09-29T10:00:00Z", "next_run_at": "2026-09-29T14:00:00Z",
             "outcome": "shipped 2 PRs"},
            {"host": "laptop", "name": "other", "status": "failed"},
        ]}
        now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        line = self.ccc._job_line(payload["jobs"][0], now)
        self.assertIn("bym-ship", line)
        self.assertIn("last 2h00m ago", line)
        self.assertIn("next in 2h00m", line)
        self.assertIn("shipped 2 PRs", line)
        args = self._parse(["--server", "http://127.0.0.1:1", "jobs", "ls", "--host", "hermes"])
        out = io.StringIO()
        with mock.patch.object(self.ccc, "_get_json", return_value=payload), redirect_stdout(out):
            self.assertEqual(self.ccc.cmd_jobs_ls(args), 0)
        self.assertIn("bym-ship", out.getvalue())
        self.assertNotIn("other", out.getvalue())


class WiringTest(unittest.TestCase):
    def test_ui_posts_to_jobs_add(self):
        js = (ROOT / "static" / "jobs-tab.js").read_text()
        self.assertIn("data-jobs-add", js)
        self.assertIn("job: f", js)
        self.assertNotIn("function composePrompt", js)
        app = (ROOT / "static" / "app.js").read_text()
        self.assertIn("'/api/jobs/add'", app)
        self.assertIn('elif path == "/api/jobs/add":', (ROOT / "server.py").read_text())


if __name__ == "__main__":
    unittest.main()
