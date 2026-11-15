import os
import tempfile

# The Codex restart fence (CCC-250) must never touch the live worker's, under
# unittest discovery too (conftest.py only runs under pytest).
os.environ.setdefault(
    "CCC_CODEX_RESTART_FENCE",
    os.path.join(tempfile.mkdtemp(prefix="ccc-test-fence-"), "codex-restart-fence.json"),
)
