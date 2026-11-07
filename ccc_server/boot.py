# Copyright (c) 2026 Amir Fish. All rights reserved.
"""Start server.py from cached bytecode: ``python boot.py /path/server.py [args]``.

Python never caches a script's bytecode, so ``python server.py`` compiles the
whole file on every start, and the compiler's scratch memory stays in the heap
for the life of the process: about 150 MB resident against about 60 MB when the
same module is imported. server.py stays on the command line because process
discovery and uninstall match on it.
"""
import os
import sys


def _compile_stale(root, target):
    """Compile in a child process so its scratch memory dies with it."""
    import importlib.util
    import subprocess

    try:
        cached = importlib.util.cache_from_source(target)
        if os.stat(cached).st_mtime >= os.stat(target).st_mtime:
            return
    except (NotImplementedError, OSError):
        pass
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", target, os.path.join(root, "ccc_server")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )


def main():
    target = os.path.abspath(sys.argv[1])
    root = os.path.dirname(target)
    # Replace this script's directory, whose modules aren't top-level names.
    sys.path[0] = root
    sys.argv = sys.argv[1:]
    _compile_stale(root, target)
    import server
    server._script_main()


if __name__ == "__main__":
    main()
