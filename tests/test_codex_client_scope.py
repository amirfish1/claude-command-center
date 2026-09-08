import tempfile
import unittest
from pathlib import Path
from ccc_server import codex_client as client


class ClientScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "repo"
        self.repo.mkdir()
        self.context = {"repo_path": str(self.repo), "thread_id": "task"}

    def check(self, method, params, context=None, cwd=None):
        return client.validate_operation_context(method, params, context or self.context,
            resolve_repo=lambda raw: str(Path(raw).resolve()),
            read_thread=lambda tid: {"id": tid, "cwd": cwd or str(self.repo)})

    def test_other_thread_or_repository_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check("thread/read", {"threadId": ""})
        with self.assertRaises(ValueError):
            self.check("turn/start", {"threadId": "other"})
        with self.assertRaises(ValueError):
            self.check("thread/delete", {"threadId": "task"}, cwd=str(self.repo.parent))

    def test_file_paths_are_clamped_and_symlink_escape_is_rejected(self):
        result = self.check("fs/readFile", {"path": "notes.txt"})
        self.assertEqual(result["path"], str(self.repo / "notes.txt"))
        (self.repo / "outside").symlink_to(self.repo.parent, target_is_directory=True)
        for file_path in ("../outside.txt", "outside/private.txt"):
            with self.assertRaises(ValueError):
                self.check("fs/writeFile", {"path": file_path, "dataBase64": ""})

    def test_both_copy_paths_are_validated(self):
        with self.assertRaises(ValueError):
            self.check("fs/copy", {"sourcePath": "ok", "destinationPath": "../bad"})

    def test_new_thread_uses_explicit_repository(self):
        self.assertEqual(self.check("thread/start", {})["cwd"], str(self.repo))
        with self.assertRaises(ValueError):
            self.check("thread/start", {"cwd": str(self.repo.parent)})

    def test_account_actions_do_not_require_repository(self):
        self.assertEqual(self.check("account/read", {}, context={"account": True}), {})

    def test_remote_paths_cannot_fall_back_to_local_operations(self):
        with self.assertRaises(ValueError):
            self.check("fs/readFile", {"path": "/remote/a"},
                       context={"repo_path": str(self.repo), "environment_id": "remote"})

    def test_source_parameters_are_not_mutated(self):
        original = {"cwd": "nested"}
        result = self.check("command/exec", original)
        self.assertEqual(original, {"cwd": "nested"})
        self.assertEqual(result["cwd"], str(self.repo / "nested"))

    def test_secret_fields_are_redacted_recursively(self):
        result = client.redact_client_data({"accessToken": "secret", "nested": [{"api_key": "hidden"}], "tokenUsage": {"totalTokens": 10}})
        self.assertNotIn("secret", str(result))
        self.assertNotIn("hidden", str(result))
        self.assertEqual(result["tokenUsage"]["totalTokens"], 10)

    def test_thread_listing_is_bound_to_selected_repo(self):
        self.assertEqual(self.check("thread/list", {})["cwd"], str(self.repo))
        self.assertEqual(self.check("thread/list", {"cwd": [str(self.repo)]})["cwd"], [str(self.repo)])
        with self.assertRaises(ValueError):
            self.check("thread/list", {"cwd": str(self.repo.parent)})

    def test_remote_selection_cannot_retarget_local_settings_or_task_start(self):
        for method in ("config/read", "skills/list", "thread/start"):
            with self.assertRaises(ValueError):
                self.check(method, {}, context={"repo_path": str(self.repo), "environment_id": "remote"})

    def test_path_bearing_control_calls_require_and_confine_repo(self):
        for method, params in (("windowsSandbox/setupStart", {"cwd": str(self.repo.parent)}),
                               ("hooks/list", {"cwds": [str(self.repo.parent)]}),
                               ("permissionProfile/list", {"cwd": str(self.repo.parent)})):
            with self.assertRaises(ValueError):
                self.check(method, params)

    def test_config_override_cannot_write_an_unrelated_file(self):
        with self.assertRaises(ValueError):
            client.validate_operation_context("config/batchWrite", {"filePath": str(self.repo.parent / "private.toml")},
                self.context, descriptor={"scope": "workspace"}, resolve_repo=lambda x: x, read_thread=lambda x: {})
