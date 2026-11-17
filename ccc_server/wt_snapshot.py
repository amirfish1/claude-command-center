"""Keep remote ticket-list reconstruction outside the HTTP interpreter."""

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def fetch(queue_module, timeout=90, *, snapshot_path=None):
    """Read the same queue data in a disposable interpreter, without server boot.

    A temporary stream avoids pipe backpressure and a single giant JSON decode
    under the dashboard's GIL. One record per ticket preserves all fields,
    including history used by question links and queue charts.
    """
    package_root = Path(queue_module.__file__).resolve().parent.parent
    with contextlib.ExitStack() as stack:
        output = stack.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8"))
        command = [sys.executable, str(Path(__file__).resolve()), str(package_root)]
        staged = None
        if snapshot_path is not None:
            try:
                destination = Path(snapshot_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                folder = stack.enter_context(tempfile.TemporaryDirectory(dir=destination.parent))
                staged = Path(folder) / "snapshot.json"
                command.append(str(staged))
            except OSError:
                pass
        subprocess.run(command,
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
                # The child does the expensive encoding; only this parent
                # publishes, after validating every row and the completion.
                if staged is not None:
                    with contextlib.suppress(OSError):
                        os.replace(staged, destination)
                return items
            if set(record) != {"item"} or not isinstance(record["item"], dict):
                raise ValueError("invalid ticket snapshot item")
            items.append(record["item"])
        raise ValueError("ticket snapshot completion missing")


def _persist(items, destination):
    """Publish the display copy in the child, away from the HTTP GIL.

    A unique temporary file prevents overlapping readers from sharing a
    partially written file. Persistence is optional: a full successful fetch
    can still replace the memory memo when the disk is unavailable.
    """
    temporary = None
    try:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=destination.parent, delete=False) as output:
            temporary = output.name
            output.write(json.dumps({"items": items}))
        os.replace(temporary, destination)
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


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
    if len(sys.argv) > 2:
        _persist(items, sys.argv[2])
    print(json.dumps({"items_total": len(items)}))


if __name__ == "__main__":
    _main()
