"""No function in server.py may bind the name ``_q``.

``_q`` is the module-level queue engine. A local assignment anywhere in a
function (for example ``_q = urllib.parse.parse_qs(...)``) makes ``_q`` local
for the WHOLE function, so every queue read in that function raises
UnboundLocalError. In ``do_GET`` that broke every live ticket read and the
dashboard reported tickets as "not found" (OPS-1736).
"""
import ast
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server.py"


class QNotShadowedTest(unittest.TestCase):
    def test_no_function_binds_q(self):
        tree = ast.parse(SERVER.read_text())
        offenders = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Name) and node.id == "_q"
                        and isinstance(node.ctx, (ast.Store, ast.Del))):
                    offenders.append(f"{fn.name}:{node.lineno}")
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        if alias.asname == "_q":
                            offenders.append(f"{fn.name}:{node.lineno}")
        self.assertEqual(offenders, [], "local binding of _q shadows the queue engine")


if __name__ == "__main__":
    unittest.main()
