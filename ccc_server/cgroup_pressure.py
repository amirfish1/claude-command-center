"""Attribute off-CPU stalls to the service's cgroup (CCC-713).

A pause can be long in wall time yet short in CPU time: a 1237 ms young GC
pass used 292 ms of CPU while the next identical pass took 294 ms. The rest
was spent off-CPU, and afterwards nothing could say whether the cgroup's CPU
quota throttled it, the memory.high penalty slept it, or it waited on the run
queue or on I/O.

The cgroup v2 counters say exactly that, but they are cumulative, so a stall
needs a reading from just before it started. A daemon thread keeps a short
ring of readings, one per second (five small file reads, ~0.15 ms). A slow
event takes one fresh reading when it is recorded and reports the deltas
since the newest ring reading taken before it began. Reading the files at the
start of every event instead would cost that on every young GC pass.

Linux cgroup v2 only. Anywhere else (macOS, cgroup v1, unreadable files)
every call returns None and no thread starts. Never raises.
"""
import collections
import os
import threading
import time

SAMPLE_EVERY_S = 1.0
_RING_LEN = 120

_lock = threading.Lock()
_io_lock = threading.Lock()
_ring = collections.deque(maxlen=_RING_LEN)
_state = {"dir": None, "resolved": False, "thread": None}


def _cgroup_dir():
    if _state["resolved"]:
        return _state["dir"]
    _state["resolved"] = True
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as f:
            for line in f:
                hierarchy, _, rest = line.rstrip("\n").partition(":")
                controllers, _, path = rest.partition(":")
                if hierarchy == "0" and controllers == "":
                    candidate = "/sys/fs/cgroup" + path
                    if os.path.exists(os.path.join(candidate, "cpu.stat")):
                        _state["dir"] = candidate
                    break
    except OSError:
        pass
    return _state["dir"]


_FILES = ("cpu.stat", "memory.events", "cpu.pressure", "memory.pressure", "io.pressure")
_fds = {}


def _pread(name):
    """Current contents of one cgroup file, or None. The descriptors stay open:
    re-reading a kernel seq file from offset 0 regenerates it, and pread skips
    the open/close (five files: ~0.07 ms instead of ~0.3 ms)."""
    path = os.path.join(_state["dir"], name)
    fd = _fds.get(path)
    try:
        if fd is None:
            fd = os.open(path, os.O_RDONLY)
            _fds[path] = fd
        return os.pread(fd, 4096, 0)
    except OSError:
        if fd is not None:
            _fds.pop(path, None)
            try:
                os.close(fd)
            except OSError:
                pass
        return None


def close_files():
    """Close the held descriptors (tests; a moved cgroup reopens lazily)."""
    with _io_lock:
        while _fds:
            try:
                os.close(_fds.popitem()[1])
            except OSError:
                pass


def _kv(data, key):
    for line in data.split(b"\n"):
        name, _, value = line.partition(b" ")
        if name == key:
            return int(value)
    return None


def _psi_total(data, kind):
    """``total=`` microseconds of the ``some``/``full`` line of a PSI file."""
    for line in data.split(b"\n"):
        if line.startswith(kind + b" "):
            at = line.find(b"total=")
            return int(line[at + 6:]) if at >= 0 else None
    return None


def read():
    """One cumulative reading, or None when cgroup v2 files are unavailable."""
    if not _cgroup_dir():
        return None
    try:
        with _io_lock:
            raw = {name: _pread(name) for name in _FILES}
        cpu = raw["cpu.stat"]
        if cpu is None:
            return None
        events, cpu_p, mem_p, io_p = (raw["memory.events"], raw["cpu.pressure"],
                                      raw["memory.pressure"], raw["io.pressure"])
        return {
            "t": time.monotonic(),
            "throttled_us": _kv(cpu, b"throttled_usec") or 0,
            "nr_throttled": _kv(cpu, b"nr_throttled") or 0,
            "mem_high": _kv(events, b"high") if events else None,
            "cpu_some_us": _psi_total(cpu_p, b"some") if cpu_p else None,
            "memory_some_us": _psi_total(mem_p, b"some") if mem_p else None,
            "memory_full_us": _psi_total(mem_p, b"full") if mem_p else None,
            "io_some_us": _psi_total(io_p, b"some") if io_p else None,
        }
    except ValueError:
        return None


def _sample_once():
    reading = read()
    if reading is not None:
        with _lock:
            _ring.append(reading)
    return reading


def _loop():
    while True:
        _sample_once()
        time.sleep(SAMPLE_EVERY_S)


def start_sampler():
    """Start the once-a-second sampler (idempotent). False when unsupported."""
    with _lock:
        if _state["thread"] is not None:
            return True
    if _sample_once() is None:
        return False
    with _lock:
        if _state["thread"] is not None:
            return True
        thread = threading.Thread(target=_loop, name="cgroup-pressure", daemon=True)
        _state["thread"] = thread
    thread.start()
    return True


def _delta_ms(new, old, key):
    a, b = new.get(key), old.get(key)
    if a is None or b is None:
        return None
    return round((a - b) / 1000.0, 1)


def attribution(duration_s):
    """Cgroup counter deltas covering an event that just ended after
    ``duration_s`` seconds, or None.

    The window runs from the newest ring reading taken at or before the
    event started (``lead_ms`` earlier) to now, so it covers the whole event
    plus up to ~one sampling interval before it. ``window_ms`` is the exact
    span the deltas cover: compare stall time against it, not against the
    event's own duration, when ``lead_ms`` is large.
    """
    try:
        with _lock:
            ring = list(_ring)
        if not ring:
            return None
        now = read()
        if now is None:
            return None
        start = now["t"] - max(0.0, float(duration_s))
        base = None
        for reading in reversed(ring):
            if reading["t"] <= start:
                base = reading
                break
        if base is None:
            # The event began before the oldest kept reading; the window is
            # then shorter than the event. Report it rather than guessing.
            base = ring[0]
        return {
            "window_ms": round((now["t"] - base["t"]) * 1000.0, 1),
            "lead_ms": round((start - base["t"]) * 1000.0, 1),
            "throttled_ms": _delta_ms(now, base, "throttled_us"),
            "nr_throttled": now["nr_throttled"] - base["nr_throttled"],
            "mem_high": (now["mem_high"] - base["mem_high"]
                         if now.get("mem_high") is not None
                         and base.get("mem_high") is not None else None),
            "cpu_some_ms": _delta_ms(now, base, "cpu_some_us"),
            "mem_some_ms": _delta_ms(now, base, "memory_some_us"),
            "mem_full_ms": _delta_ms(now, base, "memory_full_us"),
            "io_some_ms": _delta_ms(now, base, "io_some_us"),
        }
    except Exception:
        return None
