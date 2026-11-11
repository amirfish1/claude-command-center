"""Filing a ticket must not wait for WatchTower to staff the queue.

Staffing can start a worker and take minutes. When On-call waited for it, the
call timed out after the ticket existed: the report stayed unlinked, so no
"fixed" notice was ever sent for it.
"""

import os
import shutil
import urllib.error

import pytest

from ccc_server.oncall import cli
from ccc_server.oncall import wt as wtmod


def _real(tmp_path, **kwargs):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "WT_WORKER_ID", "WT_ROLE")}
    env["WATCHTOWER_DATA_DIR"] = str(tmp_path / "wt")
    return wtmod.WT([shutil.which("wt")], env=env, **kwargs)


@pytest.mark.skipif(not shutil.which("wt"), reason="WatchTower is not installed")
def test_ticket_is_filed_first_and_staffed_apart(tmp_path):
    staffed = []
    wt = _real(tmp_path, staff=lambda argv, env: staffed.append(argv))

    ref = wt.add("SHOP", title="Checkout fails", text="It fails.", dedupe_key="oncall:manual:abc")

    assert ref == "SHOP-1"
    assert len(staffed) == 1 and "--no-dispatch" not in staffed[0]
    assert staffed[0][staffed[0].index("--dedupe-key") + 1] == "oncall:manual:abc"
    # The staffing call names the same key, so it finds this ticket instead of filing another.
    assert wt.add("SHOP", title="Checkout fails", text="It fails.", dedupe_key="oncall:manual:abc") == ref
    assert [t["ref"] for t in wt.ls("SHOP")] == [ref]


@pytest.mark.skipif(not shutil.which("wt"), reason="WatchTower is not installed")
def test_no_key_or_no_dispatch_never_staffs_apart(tmp_path):
    staffed = []
    quiet = _real(tmp_path, dispatch=False, staff=lambda argv, env: staffed.append(argv))
    assert quiet.add("SHOP", title="One", text="x", dedupe_key="k1") == "SHOP-1"
    # No key: a second call would file a duplicate, so it stays one call.
    keyless = _real(tmp_path, staff=lambda argv, env: staffed.append(argv))
    assert keyless.add("SHOP", title="Two", text="y") == "SHOP-2"
    assert staffed == []


def test_a_staffing_failure_does_not_lose_the_ref():
    def run(argv, **kwargs):
        class Done:
            returncode, stdout, stderr = 0, "FILED: SHOP-7\n", ""
        assert "--no-dispatch" in argv
        return Done()

    def staff(argv, env):
        raise OSError("cannot start")

    wt = wtmod.WT(["wt"], run=run, staff=staff)
    assert wt.add("SHOP", title="t", text="x", dedupe_key="k") == "SHOP-7"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))])
def test_a_slow_answer_is_not_reported_as_ccc_not_running(error):
    def post(base, path, payload, timeout=60):
        raise error

    client = cli.Client("http://127.0.0.1:1", None, post)
    with pytest.raises(cli.Stop) as stop:
        client.post("report", {"text": "x"})
    assert str(stop.value) == cli.STILL_WORKING

    def refused(base, path, payload, timeout=60):
        raise ConnectionRefusedError()

    with pytest.raises(cli.Stop) as stop:
        cli.Client("http://127.0.0.1:1", None, refused).post("report", {"text": "x"})
    assert str(stop.value) == cli.NOT_RUNNING
