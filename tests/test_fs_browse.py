import tempfile
import unittest
from pathlib import Path

import server


class FolderViewerListingTests(unittest.TestCase):
    def test_lists_dirs_files_and_categories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            (root / ".hidden").write_text("x")
            (root / "a.png").write_bytes(b"12345")
            (root / "b.bin").write_bytes(b"1")
            r = server._browse_folder_for_viewer(str(root))
            self.assertTrue(r["ok"])
            self.assertEqual([x["name"] for x in r["dirs"]], ["sub"])
            files = {f["name"]: f for f in r["files"]}
            self.assertEqual(set(files), {"a.png", "b.bin"})
            self.assertEqual(files["a.png"]["category"], "images")
            self.assertEqual(files["a.png"]["size"], 5)
            self.assertIsNone(files["b.bin"]["category"])

    def test_file_path_lists_parent_with_selected(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.png"
            f.write_bytes(b"1")
            r = server._browse_folder_for_viewer(str(f))
            self.assertEqual(r["selected"], "a.png")
            self.assertEqual(r["path"], str(Path(d).resolve()))

    def test_missing_path_errors(self):
        self.assertFalse(server._browse_folder_for_viewer("/nonexistent/zzz")["ok"])


if __name__ == "__main__":
    unittest.main()


def test_text_reports_stream_as_inert_text_with_existing_file_boundary():
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request

    httpd = server.ThreadedHTTPServer(('127.0.0.1', 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    repo = str(Path(server.__file__).parent)
    try:
        with tempfile.TemporaryDirectory() as folder:
            for ext in ('.txt', '.log', '.json', '.bin'):
                report = Path(folder) / ('report' + ext)
                content = '<script>window.reportExecuted=true</script>\nReport details\n'
                report.write_text(content)
                listing = server._browse_folder_for_viewer(str(report))
                assert listing['selected'] == report.name
                category = next(f['category'] for f in listing['files'] if f['name'] == report.name)
                query = urllib.parse.urlencode({'path': str(report), 'repo_path': repo})
                url = f'http://127.0.0.1:{httpd.server_port}/api/media?{query}'
                if ext == '.bin':
                    assert category is None
                    try:
                        urllib.request.urlopen(url, timeout=2)
                        raise AssertionError('unsupported binary became streamable')
                    except urllib.error.HTTPError as error:
                        assert error.code == 404
                    continue
                assert category == 'text'
                with urllib.request.urlopen(url, timeout=2) as response:
                    assert response.headers['Content-Type'] == 'text/plain; charset=utf-8'
                    assert response.headers['X-Content-Type-Options'] == 'nosniff'
                    assert response.read().decode() == content
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def test_text_preview_does_not_expand_os_opener_categories(tmp_path):
    for ext in ('.txt', '.log', '.json'):
        assert server._categorize_file_target('/tmp/report' + ext) is None
        assert server._categorize_file_target('/tmp/report' + ext, preview=True) == 'text'
        report = tmp_path / ('report' + ext)
        report.write_text('Inert report')
        assert not server._safe_local_file_open_path(report)
    for ext in ('.sh', '.py', '.app', '.command', '.bin'):
        assert server._categorize_file_target('/tmp/unsafe' + ext, preview=True) is None
