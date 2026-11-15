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
from pathlib import Path
import runpy
import signal
import sys
import types


EXCLUDED = ("/state", "/tmp", "/run", "/proc", "/sys", "/dev")
EXCLUDED_FILES = {"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}


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
    def __init__(self, commands, generated_sources=()):
        self.commands = {tuple(command) for command in commands}
        self.code = set()
        self.generated_sources = set(generated_sources)
        self._deriving = False

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
        if event == "compile":
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
                elif not isinstance(spec.loader, importlib.machinery.ExtensionFileLoader):
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
                               tuple(tuple(p) for p in value["generated_sources"]))
    if canonical(profile.value()) != canonical(value):
        raise RuntimeError("execution refused: unknown execution profile")
    # No application code executes before installation. Pre-imported modules
    # above come exclusively from the immutable interpreter/base/source image.
    generated = [(filename, Path(measured_filename(source)).read_bytes())
                 for filename, source in profile.generated_sources]
    guard = SourceGuard(profile.commands, generated)
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
