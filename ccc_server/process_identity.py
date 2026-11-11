"""Bounded, low-level process launch identity readers.

This module deliberately exposes only launch argv and two correlation IDs.
Callers must never need a process-per-candidate command or raw environment.
"""

import ctypes
import os
from pathlib import Path
import re
import struct
import subprocess
import sys


_ALLOWED_ENV = frozenset(("CCC_PARENT_SESSION_ID", "CODEX_THREAD_ID"))
_MAX_PROCARGS_BYTES = 1024 * 1024
_WRITER_SID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def _writer_session(filename, lock_dirs):
    candidate = Path(filename)
    if candidate.parent not in lock_dirs or candidate.suffix != '.lock':
        return ''
    return candidate.stem if _WRITER_SID.fullmatch(candidate.stem) else ''


def codex_writer_sessions(pids, homes):
    """Held native writer locks, grouped by PID; never infer from cwd/argv.

    A shared app-server can own several threads. Keep the whole set so callers
    cannot mistake one of its sessions for the parent of another. Linux reads
    procfs directly; macOS uses one bounded lsof call for the batch.
    """
    pids = sorted({pid for pid in pids if isinstance(pid, int) and pid > 0})
    lock_dirs = {Path(home).expanduser().resolve() / 'thread-writer-locks' for home in homes}
    if not pids or not lock_dirs:
        return {}
    result = {}
    if sys.platform.startswith('linux'):
        for pid in pids:
            try:
                descriptors = list(Path('/proc', str(pid), 'fd').iterdir())
                if len(descriptors) > 4096:
                    continue
                for descriptor in descriptors:
                    try:
                        sid = _writer_session(str(descriptor.readlink()), lock_dirs)
                        if not sid:
                            continue
                        info = Path('/proc', str(pid), 'fdinfo', descriptor.name).read_text()
                        # An open file is insufficient: the process must own
                        # the exclusive whole-file lock, not be waiting for it.
                        if re.search(r'^lock:\s+\d+: FLOCK\s+ADVISORY\s+WRITE '
                                     + str(pid) + r' \S+ 0 EOF$', info, re.M):
                            result.setdefault(pid, set()).add(sid)
                    except OSError:
                        continue
            except OSError:
                continue
    elif sys.platform == 'darwin':
        try:
            completed = subprocess.run(
                ['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(map(str, pids)), '-Fpfln'],
                capture_output=True, text=True, timeout=3, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        if completed.returncode not in (0, 1) or len(completed.stdout) > _MAX_PROCARGS_BYTES:
            return {}
        result = _parse_writer_lsof(completed.stdout, set(pids), lock_dirs)
    return result


def _parse_writer_lsof(output, pids, lock_dirs):
    result = {}
    pid, locked = None, False
    for line in output.splitlines():
        if line.startswith('p'):
            pid = int(line[1:]) if line[1:].isdigit() else None
            locked = False
        elif line.startswith('f'):
            locked = False
        elif line.startswith('l'):
            locked = line[1:] == 'W'
        elif line.startswith('n') and locked and pid in pids:
            sid = _writer_session(line[1:], lock_dirs)
            if sid:
                result.setdefault(pid, set()).add(sid)
    return result


def process_snapshot():
    """Return pid, parent pid, and birth time from one batched ``ps`` call."""
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,lstart="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode:
        return {}

    snapshot = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 7:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if pid > 0 and ppid >= 0:
            snapshot[pid] = {"ppid": ppid, "birth": " ".join(fields[2:])}
    return snapshot


def _parse_nul_fields(blob):
    """Return NUL-delimited fields only when the byte record is complete."""
    if not blob or not blob.endswith(b"\0"):
        return None
    return blob[:-1].split(b"\0")


def _allowed_environment(fields):
    environment = {}
    for field in fields:
        if not field:
            continue
        name, separator, value = field.partition(b"=")
        if not separator:
            continue
        try:
            decoded_name = name.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if decoded_name not in _ALLOWED_ENV:
            continue
        try:
            environment[decoded_name] = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return environment


def _decode_argv(fields):
    try:
        return [field.decode("utf-8") for field in fields]
    except UnicodeDecodeError:
        return None


def _parse_linux_identity(cmdline, environ):
    """Parse bounded Linux proc files without exposing their raw values."""
    argv_fields = _parse_nul_fields(cmdline)
    environment_fields = _parse_nul_fields(environ) if environ else []
    if argv_fields is None or environment_fields is None:
        return None
    argv = _decode_argv(argv_fields)
    if not argv or not argv[0]:
        return None
    return {"argv": argv, "env": _allowed_environment(environment_fields)}


def _parse_procargs2(record):
    """Parse Darwin KERN_PROCARGS2 bytes, using argc as the argv boundary."""
    int_size = struct.calcsize("=i")
    if len(record) < int_size:
        return None
    argc = struct.unpack_from("=i", record)[0]
    if argc < 1 or argc > 4096:
        return None

    offset = int_size
    executable_end = record.find(b"\0", offset)
    if executable_end < offset:
        return None
    offset = executable_end + 1
    while offset < len(record) and record[offset] == 0:
        offset += 1

    argv_fields = []
    for _ in range(argc):
        end = record.find(b"\0", offset)
        if end < offset:
            return None
        argv_fields.append(record[offset:end])
        offset = end + 1
    argv = _decode_argv(argv_fields)
    if not argv or not argv[0]:
        return None

    # The remainder is a complete NUL-delimited environment; truncated IDs
    # are not reliable correlation evidence. Empty padding is benign.
    remainder = record[offset:]
    fields = _parse_nul_fields(remainder) if remainder else []
    if fields is None:
        return None
    return {"argv": argv, "env": _allowed_environment(fields)}


def _read_darwin_procargs(pid):
    """Read KERN_PROCARGS2 through libc, bounded before allocating a buffer."""
    ctl_kern, kern_procargs2 = 1, 49
    mib = (ctypes.c_int * 3)(ctl_kern, kern_procargs2, pid)
    size = ctypes.c_size_t(0)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        sysctl = libc.sysctl
        sysctl.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t,
        ]
        sysctl.restype = ctypes.c_int
        if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value < struct.calcsize("=i") or size.value > _MAX_PROCARGS_BYTES:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value < struct.calcsize("=i") or size.value > len(buffer):
            return None
        return buffer.raw[:size.value]
    except (AttributeError, OSError):
        return None


def read_launch_identity(pid):
    """Read a process's launch identity, or return ``None`` when unreadable."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if sys.platform == "darwin":
        record = _read_darwin_procargs(pid)
        return _parse_procargs2(record) if record is not None else None
    if sys.platform.startswith("linux"):
        base = "/proc/%d" % pid
        try:
            with open(os.path.join(base, "cmdline"), "rb") as handle:
                cmdline = handle.read(_MAX_PROCARGS_BYTES + 1)
            with open(os.path.join(base, "environ"), "rb") as handle:
                environ = handle.read(_MAX_PROCARGS_BYTES + 1)
        except OSError:
            return None
        if len(cmdline) > _MAX_PROCARGS_BYTES or len(environ) > _MAX_PROCARGS_BYTES:
            return None
        return _parse_linux_identity(cmdline, environ)
    return None
