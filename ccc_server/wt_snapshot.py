"""Keep remote ticket-list reconstruction outside the HTTP interpreter."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def clip_title(text, limit=60):
    text = " ".join(str(text or "").split()).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def ticket_context(item, limit=60):
    res = item.get("resolution") if isinstance(item, dict) else {}
    summary = ""
    if isinstance(res, str):
        summary = res
    elif isinstance(res, dict):
        summary = str(res.get("summary") or "")
    # Annotation titles mirror the browser document title, which is usually a
    # generic product name. Their note is the reporter's actual request and
    # therefore makes a useful worker label. Ordinary WatchTower tickets keep
    # their explicit title as the preferred context.
    annotation_id = str(item.get("annotation_id") or item.get("id") or "")
    is_annotation = annotation_id.startswith("ann-")
    values = (
        (summary, item.get("note"), item.get("title"), item.get("text"))
        if is_annotation
        else (summary, item.get("title"), item.get("note"), item.get("text"))
    )
    for value in values:
        text = clip_title(value, limit=limit)
        if text:
            return text
    return ""


def worker_title_path(snapshot_path):
    return snapshot_path.with_name(snapshot_path.stem + ".worker-titles.json")


def worker_title_generation(stat):
    return [1, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size]


def project_worker_title_items(rows):
    # Retain original ticket order and the claim evidence used by the
    # dashboard. Omit non-claim history and text superseded by a resolution.
    items = []
    for item in rows:
        if not isinstance(item, dict) or item.get("status") not in (
            "in_progress", "in_review", "closed",
        ):
            continue
        title_item = {field: item[field] for field in (
            "project", "ref", "status", "claimed_by", "claimed_session_id",
            "claimed_at", "closed_at", "updated_at", "created_at",
        ) if field in item}
        title_item["title"] = ticket_context(item, limit=0)
        history = item.get("history")
        if isinstance(history, list) and history:
            title_item["history"] = [
                {"event": "claim", "by": {"session_id": event["by"].get("session_id")}}
                if isinstance(event.get("by"), dict) else {"event": "claim"}
                for event in history if isinstance(event, dict) and event.get("event") == "claim"
            ] or [{}]
        items.append(title_item)
    return items


class _Snapshot(list):
    """Read-only display rows and their last child-output fingerprints."""

    def __init__(self):
        super().__init__()
        self.fingerprints = {}


def fetch(queue_module, timeout=90, *, snapshot_path=None, previous=None):
    """Read the same queue data in a disposable interpreter, without server boot.

    A temporary stream avoids pipe backpressure and a single giant JSON decode
    under the dashboard's GIL. One record per ticket preserves all fields,
    including history used by question links and queue charts. A refresh can
    reuse already-decoded rows from its previous read-only display snapshot.
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
        items = _Snapshot()
        prior = previous.fingerprints if isinstance(previous, _Snapshot) else {}
        for line in output:
            # The trusted local child hashes the complete encoded row,
            # including its history and resolution. Keep hashing off the
            # dashboard thread even on the first refresh.
            framed = line[64:65] == "\t"
            fingerprint = line[:64] if framed else None
            if framed and fingerprint in prior and fingerprint not in items.fingerprints:
                item = prior[fingerprint]
                items.fingerprints[fingerprint] = item
                items.append(item)
                continue
            record = json.loads(line[65:] if framed else line)
            if not isinstance(record, dict):
                raise ValueError("invalid ticket snapshot record")
            if not framed and "items_total" in record:
                if record != {"items_total": len(items)} or output.read():
                    raise ValueError("incomplete ticket snapshot")
                # The child does the expensive encoding; only this parent
                # publishes, after validating every row and the completion.
                if staged is not None:
                    try:
                        generation = staged.stat()
                        os.replace(staged, destination)
                    except OSError:
                        pass
                    else:
                        with contextlib.suppress(OSError):
                            os.replace(worker_title_path(staged), worker_title_path(destination))
                        # Alert polls need only status fields. Associate them
                        # with the published inode to avoid re-decoding history.
                        from ccc_server.wt_alerts import remember_ticket_snapshot
                        remember_ticket_snapshot(destination, generation, items)
                if isinstance(previous, _Snapshot) and len(previous) == len(items) and all(
                    old is new for old, new in zip(previous, items)
                ):
                    return previous
                return items
            if not framed or set(record) != {"item"} or not isinstance(record["item"], dict):
                raise ValueError("invalid ticket snapshot item")
            item = record["item"]
            items.fingerprints.setdefault(fingerprint, item)
            items.append(item)
        raise ValueError("ticket snapshot completion missing")


def _persist(items, destination):
    destination = Path(destination)
    stamp = _persist_data({"items": items}, destination)
    if stamp is not None:
        _persist_data({"key": worker_title_generation(stamp),
                       "items": project_worker_title_items(items)},
                      worker_title_path(destination))


def _persist_data(data, destination):
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
            output.write(json.dumps(data, separators=(",", ":")))
        stamp = os.stat(temporary)
        os.replace(temporary, destination)
        return stamp
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


def _write_row(output, item):
    encoded = json.dumps({"item": item}, separators=(",", ":"))
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    output.write(f"{fingerprint}\t{encoded}\n")


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
    output = sys.stdout
    for item in items:
        _write_row(output, item)
    if len(sys.argv) > 2:
        _persist(items, sys.argv[2])
    output.write(json.dumps({"items_total": len(items)}) + "\n")


if __name__ == "__main__":
    _main()
