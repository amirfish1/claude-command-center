"""Source-only entrypoint for an immutable, isolated execution profile.

The rootfs, mounts, entrypoint and environment are enforced independently by
the root Docker authority. This guard prevents writable state from becoming
Python source/bytecode and restricts subprocesses to the measured profile.
It is not a sandbox for hostile code already included in an approved image.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
from pathlib import Path
import signal
import sys
import types


EXCLUDED = ("/state", "/tmp", "/run", "/proc", "/sys", "/dev")
EXCLUDED_FILES = {"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}
DATA_ROOTS = ("/state", "/tmp", "/run/data")


def install_kernel_policy():
    """After immutable Python bootstrap, deny every further OS executable.

    This deliberately supports a Python client/control process, not arbitrary
    native subprocess profiles. Forked children inherit the filter. Optional
    native tools require a separate measured broker; unknown is not permission.
    """
    import ctypes
    architecture = os.uname().machine
    policies = {"x86_64": (0xc000003e, 56, (59, 322, 101, 311, 319, 165, 166, 272, 308, 155, 321,
                                          428, 429, 430, 431, 432, 442)),
                "aarch64": (0xc00000b7, 220, (221, 281, 117, 271, 279, 40, 39, 97, 268, 41, 280,
                                              428, 429, 430, 431, 432, 442))}
    if architecture not in policies:
        raise RuntimeError("execution refused: unsupported kernel architecture")
    arch, clone, denied = policies[architecture]

    class Instruction(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Instruction))]

    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
                    (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x50001)]
    # No new mount/user/PID/etc namespaces. clone3 has pointer arguments BPF
    # cannot inspect: return ENOSYS so libc safely falls back to plain clone.
    instructions.extend([(0x15, 0, 1, 435), (0x06, 0, 0, 0x50026),
                         (0x15, 0, 3, clone), (0x20, 0, 0, 16),
                         (0x45, 0, 1, 0x7e020000), (0x06, 0, 0, 0x50001),
                         (0x20, 0, 0, 0)])
    for syscall in denied:
        instructions.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x50001)])
    instructions.append((0x06, 0, 0, 0x7fff0000))
    buffer = (Instruction * len(instructions))(*(Instruction(*values) for values in instructions))
    program = Program(len(instructions), buffer)
    libc = ctypes.CDLL(None, use_errno=True)
    if (libc.prctl(38, 1, 0, 0, 0) or libc.prctl(4, 0, 0, 0, 0)
            or libc.prctl(22, 2, ctypes.byref(program), 0, 0)):
        raise OSError(ctypes.get_errno(), "execution refused: seccomp installation failed")
    for name in list(sys.modules):
        if name == "ctypes" or name.startswith("ctypes.") or name == "_ctypes":
            del sys.modules[name]


def measured_filename(filename):
    if not isinstance(filename, str) or not filename.startswith("/"):
        raise RuntimeError("execution refused: non-file source")
    resolved = os.path.realpath(filename)
    if resolved in EXCLUDED_FILES or any(resolved == root or resolved.startswith(root + "/")
                                         for root in EXCLUDED):
        raise RuntimeError("execution refused: source from writable/runtime data")
    if not os.path.isfile(resolved):
        raise RuntimeError("execution refused: missing source")
    return resolved


class SourceGuard:
    def __init__(self, commands, generated_sources=(), unix_sockets=(), state_files=()):
        self.commands = {tuple(command) for command in commands}
        self.code = set()
        self.generated_sources = set(generated_sources)
        self._deriving = False
        self.unix_sockets = set(unix_sockets)
        self.state_files = set(state_files)

    def register(self, code):
        self.code.add(code)
        for child in code.co_consts:
            if isinstance(child, types.CodeType):
                self.register(child)
        return code

    def compile_file(self, filename):
        filename = measured_filename(filename)
        return self.register(compile(Path(filename).read_bytes(), filename, "exec", dont_inherit=True))

    def audit(self, event, args):
        if event == "open":
            filename, _, flags = args
            if isinstance(filename, (str, bytes)):
                if not os.path.isabs(filename):
                    raise RuntimeError("execution refused: unmeasured relative file access")
                filename = os.path.realpath(os.fsdecode(filename))
                writing_only = flags & os.O_ACCMODE == os.O_WRONLY
                if any(filename == root or filename.startswith(root + "/") for root in DATA_ROOTS):
                    if not writing_only and filename not in self.state_files:
                        raise RuntimeError("execution refused: undeclared mutable input/configuration")
                elif filename.startswith(("/proc/", "/sys/", "/dev/")):
                    if filename.endswith("/mem") or (flags & os.O_ACCMODE != os.O_RDONLY and filename != "/dev/null"):
                        raise RuntimeError("execution refused: unsafe runtime file access")
        elif event == "compile":
            source, filename = args
            if self._deriving:
                return
            if isinstance(source, str):
                source = source.encode("utf-8")
            if (filename, source) in self.generated_sources:
                # Pre-measured generated source, e.g. a fixed dataclass body.
                # Never permit an arbitrary stdlib factory or caller frame.
                self._deriving = True
                try:
                    for mode in ("exec", "eval", "single"):
                        try:
                            self.register(compile(source, filename, mode, dont_inherit=True))
                        except SyntaxError:
                            pass
                finally:
                    self._deriving = False
                return
            filename = measured_filename(filename)
            if source != Path(filename).read_bytes():
                raise RuntimeError("execution refused: source differs from artifact")
        elif event == "exec":
            code = args[0]
            if code not in self.code and not code.co_filename.startswith("<frozen "):
                raise RuntimeError("execution refused: unmeasured code object")
        elif event in ("marshal.loads", "marshal.load", "code.__new__", "function.__new__"):
            raise RuntimeError("execution refused: unmeasured bytecode/code injection")
        elif event == "import":
            name, filename = args[:2]
            if name in ("ctypes", "_ctypes"):
                raise RuntimeError("execution refused: dynamic native loader")
            if filename:
                measured_filename(filename)
        elif event.startswith("ctypes.") or event in ("os.system", "os.posix_spawn", "pty.spawn"):
            raise RuntimeError("execution refused: unmeasured execution mechanism")
        elif event == "subprocess.Popen":
            executable, argv, _, env = args
            if tuple(argv) not in self.commands or executable != argv[0]:
                raise RuntimeError("execution refused: unmeasured subprocess argv")
            measured_filename(executable)
            if env is not None and dict(env) != dict(os.environ):
                raise RuntimeError("execution refused: subprocess environment drift")
        elif event == "os.exec":
            raise RuntimeError("execution refused: process identity replacement")
        elif event in ("os.putenv", "os.unsetenv"):
            raise RuntimeError("execution refused: runtime configuration mutation")
        elif event == "socket.connect":
            connection, address = args
            if connection.family == 1 and address not in self.unix_sockets:
                raise RuntimeError("execution refused: undeclared Unix socket")

    def install(self):
        guard = self

        class SourceLoader(importlib.machinery.SourceFileLoader):
            def get_code(self, fullname):
                return guard.compile_file(self.path)

        class Finder:
            @staticmethod
            def find_spec(fullname, path=None, target=None):
                if fullname in ("ctypes", "_ctypes"):
                    raise ImportError("unmeasured dynamic loader")
                for directory in path if path is not None else sys.path:
                    # Zip/custom importers and writable plugin paths are not
                    # supported. Missing default stdlib zip paths are removed.
                    if not os.path.isdir(directory):
                        raise ImportError("unmeasured import search path")
                    resolved = os.path.realpath(directory)
                    if any(resolved == root or resolved.startswith(root + "/") for root in EXCLUDED):
                        raise ImportError("unmeasured import search path")
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                if spec is None:
                    return None
                if spec.origin is None:  # Namespace packages permit path injection.
                    raise ImportError("unmeasured namespace package")
                measured_filename(spec.origin)
                if isinstance(spec.loader, importlib.machinery.SourceFileLoader):
                    spec.loader = SourceLoader(fullname, spec.origin)
                elif isinstance(spec.loader, importlib.machinery.ExtensionFileLoader):
                    if (not re.search(r"/lib/python[0-9.]+/lib-dynload/", spec.origin)
                            or fullname.startswith(("_test", "_xx", "_ctypes"))):
                        raise ImportError("unmeasurable native plugin/code-generation closure")
                else:
                    raise ImportError("unmeasured bytecode/custom import loader")
                return spec

        sys.path[:] = [p for p in sys.path if os.path.isdir(p)]
        sys.meta_path[:] = [importlib.machinery.BuiltinImporter,
                            importlib.machinery.FrozenImporter, Finder]
        sys.path_hooks[:] = [importlib.machinery.FileFinder.path_hook(
            (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
            (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES))]
        sys.path_importer_cache.clear()
        sys.addaudithook(self.audit)


def main():
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise RuntimeError("execution refused: isolated interpreter flags required")
    sys.path.insert(0, "/opt/ccc")
    from ccc_server.execution_artifact import ExecutionProfile, canonical
    value = json.loads(sys.argv[1])
    profile = ExecutionProfile(value["entry"], tuple(value["argv"]),
                               tuple(tuple(p) for p in value["environment"]),
                               tuple(tuple(p) for p in value["data_mounts"]),
                               tuple(tuple(p) for p in value["commands"]),
                               value["network"], value["user"],
                               tuple(tuple(p) for p in value["generated_sources"]),
                               tuple(value["unix_sockets"]), tuple(value["state_files"]))
    if canonical(profile.value()) != canonical(value):
        raise RuntimeError("execution refused: unknown execution profile")
    # No application code executes before installation. Pre-imported modules
    # above come exclusively from the immutable interpreter/base/source image.
    generated = [(filename, Path(measured_filename(source)).read_bytes())
                 for filename, source in profile.generated_sources]
    install_kernel_policy()
    # Namespace PID 1 ignores an unhandled self-SIGSTOP. Keep an immutable
    # guardian as PID 1 and run application admission in its one child. The
    # root verifier binds both PID/start identities; both inherit the filter.
    child = os.fork()
    if child:
        descriptor = os.pidfd_open(child)
        try:
            def forward(number, _frame):
                try:
                    signal.pidfd_send_signal(descriptor, number)
                except ProcessLookupError:
                    pass
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(number, forward)
            _, status = os.waitpid(child, 0)
            raise SystemExit(os.waitstatus_to_exitcode(status))
        finally:
            os.close(descriptor)
    guard = SourceGuard(profile.commands, generated, profile.unix_sockets, profile.state_files)
    guard.install()
    # Every launch begins behind this kernel admission stop. A root authority
    # re-observes the exact prepared identity before the coordinator releases
    # it. No application/recovery code has executed at this point.
    os.kill(os.getpid(), signal.SIGSTOP)
    sys.argv[:] = [profile.entry, *profile.argv]
    namespace = {"__name__": "__main__", "__file__": profile.entry,
                 "__package__": None, "__builtins__": __builtins__}
    exec(guard.compile_file(profile.entry), namespace)


if __name__ == "__main__":
    main()
