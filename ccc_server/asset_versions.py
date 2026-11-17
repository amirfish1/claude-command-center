"""Track local static edits without repeated warm-page filesystem scans.

Linux change notifications are drained synchronously at each read; there is
no polling interval. Unsupported filesystems/platforms and watch failures keep
the strict stat path. Only the trusted static source tree uses this cache.
"""

import ctypes
import os
import re
import stat
import struct
import sys
import threading
from collections import OrderedDict


class AssetVersions:
    def __init__(self):
        self._lock = threading.Lock()
        self._root = None
        self._fd = None
        self._add_watch = None
        self._directories = set()
        self._versions = OrderedDict()
        self._files = {}

    def close(self):
        with self._lock:
            self._close()

    def _close(self):
        if self._fd is not None:
            os.close(self._fd)
        self._fd = None
        self._directories.clear()
        self._versions.clear()
        self._files.clear()

    def _open(self, root):
        self._close()
        self._root = root
        if sys.platform != 'linux' or not self._local_filesystem(root):
            return
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            initialize = libc.inotify_init1
            initialize.argtypes = (ctypes.c_int,)
            initialize.restype = ctypes.c_int
            add = libc.inotify_add_watch
            add.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
            add.restype = ctypes.c_int
            fd = initialize(os.O_NONBLOCK | os.O_CLOEXEC)
            if fd < 0:
                return
            self._fd, self._add_watch = fd, add
            self._watch_directory(root)
        except (AttributeError, OSError):
            self._close()

    @staticmethod
    def _local_filesystem(root):
        # Network filesystems can change remotely without an inotify event.
        # Restrict this optimization to local filesystems with kernel events.
        try:
            with open('/proc/self/mountinfo', encoding='utf-8') as stream:
                mounts = []
                for line in stream:
                    location, filesystem = line.split(' - ', 1)
                    directory = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), location.split()[4])
                    if root == directory or root.startswith(directory.rstrip('/') + '/'):
                        mounts.append((len(directory), filesystem.split()[0]))
            kind = max(mounts)[1] if mounts else ''
            return kind in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'tmpfs', 'ramfs', 'overlay', 'f2fs'}
        except (OSError, ValueError, IndexError):
            return False

    def _watch_directory(self, directory):
        if directory in self._directories:
            return
        # Watch the parent of a missing nested directory so creating it also
        # invalidates missing-file versions. Later reads add the new directory.
        if not os.path.isdir(directory):
            parent = os.path.dirname(directory)
            if parent != directory:
                self._watch_directory(parent)
            return
        # A symlink can be atomically replaced outside the watched tree. Keep
        # the strict path rather than caching a dependency we cannot observe.
        if os.path.realpath(directory) != directory:
            raise OSError('static directory is a symlink')
        mask = 0x00000FCE | 0x00002000  # modify/attrib/close, move/create/delete, unmount
        if self._add_watch(self._fd, os.fsencode(directory), mask) < 0:
            raise OSError(ctypes.get_errno(), 'static directory watch unavailable')
        self._directories.add(directory)

    def _drain(self):
        changed = False
        while self._fd is not None:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                self._close()
                return True
            if not data:
                self._close()
                return True
            changed = True
            offset = 0
            while offset + 16 <= len(data):
                _, mask, _, size = struct.unpack_from('iIII', data, offset)
                offset += 16 + size
                # Queue overflow, a removed watch or an unmounted directory
                # makes notifications incomplete. Fall back for this root.
                if mask & (0x00004000 | 0x00008000 | 0x00002000 | 0x00000C00):
                    self._close()
                    return True
        if changed:
            self._versions.clear()
            self._files.clear()
        return changed

    @staticmethod
    def _scan(root, names):
        versions = []
        symlink = False
        for name in names:
            candidate = os.path.join(root, name)
            try:
                info = os.lstat(candidate)
                if stat.S_ISLNK(info.st_mode):
                    symlink = True
                    info = os.stat(candidate)
                versions.append((name, info.st_mtime_ns, info.st_ctime_ns, info.st_size))
            except OSError:
                versions.append((name, None, None))
        return tuple(versions), symlink

    def read(self, root, names):
        root = os.path.abspath(root)
        names = tuple(sorted(set(names)))
        with self._lock:
            if root != self._root:
                self._open(root)
            if self._fd is not None:
                try:
                    for directory in {os.path.dirname(os.path.join(root, name)) for name in names}:
                        self._watch_directory(directory)
                except OSError:
                    self._close()
                self._drain()
            if self._fd is not None and names in self._versions:
                self._versions.move_to_end(names)
                return self._versions[names]
            known = self._files if self._fd is not None else {}
            scanned, symlink = self._scan(root, [name for name in names if name not in known])
            values = dict(known)
            values.update((info[0], info) for info in scanned)
            versions = tuple(values[name] for name in names)
            if symlink:
                self._close()
            if self._fd is not None and not self._drain():
                self._files.update((info[0], info) for info in scanned)
                if len(self._files) > 2048:
                    self._files.clear()
                self._versions[names] = versions
                while len(self._versions) > 16:
                    self._versions.popitem(last=False)
            return versions


STATIC_ASSET_VERSIONS = AssetVersions()
