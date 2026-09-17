"""Cross-platform fcntl compatibility shim.

On Unix-like platforms (Linux, macOS, BSD), this delegates transparently to the
Python standard library's C extension module `fcntl`.

On Windows (where standard library fcntl is not available), this provides no-op
stubs and POSIX constants so modules importing and using advisory `fcntl.flock`
or `fcntl.fcntl` can run without raising `ModuleNotFoundError`.
"""

import sys

if sys.platform != "win32":
    import importlib.machinery
    import importlib.util

    # Exclude sys.path[0] (this directory) to locate the real stdlib fcntl
    _stdlib_paths = [p for p in sys.path if p and p != sys.path[0]]
    _spec = importlib.machinery.PathFinder.find_spec("fcntl", _stdlib_paths)
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        for _k, _v in _mod.__dict__.items():
            if not _k.startswith("__"):
                globals()[_k] = _v
    else:
        LOCK_SH = 1
        LOCK_EX = 2
        LOCK_NB = 4
        LOCK_UN = 8
        F_GETFL = 3
        F_SETFL = 4

        def flock(fd, operation):
            pass

        def fcntl(fd, op, arg=0):
            return 0

        def ioctl(fd, op, arg=0):
            return 0
else:
    # Windows stub
    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8
    F_GETFL = 3
    F_SETFL = 4

    def flock(fd, operation):
        pass

    def fcntl(fd, op, arg=0):
        return 0

    def ioctl(fd, op, arg=0):
        return 0
