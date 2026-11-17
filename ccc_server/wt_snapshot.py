"""Keep remote ticket-list reconstruction outside the HTTP interpreter."""

import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def fetch(queue_module, timeout=90):
    """Read the same queue data in a disposable interpreter, without server boot.

    A temporary stream avoids pipe backpressure and a single giant JSON decode
    under the dashboard's GIL. One record per ticket preserves all fields,
    including history used by question links and queue charts.
    """
    package_root = Path(queue_module.__file__).resolve().parent.parent
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), str(package_root)],
                       stdout=output, stderr=subprocess.DEVNULL, check=True,
                       timeout=timeout)
        output.seek(0)
        items = []
        for line in output:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("invalid ticket snapshot record")
            if "items_total" in record:
                if record != {"items_total": len(items)} or output.read():
                    raise ValueError("incomplete ticket snapshot")
                return items
            if set(record) != {"item"} or not isinstance(record["item"], dict):
                raise ValueError("invalid ticket snapshot item")
            items.append(record["item"])
        raise ValueError("ticket snapshot completion missing")


def _main():
    # Resolve the exact package already imported by the parent, including an
    # editable install not on the child's default import path.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ccc_server.cpu_priority import set_background_priority
    set_background_priority("main")
    sys.path.insert(0, sys.argv[1])
    with contextlib.redirect_stdout(sys.stderr):
        from watchtower import queue
        items = queue.list_items() or []
    for item in items:
        print(json.dumps({"item": item}, separators=(",", ":")))
    print(json.dumps({"items_total": len(items)}))


if __name__ == "__main__":
    _main()
