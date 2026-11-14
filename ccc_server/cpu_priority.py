"""Best-effort CPU priorities for disposable maintenance processes.

Call before importing the server or creating threads so children inherit the
priority. Interactive processes keep the priority chosen by their launcher.
"""

import os


BACKGROUND_ROLES = frozenset(("main", "archive"))


def set_background_priority(role):
    """Lower maintenance CPU priority without ever raising an inherited nice.

    CCC_BACKGROUND_NICE=0 disables the adjustment; POSIX defaults to nice 10.
    Unsupported or restricted platforms retain their existing scheduling.
    """
    if role not in BACKGROUND_ROLES:
        return False
    try:
        target = max(0, min(19, int(os.environ.get("CCC_BACKGROUND_NICE", "10"))))
    except ValueError:
        target = 10
    if not target:
        return False
    try:
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current < target:
            os.setpriority(os.PRIO_PROCESS, 0, target)
        return True
    except (AttributeError, OSError):
        return False
