"""Unit tests for the handoff core: transcript rewrite, bundle build,
staged/atomic import, ownership leases."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import federation


SRC = "/home/alice/code/demo-app"
DST = "/home/bob/repos/demo-app"
SID = "abcdefab-1111-2222-3333-444444444444"


def _transcript_bytes(cwd=SRC, extra_lines=()):
    lines = [
        {"type": "summary", "summary": "demo"},
        {"type": "user", "cwd": cwd, "sessionId": SID,
         "message": {"role": "user", "content": "hello"}, "gitBranch": "main"},
        {"type": "assistant", "cwd": cwd, "sessionId": SID,
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": f"reading {cwd}/a.py"}]}},
        {"type": "user", "cwd": cwd + "/subdir", "sessionId": SID,
         "message": {"role": "user", "content": "in subdir"}},
    ]
    lines.extend(extra_lines)
    return ("\n".join(json.dumps(l) for l in lines) + "\n").encode()


class _IsolatedHome(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("HOME")
        self._tmp = tempfile.TemporaryDirectory(prefix="ccc-handoff-test-")
        os.environ["HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        self._tmp.cleanup()


class TestRewrite(unittest.TestCase):
    def test_cwd_rewritten_subdirs_included(self):
        out, audit = federation.rewrite_transcript(_transcript_bytes(), SRC, DST)
        self.assertEqual(audit["cwd_rewrites"], 3)
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual(rows[1]["cwd"], DST)
        self.assertEqual(rows[3]["cwd"], DST + "/subdir")
        # Message content (history) is untouched
        self.assertIn(SRC, rows[2]["message"]["content"][0]["text"])

    def test_foreign_cwd_warns_not_rewritten(self):
        extra = ({"type": "user", "cwd": "/somewhere/else", "sessionId": SID},)
        out, audit = federation.rewrite_transcript(
            _transcript_bytes(extra_lines=extra), SRC, DST)
        self.assertEqual(len(audit["warnings"]), 1)
        self.assertIn("/somewhere/else", audit["warnings"][0])
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual(rows[-1]["cwd"], "/somewhere/else")

    def test_similar_prefix_not_rewritten(self):
        # /home/alice/code/demo-app-2 must NOT match /home/alice/code/demo-app
        extra = ({"type": "user", "cwd": SRC + "-2", "sessionId": SID},)
        out, audit = federation.rewrite_transcript(
            _transcript_bytes(extra_lines=extra), SRC, DST)
        rows = [json.loads(l) for l in out.splitlines() if l.strip()]
        self.assertEqual(rows[-1]["cwd"], SRC + "-2")

    def test_malformed_lines_pass_through(self):
        raw = b'not json at all\n' + _transcript_bytes()
        out, audit = federation.rewrite_transcript(raw, SRC, DST)
        self.assertTrue(out.startswith(b"not json at all"))
        self.assertEqual(audit["cwd_rewrites"], 3)


class TestBundleAndImport(_IsolatedHome):
    def _bundle(self, transcript=None, dest_cwd=DST):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            f.write(transcript or _transcript_bytes())
            path = f.name
        self.addCleanup(os.unlink, path)
        return federation.build_transfer_bundle(
            engine="claude",
            session_id=SID,
            transcript_path=path,
            source_cwd=SRC,
            dest_cwd=dest_cwd,
            repo_identity="example.test/acme/demo-app",
            source_node="node-a",
            dest_node="node-b",
            branch="main",
            commit="f" * 40,
            sidecars={"display_name": "my session"},
        )

    def test_bundle_manifest_valid_and_hashes_match(self):
        bundle = self._bundle()
        self.assertEqual(federation.validate_transfer_manifest(bundle["manifest"]), [])
        f = bundle["manifest"]["files"][0]
        self.assertEqual(f["role"], "transcript")
        self.assertEqual(f["sha256"],
                         federation._sha256_bytes(bundle["files"]["transcript.jsonl"]))
        self.assertEqual(bundle["manifest"]["rewrites"]["cwd_rewrites"], 3)
        self.assertEqual(bundle["manifest"]["sidecars"]["display_name"], "my session")

    def test_non_claude_engine_is_truthful_capability_error(self):
        with self.assertRaises(federation.PeerError) as ctx:
            federation.build_transfer_bundle(
                engine="codex", session_id=SID, transcript_path="/nonexistent",
                source_cwd=SRC, dest_cwd=DST, repo_identity="x", source_node="a",
                dest_node="b", branch=None, commit=None)
        self.assertEqual(ctx.exception.kind, "unsupported_capability")

    def test_import_lands_atomically_in_encoded_slug(self):
        bundle = self._bundle()
        projects = Path(self._tmp.name) / "projects"
        result = federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"], projects_root=projects)
        self.assertTrue(result["ok"], result)
        expected_dir = projects / federation.encode_project_slug(DST)
        self.assertEqual(Path(result["transcript_path"]).parent, expected_dir)
        content = Path(result["transcript_path"]).read_bytes()
        self.assertEqual(federation._sha256_bytes(content),
                         result["transcript_sha256"])
        # Staging is cleaned up
        self.assertEqual(
            list((federation.federation_dir() / "staging").glob("*")), [])

    def test_import_rejects_bad_hash_leaves_dest_untouched(self):
        bundle = self._bundle()
        projects = Path(self._tmp.name) / "projects"
        ok = federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"], projects_root=projects)
        original = Path(ok["transcript_path"]).read_bytes()

        tampered_manifest = json.loads(json.dumps(bundle["manifest"]))
        tampered_files = {"transcript.jsonl": bundle["files"]["transcript.jsonl"] + b'{"x":1}\n'}
        # size/sha in manifest no longer match the (different) content
        result = federation.stage_and_import_bundle(
            tampered_manifest, tampered_files, projects_root=projects,
            allow_overwrite=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "hash_mismatch")
        self.assertEqual(Path(ok["transcript_path"]).read_bytes(), original)

    def test_import_identical_content_is_idempotent(self):
        bundle = self._bundle()
        projects = Path(self._tmp.name) / "projects"
        first = federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"], projects_root=projects)
        self.assertTrue(first["ok"])
        again = federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"], projects_root=projects)
        self.assertTrue(again["ok"])
        self.assertTrue(again["already_present"])

    def test_import_divergent_content_blocked_without_overwrite(self):
        bundle = self._bundle()
        projects = Path(self._tmp.name) / "projects"
        federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"], projects_root=projects)
        divergent = self._bundle(
            transcript=_transcript_bytes(
                extra_lines=({"type": "user", "cwd": SRC, "sessionId": SID,
                              "message": {"role": "user", "content": "more"}},)))
        result = federation.stage_and_import_bundle(
            divergent["manifest"], divergent["files"], projects_root=projects)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "session_exists")
        self.assertTrue(result["existing_sha256"])
        forced = federation.stage_and_import_bundle(
            divergent["manifest"], divergent["files"], projects_root=projects,
            allow_overwrite=True)
        self.assertTrue(forced["ok"])
        self.assertEqual(forced["replaced_existing_sha256"], result["existing_sha256"])

    def test_import_rejects_unsafe_session_id(self):
        bundle = self._bundle()
        bundle["manifest"]["session_id"] = "../../evil"
        result = federation.stage_and_import_bundle(
            bundle["manifest"], bundle["files"],
            projects_root=Path(self._tmp.name) / "projects")
        self.assertFalse(result["ok"])


class TestLeases(_IsolatedHome):
    def test_lease_lifecycle_and_history(self):
        self.assertIsNone(federation.read_lease(SID))
        self.assertIsNone(federation.lease_owner(SID))
        federation.write_lease(SID, "node-b", transfer_id="t1",
                               transcript_sha="s1", note="handed off")
        self.assertEqual(federation.lease_owner(SID), "node-b")
        lease = federation.write_lease(SID, "node-a", transfer_id="t2",
                                       transcript_sha="s2", note="returned")
        self.assertEqual(federation.lease_owner(SID), "node-a")
        self.assertEqual(len(lease["history"]), 2)
        self.assertEqual(lease["history"][0]["owner_node"], "node-b")

    def test_unsafe_session_id_rejected(self):
        with self.assertRaises(ValueError):
            federation.write_lease("../evil", "node-x")
        self.assertIsNone(federation.read_lease("../evil"))


if __name__ == "__main__":
    unittest.main()
