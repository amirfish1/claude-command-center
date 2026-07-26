"""Modules extracted from server.py, one subsystem per file.

Each module may reach names still living in server.py via
`import server as _core` (resolved at call time, never import time).
server.py aliases itself into sys.modules as "server" before importing
anything from this package, so these imports never re-execute server.py.

Rules: stdlib-only (same as server.py); no side effects at import beyond
def/class/constants; new subsystems start here, not in server.py.
"""
