"""Immutable OCI execution closure and root-Docker attestation primitives.

This module does NOT select a restart barrier. A receipt is useful only after
the caller compares it with an explicitly accepted, exact tested transition.
Docker's root daemon and the host kernel are the trust base. Image labels,
container environment claims, mutable checkout hashes and caller JSON are not.
"""

from dataclasses import dataclass
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile


class Refused(ValueError):
    """An unknown or unenforceable execution binding; retain refusal."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _relative(name):
    if not isinstance(name, str) or not name or "\x00" in name:
        raise Refused("invalid artifact path")
    name = name.rstrip("/")
    if name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/")):
        raise Refused("noncanonical or escaping artifact path: " + name)
    name.encode("utf-8", "strict")
    return name


def _target(name, target):
    if not target or "\x00" in target:
        raise Refused("empty link target")
    # Preserve intermediate components: a missing/link parent before '..' is
    # meaningful to the kernel and must not be hidden by lexical normpath.
    return target.lstrip("/") if target.startswith("/") else posixpath.join(posixpath.dirname(name), target)


def _resolve(entries, name, seen=()):
    resolved, remaining, links = [], name.split("/"), 0
    while remaining:
        part = remaining.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                raise Refused("link escapes artifact: " + name)
            resolved.pop()
            continue
        prefix = "/".join([*resolved, part])
        item = entries.get(prefix)
        if item is None:
            raise Refused("missing artifact entry: " + prefix)
        if item["type"] == "symlink":
            links += 1
            if links > 40:
                raise Refused("cyclic artifact link: " + name)
            target = item["target"]
            if target.startswith("/"):
                resolved = []
            remaining = target.split("/") + remaining
        else:
            if remaining and item["type"] != "directory":
                raise Refused("non-directory artifact ancestor: " + prefix)
            resolved.append(part)
    return "/".join(resolved)


def manifest_from_tar(fileobj, *, verify_links=True):
    """Canonical whole rootfs, including every dependency, never just CCC files.

    Tar ownership/timestamps are not executable identity; full permission bits
    are. Devices, FIFOs, duplicates, xattrs and unresolved links are refused.
    Absolute symlinks are interpreted relative to the container root.
    """
    entries = {}
    hardlinks = {}
    with tarfile.open(fileobj=fileobj, mode="r:*") as archive:
        for member in archive:
            if member.name in (".", "./"):
                continue
            name = _relative(member.name.removeprefix("./"))
            if name in entries:
                raise Refused("duplicate artifact entry: " + name)
            if any("xattr" in key for key in member.pax_headers):
                raise Refused("unmeasured extended attributes: " + name)
            item = {"path": name, "mode": member.mode & 0o7777}
            if member.isfile():
                stream = archive.extractfile(member)
                sha = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
                item.update(type="file", sha256=sha.hexdigest(), size=member.size)
            elif member.isdir():
                item["type"] = "directory"
            elif member.issym():
                item.update(type="symlink", target=member.linkname)
            elif member.islnk():
                item["type"] = "hardlink"
                hardlinks[name] = _relative(member.linkname)
            else:
                raise Refused("unsupported artifact file type: " + name)
            entries[name] = item
    # Resolve tar hardlinks before symlinks; their target must be a regular file.
    def hardlink(name, seen=()):
        if name in seen:
            raise Refused("cyclic hardlink")
        target = entries.get(hardlinks[name])
        if target is None:
            raise Refused("missing hardlink target")
        if target["type"] == "hardlink":
            hardlink(target["path"], seen + (name,))
        if target["type"] != "file" or target["mode"] != entries[name]["mode"]:
            raise Refused("unsupported hardlink target/mode")
        entries[name].update(type="file", sha256=target["sha256"], size=target["size"])
    for name in hardlinks:
        hardlink(name)
    if verify_links:
        for name, entry in entries.items():
            _resolve(entries, name)
            if entry["type"] == "symlink":
                _resolve(entries, _target(name, entry["target"]))
    return canonical({"format": "ccc-execution-rootfs-v1",
                      "entries": [entries[name] for name in sorted(entries)]})


@dataclass(frozen=True)
class Artifact:
    image_id: str
    executed_build_sha256: str
    manifest: bytes


def manifest_from_saved_image(data, image_id):
    """Measure the stored image, not Docker's mutable initialization layer.

    Builder artifacts are deliberately flattened single-layer imports. Unknown
    layered/whiteout layouts refuse rather than partially applying OCI layers.
    Verify both config identity and its uncompressed layer digest.
    """
    with tarfile.open(fileobj=io.BytesIO(data)) as saved:
        manifests = json.load(saved.extractfile("manifest.json"))
        if len(manifests) != 1 or len(manifests[0]["Layers"]) != 1:
            raise Refused("execution artifact must be a flattened single-layer image")
        record = manifests[0]
        config_bytes = saved.extractfile(record["Config"]).read()
        config = json.loads(config_bytes)
        if config.get("rootfs", {}).get("type") != "layers":
            raise Refused("unsupported image rootfs format")
        layer = saved.extractfile(record["Layers"][0]).read()
        if "sha256:" + digest(config_bytes) != image_id:
            # Docker 29/containerd uses the OCI manifest digest as Image.Id;
            # older engines use the config digest. Verify the entire chain.
            root = saved.extractfile("blobs/sha256/" + image_id.removeprefix("sha256:")).read()
            if "sha256:" + digest(root) != image_id:
                raise Refused("saved OCI manifest differs from pinned image ID")
            descriptor = json.loads(root)
            if descriptor.get("schemaVersion") != 2:
                raise Refused("unsupported OCI manifest format")
            if descriptor["config"]["digest"] != "sha256:" + digest(config_bytes):
                raise Refused("saved config differs from OCI manifest")
            if len(descriptor["layers"]) != 1 or descriptor["layers"][0]["digest"] != "sha256:" + digest(layer):
                raise Refused("saved layer differs from OCI manifest")
            media = descriptor["layers"][0]["mediaType"]
            if media.endswith("+gzip") or media.endswith(".gzip"):
                layer = gzip.decompress(layer)
            elif not media.endswith(".tar"):
                raise Refused("unsupported execution layer compression")
        if config["rootfs"]["diff_ids"] != ["sha256:" + digest(layer)]:
            raise Refused("saved layer differs from content-addressed config")
        with tarfile.open(fileobj=io.BytesIO(layer)) as archive:
            if any(posixpath.basename(member.name).startswith(".wh.") for member in archive):
                raise Refused("unexpected whiteout in flattened execution artifact")
        return manifest_from_tar(io.BytesIO(layer))


def image_etc_mount(image_id):
    # Docker 29's overlayfs image-mount ID hex-encodes the container ID and
    # source. Full IDs exceed NAME_MAX. Unique digest lookup is resolved back
    # to the exact full image ID by the root verifier before execution.
    return {"Type": "image", "Source": image_id[:31], "Target": "/etc", "ReadOnly": True,
            "ImageOptions": {"Subpath": "opt/ccc-runtime/etc"}}


def runtime_mount_snapshot(container):
    mounts = copy.deepcopy(container.get("Mounts", []))
    for mount in mounts:
        if mount["Type"] == "image" and mount["Destination"] == "/etc":
            # Root-daemon backing paths contain this container's ID. That is
            # an identity field, not safety policy. Content is independently
            # pinned/verified against the full immutable source image.
            mount["Source"] = container["Image"] + ":opt/ccc-runtime/etc"
            mount["Name"] = container["Image"]
    return sorted(mounts, key=lambda m: m["Destination"])


def committed_source_tar(repo, revision):
    """Read every tracked blob; mutable/export-ignore attributes cannot filter it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    command = ["git", "--no-replace-objects", "-C", str(repo)]
    records = subprocess.check_output([*command, "ls-tree", "-r", "-z", revision], env=env).split(b"\0")
    files = []
    for record in records:
        if not record:
            continue
        header, filename = record.split(b"\t", 1)
        mode, kind, sha = header.decode("ascii").split()
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise Refused("submodule or unsupported committed source type")
        files.append((_relative(filename.decode("utf-8")), mode, sha))
    blobs = io.BytesIO(subprocess.check_output([*command, "cat-file", "--batch"],
                                               input="".join(sha + "\n" for _, _, sha in files).encode(), env=env))
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        directories = sorted({str(parent) for name, _, _ in files for parent in Path(name).parents if str(parent) != "."})
        for name in directories:
            item = tarfile.TarInfo(name)
            item.type, item.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(item)
        for name, mode, expected_sha in files:
            sha, kind, size = blobs.readline().decode("ascii").split()
            payload = blobs.read(int(size))
            if sha != expected_sha or kind != "blob" or blobs.read(1) != b"\n":
                raise Refused("committed source blob identity changed")
            if hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest() != sha:
                raise Refused("committed source blob content does not match SHA")
            item = tarfile.TarInfo(name)
            item.mode = 0o755 if mode == "100755" else 0o644
            if mode == "120000":
                item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, payload.decode("utf-8")
                archive.addfile(item)
            else:
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))
    return output.getvalue()


def committed_dependency_tar(package, repo, revision):
    """Measure one committed import tree; never install or copy host packages."""
    if (not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', package)
            or package in sys.stdlib_module_names or package in ('ccc_server', 'server')):
        raise Refused('dependency must be a distinct non-stdlib package')
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise Refused('dependency requires a full committed SHA')
    source = committed_source_tar(repo, revision)
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source)) as tracked, tarfile.open(fileobj=output, mode='w') as target:
        selected = [item for item in tracked if item.name == package or item.name.startswith(package + '/')]
        if not any(item.name == package + '/__init__.py' and item.isfile() for item in selected):
            raise Refused('dependency package has no committed source initializer')
        for item in selected:
            target.addfile(item, tracked.extractfile(item) if item.isfile() else None)
    return output.getvalue()


class DockerImages:
    """Root Docker CLI adapter. Builds/export never start an agent or container."""

    def command(self, *args, input=None):
        result = subprocess.run(["docker", *args], input=input, capture_output=True,
                                check=False)
        if result.returncode:
            raise Refused("Docker command failed: " + result.stderr.decode(errors="replace")[:500])
        return result.stdout

    def inspect_image(self, image):
        return json.loads(self.command("image", "inspect", image))[0]

    def export(self, image):
        ident = self.command("create", "--entrypoint", "/unused", image).decode().strip()
        try:
            return self.command("export", ident)
        finally:
            self.command("rm", ident)

    def measure(self, image):
        info = self.inspect_image(image)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", info["Id"]):
            raise Refused("Docker returned an unpinned image")
        if info.get("Config", {}).get("Volumes"):
            raise Refused("image declares unmeasured volumes")
        manifest = manifest_from_saved_image(self.command("image", "save", info["Id"]), info["Id"])
        return Artifact(info["Id"], digest(manifest), manifest)


def build_artifact(base_digest, repo, revision, *, prune_base_links=(), dependencies=(), docker=None):
    """Import a complete pinned base plus committed source, without RUN/network.

    Slim distributions sometimes contain dangling documentation/runtime links.
    Each removed *base* link must be named explicitly; source links are never
    silently dropped. The returned manifest describes the actual resulting
    image, not an intended Dockerfile or a mutable working tree.
    """
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", base_digest):
        raise Refused("base must be pinned by registry digest")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Refused("source revision must be a full committed SHA")
    docker = docker or DockerImages()
    base = docker.inspect_image(base_digest)
    if base_digest not in base.get("RepoDigests", []):
        raise Refused("base registry digest not verified by Docker")
    if base.get("Config", {}).get("Volumes"):
        raise Refused("base declares external volumes")
    source = committed_source_tar(repo, revision)
    dependency_tars = []
    names = set()
    for package, dependency_repo, dependency_revision in dependencies:
        if package in names:
            raise Refused("duplicate dependency package")
        names.add(package)
        dependency_tars.append(committed_dependency_tar(package, dependency_repo, dependency_revision))
    base_bytes = docker.export(base["Id"])
    prune = {_relative(name) for name in prune_base_links}
    with tempfile.TemporaryFile() as output:
        with tarfile.open(fileobj=output, mode="w") as target:
            with tarfile.open(fileobj=io.BytesIO(base_bytes)) as original:
                members = {m.name.rstrip("/"): m for m in original}
                base_entries = {name: {"type": "symlink" if item.issym() else
                                      "directory" if item.isdir() else "file",
                                      "target": item.linkname}
                                for name, item in members.items()}
                for name in prune:
                    item = members.get(name)
                    if not item or not item.issym():
                        raise Refused("pruning requires an explicitly missing base link: " + name)
                    try:
                        _resolve(base_entries, name)
                    except Refused as exc:
                        if not str(exc).startswith("missing artifact entry:"):
                            raise
                    else:
                        raise Refused("pruning requires an explicitly missing base link: " + name)
                for name, item in members.items():
                    if name not in prune:
                        if (name in ("opt/ccc", "opt/ccc-deps")
                                or name.startswith(("opt/ccc/", "opt/ccc-deps/"))):
                            raise Refused("base shadows source destination")
                        target.addfile(item, original.extractfile(item) if item.isfile() else None)
                if "etc/mtab" in prune:
                    # Docker recreates a missing mtab as an unresolved link to
                    # runtime /proc. An empty measured regular table prevents
                    # that implicit mutation; mount checks use kernel procfs.
                    item = tarfile.TarInfo("etc/mtab")
                    item.mode = 0o644
                    target.addfile(item)
                if "etc" not in members or "opt/ccc-runtime" in members:
                    raise Refused("base lacks etc or shadows runtime snapshot")
                for name in ("opt", "opt/ccc-runtime"):
                    if name not in members:
                        item = tarfile.TarInfo(name)
                        item.type, item.mode = tarfile.DIRTYPE, 0o755
                        target.addfile(item)
                for name, original_item in members.items():
                    if (name == "etc" or name.startswith("etc/")) and name not in prune:
                        item = copy.copy(original_item)
                        item.name = "opt/ccc-runtime/" + name
                        item.pax_headers = {k: v for k, v in item.pax_headers.items() if k not in ("path", "linkpath")}
                        target.addfile(item, original.extractfile(original_item) if item.isfile() else None)
                aliases = set()
                for name, item in members.items():
                    if name.startswith("etc/") and item.issym() and name not in prune and not item.linkname.startswith("/"):
                        destination = posixpath.normpath(_target(name, item.linkname))
                        first = destination.split("/", 1)[0]
                        if first != "etc":
                            _resolve(base_entries, name)
                            aliases.add(first)
                for name in sorted(aliases):
                    item = tarfile.TarInfo("opt/ccc-runtime/" + name)
                    item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, "/" + name
                    target.addfile(item)
                if "etc/mtab" in prune:
                    item = tarfile.TarInfo("opt/ccc-runtime/etc/mtab")
                    item.mode = 0o644
                    target.addfile(item)
            for name in ("opt", "opt/ccc"):
                if name not in members and name != "opt":
                    item = tarfile.TarInfo(name)
                    item.type, item.mode = tarfile.DIRTYPE, 0o755
                    target.addfile(item)
            with tarfile.open(fileobj=io.BytesIO(source)) as tracked:
                for item in tracked:
                    item.name = "opt/ccc/" + _relative(item.name)
                    item.pax_headers = {k: v for k, v in item.pax_headers.items() if k not in ("path", "linkpath")}
                    if item.islnk():
                        item.linkname = "opt/ccc/" + _relative(item.linkname)
                    item.uid = item.gid = 0
                    target.addfile(item, tracked.extractfile(item) if item.isfile() else None)
            if dependency_tars:
                item = tarfile.TarInfo('opt/ccc-deps')
                item.type, item.mode = tarfile.DIRTYPE, 0o755
                target.addfile(item)
                for dependency in dependency_tars:
                    with tarfile.open(fileobj=io.BytesIO(dependency)) as tracked:
                        for item in tracked:
                            item.name = 'opt/ccc-deps/' + _relative(item.name)
                            item.uid = item.gid = 0
                            target.addfile(item, tracked.extractfile(item) if item.isfile() else None)
        output.seek(0)
        manifest = manifest_from_tar(output)
        output.seek(0)
        image_id = docker.command("import", "-", input=output.read()).decode().strip()
    try:
        measured = docker.measure(image_id)
        if measured.manifest != manifest:
            raise Refused("imported image differs from canonical build")
    except BaseException:
        docker.command("image", "rm", image_id)
        raise
    return measured


_DIGEST = re.compile(r"[0-9a-f]{64}")
_DATA_ROOTS = ("/state", "/tmp", "/run/data")


@dataclass(frozen=True)
class ExecutionProfile:
    """No wildcard imports, shell commands, host code, or secret config hashes.

    Native subprocesses are kernel-denied; nonempty command policies refuse.
    Data mounts are allowed only at the fixed non-code roots and require
    kernel-confirmed noexec,nosuid,nodev. Network namespaces can be shared with
    an explicitly pinned sibling whose own network mode is none.
    """
    entry: str
    argv: tuple = ()
    environment: tuple = ()
    data_mounts: tuple = ()  # (source, destination, read_only)
    commands: tuple = ()    # Reserved for a measured broker; must remain empty.
    network: str = "none"
    user: str = "65532:65532"
    generated_sources: tuple = ()  # (compiler filename, measured source file)
    unix_sockets: tuple = ()  # Explicit private data sockets; peer proof separate.
    state_files: tuple = ()  # Exact mutable ledger/input files, never configuration.
    identity_namespace: str = "private"  # Explicit kernel PID and cgroup visibility.

    def value(self):
        if not self.entry.startswith("/opt/ccc/") or "/../" in self.entry:
            raise Refused("entry must be measured CCC source")
        if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", self.user):
            raise Refused("worker must be an unprivileged numeric user")
        if self.network != "none" and not re.fullmatch(r"container:[0-9a-f]{64}", self.network):
            raise Refused("network must be none or a pinned isolated sibling")
        if self.identity_namespace not in ("private", "host"):
            raise Refused("identity namespace must be private or host")
        env = dict(self.environment)
        if len(env) != len(self.environment):
            raise Refused("duplicate environment variable")
        # A secret may be supplied separately only under this deliberately
        # narrow namespace. It cannot choose Python/loader/plugin configuration.
        for key, val in env.items():
            if key.startswith("CCC_CREDENTIAL_"):
                raise Refused("secret values must not appear in a profile")
            if any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
                raise Refused("credentials require the excluded credential namespace")
            if key.startswith(("PYTHON", "LD_", "DYLD_")) or key in ("PATH", "ENV", "BASH_ENV"):
                raise Refused("unmeasured interpreter/loader configuration")
            if not isinstance(val, str):
                raise Refused("non-string environment value")
            if val.startswith(_DATA_ROOTS) and key != "CCC_STATE_DIR":
                raise Refused("mutable environment-backed configuration is unsupported")
            if ".." in val.split("/"):
                raise Refused("unmeasurable environment path traversal")
        destinations = set()
        for source, destination, read_only in self.data_mounts:
            if destination not in _DATA_ROOTS or destination in destinations:
                raise Refused("mount shadows executable content or duplicates a mount")
            if not source.startswith("/") or type(read_only) is not bool:
                raise Refused("invalid data mount")
            destinations.add(destination)
        if self.commands:
            raise Refused("native subprocess closure is unsupported; kernel execution stays denied")
        for filename, source_path in self.generated_sources:
            if filename not in ("<string>", "<catalog-v1>") or not source_path.startswith("/opt/ccc/") or "/../" in source_path:
                raise Refused("generated code must come from measured source files")
        for filename in self.unix_sockets:
            if not filename.startswith(tuple(root + "/" for root in _DATA_ROOTS)) or "/../" in filename:
                raise Refused("only explicit private data sockets are supported")
        for filename in self.state_files:
            if not filename.startswith(tuple(root + "/" for root in _DATA_ROOTS)) or "/../" in filename:
                raise Refused("state input must be an exact private data file")
            if posixpath.basename(filename) in ("config.json", "settings.json", ".env") or filename.endswith((".toml", ".ini", ".yaml", ".yml")):
                raise Refused("mutable configuration is not an attested state input")
        return {"format": "ccc-execution-profile-v1", "entry": self.entry,
                "argv": list(self.argv), "environment": sorted(env.items()),
                "generated_sources": list(self.generated_sources),
                "unix_sockets": list(self.unix_sockets),
                "state_files": list(self.state_files),
                "configuration_policy": "image-or-public-environment-only-v1",
                "kernel_policy": "deny-exec-ptrace-memfd-namespaces-v2",
                "native_import_policy": "stdlib-extensions-no-ctypes-tests-v1",
                "data_mounts": sorted(self.data_mounts), "commands": list(self.commands),
                "network": self.network, "user": self.user,
                "import_policy": "isolated-source-audit-v1", "readonly_rootfs": True,
                "capabilities": [], "no_new_privileges": True,
                "pid_namespace": self.identity_namespace, "cgroup_namespace": self.identity_namespace,
                "ipc_namespace": "none",
                "runtime": "runc", "init": False, "working_dir": "/opt/ccc",
                "runtime_data_exclusions": ["/proc", "/sys", "/dev", "/etc/hosts",
                                            "/etc/hostname", "/etc/resolv.conf"],
                "temporary_filesystem": "readonly-root-no-tmpfs",
                "automatic_files_readonly": True,
                "etc_snapshot": "readonly-image-subtree-v1",
                "entry_admission": "guardian-child-stop-root-pidfd-release-v1",
                "inherited_descriptors": "docker-stdio-only"}

    @property
    def sha256(self):
        return digest(canonical(self.value()))

    def config(self, image_id):
        self.value()
        return {"Image": image_id, "User": self.user, "WorkingDir": "/opt/ccc",
                "Entrypoint": ["/usr/local/bin/python3", "-I", "-S", "-B",
                               "/opt/ccc/ccc_server/execution_guard.py"],
                "Cmd": [canonical(self.value()).decode()],
                "Env": [key + "=" + value for key, value in sorted(self.environment)],
                "HostConfig": {"ReadonlyRootfs": True, "CapDrop": ["ALL"],
                               "CapAdd": [], "SecurityOpt": ["no-new-privileges"],
                               "NetworkMode": self.network,
                               "PidMode": "host" if self.identity_namespace == "host" else "",
                               "CgroupnsMode": self.identity_namespace,
                               "IpcMode": "none", "Runtime": "runc", "Init": False,
                               "Mounts": [image_etc_mount(image_id), *[{"Type": "bind", "Source": src, "Target": dst,
                                           "ReadOnly": ro} for src, dst, ro in self.data_mounts]]}}


@dataclass(frozen=True)
class ProcessIdentity:
    container_id: str
    pid: int
    start_ticks: int
    boot_id: str
    started_at: str
    launcher_pid: int = 0
    launcher_start_ticks: int = 0


_VERIFIED_FACT = object()


@dataclass(frozen=True, init=False)
class VerifiedLaunch:
    identity: ProcessIdentity
    image_id: str
    executed_build_sha256: str
    execution_profile_sha256: str
    executable_sha256: str

    def __init__(self, identity, image_id, executed_build_sha256,
                 execution_profile_sha256, executable_sha256, *, _token=None):
        if _token is not _VERIFIED_FACT:
            raise Refused("verified facts require the authenticated authority boundary")
        for name, value in (("identity", identity), ("image_id", image_id),
                            ("executed_build_sha256", executed_build_sha256),
                            ("execution_profile_sha256", execution_profile_sha256),
                            ("executable_sha256", executable_sha256)):
            object.__setattr__(self, name, value)


def _verified_launch(identity, image_id, artifact_sha, profile_sha, executable_sha):
    # Internal authority/client transport only; never expose a JSON constructor
    # at an application endpoint. Python code in the approved image is trusted.
    return VerifiedLaunch(identity, image_id, artifact_sha, profile_sha, executable_sha,
                          _token=_VERIFIED_FACT)


class LinuxProcesses:
    def environment(self, identity):
        values = Path(f"/proc/{identity.pid}/environ").read_bytes().split(b"\0")
        return [value.decode("utf-8") for value in values if value]

    def security(self, identity):
        return dict(line.split(":", 1) for line in Path(f"/proc/{identity.pid}/status").read_text().splitlines() if ":" in line)

    def executable_mappings(self, identity):
        result = []
        for line in Path(f"/proc/{identity.pid}/maps").read_text().splitlines():
            fields = line.split(maxsplit=5)
            if "x" in fields[1]:
                result.append((fields[5] if len(fields) == 6 else "", fields[1]))
        return result

    def network_namespace(self, identity):
        return os.readlink(f"/proc/{identity.pid}/ns/net")

    def stopped(self, identity):
        text = Path(f"/proc/{identity.pid}/stat").read_text()
        return text[text.rfind(")") + 2:].split()[0] in ("T", "t")

    def identity(self, container):
        launcher = container["State"]["Pid"]
        if type(launcher) is not int or launcher <= 0:
            raise Refused("no running process identity")
        parent_stat = Path(f"/proc/{launcher}/stat").read_text()
        parent_fields = parent_stat[parent_stat.rfind(")") + 2:].split()
        pid = launcher
        if container["Config"].get("Entrypoint", [])[-1:] == ["/opt/ccc/ccc_server/execution_guard.py"]:
            children = Path(f"/proc/{launcher}/task/{launcher}/children").read_text().split()
            if len(children) != 1:
                raise Refused("guard application child not ready or ambiguous")
            pid = int(children[0])
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        return ProcessIdentity(container["Id"], pid, int(fields[19]),
                               Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                               container["State"]["StartedAt"], launcher, int(parent_fields[19]))

    def release(self, identity):
        import signal
        descriptor = os.pidfd_open(identity.pid)
        try:
            text = Path(f"/proc/{identity.pid}/stat").read_text()
            if int(text[text.rfind(")") + 2:].split()[19]) != identity.start_ticks:
                raise Refused("application PID was reused before release")
            signal.pidfd_send_signal(descriptor, signal.SIGCONT)
        finally:
            os.close(descriptor)

    def executable(self, identity):
        return digest(Path(f"/proc/{identity.pid}/exe").read_bytes())

    def mounts(self, identity):
        result = {}
        for line in Path(f"/proc/{identity.pid}/mountinfo").read_text().splitlines():
            fields = line.split()
            destination = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
            result[destination] = set(fields[5].split(","))
        return result


def verify_container(container, profile, processes):
    """Kernel/daemon facts only. No digest or 'readonly' claim from the worker."""
    expected = profile.config(container["Image"])
    config, host = container["Config"], container["HostConfig"]
    if container.get("ExecIDs") or config.get("Tty") or config.get("OpenStdin"):
        raise Refused("unmeasured exec session or inherited input facility")
    if config.get("Healthcheck") and config["Healthcheck"].get("Test") != ["NONE"]:
        raise Refused("unmeasured healthcheck subprocess")
    if host.get("LogConfig", {}).get("Config"):
        raise Refused("unmeasurable logging configuration/credentials")
    if not container["State"].get("Running") or container["State"].get("Paused"):
        raise Refused("process is not continuously running")
    for key in ("Entrypoint", "Cmd", "User", "WorkingDir"):
        if config.get(key) != expected[key]:
            raise Refused("launch configuration differs: " + key)
    env = {}
    for value in config.get("Env") or []:
        key, sep, val = value.partition("=")
        if not sep or key in env:
            raise Refused("invalid/duplicate runtime environment")
        if key.startswith("CCC_CREDENTIAL_"):
            continue  # Secret values never enter an attestation.
        env[key] = val
    if env != dict(profile.environment):
        raise Refused("unmeasured environment/configuration")
    for key in ("ReadonlyRootfs", "NetworkMode", "PidMode", "CgroupnsMode", "IpcMode", "Runtime"):
        if host.get(key) != expected["HostConfig"][key]:
            raise Refused("unsafe runtime configuration: " + key)
    if set(host.get("CapDrop") or []) != {"ALL"} or host.get("CapAdd"):
        raise Refused("worker retains capabilities")
    if set(host.get("SecurityOpt") or []) not in ({"no-new-privileges"}, {"no-new-privileges=true"}):
        raise Refused("no-new-privileges not enforced")
    if any(host.get(key) for key in ("Privileged", "Init", "Binds", "VolumesFrom", "Devices",
                                     "DeviceRequests", "Tmpfs", "ExtraHosts", "Links")):
        raise Refused("unmeasured runtime facility")
    image_mounts = [m for m in container.get("Mounts", []) if m["Destination"] == "/etc"]
    if (len(image_mounts) != 1 or image_mounts[0]["Type"] != "image" or image_mounts[0]["RW"]
            or image_mounts[0].get("Name") != container["Image"][:31]):
        raise Refused("etc is not the pinned read-only image subtree")
    actual_mounts = sorted((item["Source"], item["Destination"], not item["RW"])
                           for item in container.get("Mounts", []) if item["Destination"] != "/etc")
    if actual_mounts != sorted(profile.data_mounts):
        raise Refused("unmeasured bind/volume/plugin mount")
    identity = processes.identity(container)
    security = processes.security(identity)
    if (security.get("NoNewPrivs", "").strip() != "1" or security.get("Seccomp", "").strip() != "2"
            or int(security.get("Seccomp_filters", "0")) < 2):
        raise Refused("kernel execution guard is not installed")
    mounts = processes.mounts(identity)
    if "ro" not in mounts.get("/", set()):
        raise Refused("kernel reports writable root filesystem")
    if "ro" not in mounts.get("/etc", set()):
        raise Refused("kernel reports writable etc snapshot")
    for _, destination, _ in profile.data_mounts:
        if not {"noexec", "nosuid", "nodev"} <= mounts.get(destination, set()):
            raise Refused("data mount permits executable content")
    # Runtime pseudo-filesystems cannot overlay measured code. Docker-generated
    # resolver/hostname files are explicitly excluded by the import guard.
    for destination in ("/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
        if destination in mounts and "ro" not in mounts[destination]:
            raise Refused("writable Docker-generated file: " + destination)
    if any(m == "/dev/shm" or m.startswith("/dev/shm/") or m == "/dev/mqueue"
           for m in mounts):
        raise Refused("shared-memory/message-queue mount is not permitted")
    allowed = {"/", "/etc", "/proc", "/sys", "/dev", "/dev/pts", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}
    allowed.update(dst for _, dst, _ in profile.data_mounts)
    for destination in mounts:
        if destination in allowed or destination.startswith(("/proc/", "/sys/", "/dev/pts/")):
            continue
        raise Refused("unmeasured kernel mount: " + destination)
    return identity


def measured_profile_sha256(container, profile, process_environment=None):
    """Bind ALL actual Docker settings, including defaults we did not request.

    Only the generated hostname (already covered by exact launch identity) and
    narrowly namespaced credential VALUES are excluded. Unknown engine settings
    therefore change the binding instead of silently becoming wildcards.
    """
    config = dict(container["Config"])
    config.pop("Hostname", None)
    config["Env"] = sorted(key + "=<excluded-credential-value>" if key.startswith("CCC_CREDENTIAL_") else value
                           for value in config.get("Env") or [] for key in [value.partition("=")[0]])
    actual = {}
    for value in process_environment if process_environment is not None else container["Config"].get("Env") or []:
        key, sep, val = value.partition("=")
        if not sep or key in actual:
            raise Refused("invalid kernel environment snapshot")
        actual[key] = val
    requested = dict(profile.environment)
    for key, value in requested.items():
        if actual.get(key) != value:
            raise Refused("kernel environment differs from requested configuration")
    if any(key not in requested and key not in {"PATH", "HOME", "HOSTNAME"}
           and not key.startswith("CCC_CREDENTIAL_") for key in actual):
        raise Refused("unknown implicit runtime environment")
    if actual.get("HOME", "").startswith(_DATA_ROOTS):
        raise Refused("kernel supplied mutable HOME configuration")
    if "HOSTNAME" in actual and "HOSTNAME" not in requested:
        if actual["HOSTNAME"] != container["Config"].get("Hostname"):
            raise Refused("unknown generated hostname environment")
        actual["HOSTNAME"] = "<launch-identity>"
    actual = {key: "<excluded-credential-value>" if key.startswith("CCC_CREDENTIAL_") else value
              for key, value in actual.items()}
    return digest(canonical({"policy": profile.value(), "config": config,
                             "process_environment": actual,
                             "host_config": container["HostConfig"],
                             "engine_runtime": {key: container.get(key) for key in
                                                ("Platform", "Driver", "AppArmorProfile", "ProcessLabel", "MountLabel")},
                             "mounts": runtime_mount_snapshot(container)}))


class TrustedLaunchVerifier:
    """Used inside the pinned root authority, never as a caller JSON validator.

    The authority runner authenticates its own image/entrypoint/profile before
    constructing this object. Consumers use AttestationClient; the injectable
    adapters here support offline negative controls, not runtime admission.
    """

    def __init__(self, docker, processes):
        self.docker, self.processes = docker, processes

    def prepare_launch(self, launch_id, image_id, executed_build_sha256, profile):
        """Measure before execution, then leave application admission stopped.

        Only the immutable guard runs before the stop. Recovery/application
        code cannot run until the coordinator accepts proofs and calls release.
        """
        import time
        if not re.fullmatch(r"[a-z0-9-]{1,64}", launch_id):
            raise Refused("launch id must be an explicit stable idempotency name")
        artifact = self.docker.measure(image_id)
        if (artifact.image_id != image_id or artifact.executed_build_sha256 != executed_build_sha256
                or digest(artifact.manifest) != executed_build_sha256):
            raise Refused("launch artifact differs from the accepted exact image")
        ident = self.docker.request("POST", "/containers/create?name=ccc-execution-" + launch_id,
                                    profile.config(image_id))["Id"]
        try:
            self.docker.verify_runtime_etc(self.docker.inspect_container(ident), artifact)
            self.docker.request("POST", "/containers/" + ident + "/start")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = self.docker.inspect_container(ident)
                if not current["State"].get("Running"):
                    raise Refused("immutable entry guard did not reach admission stop")
                try:
                    identity = self.processes.identity(current)
                except Refused as exc:
                    if str(exc) != "guard application child not ready or ambiguous":
                        raise
                    time.sleep(0.02)
                    continue
                if self.processes.stopped(identity):
                    return self.observe(ident, profile, identity)
                time.sleep(0.02)
            raise Refused("immutable entry guard readiness unknown")
        except BaseException:
            self.docker.request("DELETE", "/containers/" + ident + "?force=1")
            raise

    def release(self, fact, profile):
        """Admission only; caller must already hold the accepted proof/freeze.

        This does not choose/unlock any restart barrier. It repeats the exact
        live binding before admitting the prepared application's first input.
        """
        actual = self.observe(fact.identity.container_id, profile, fact.identity)
        if actual != fact or not self.processes.stopped(fact.identity):
            raise Refused("prepared application identity/admission stop changed")
        self.processes.release(fact.identity)

    def observe(self, container_id, profile, expected_identity=None):
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise Refused("container identity must be exact, not a name/tag")
        before = self.docker.inspect_container(container_id)
        identity = verify_container(before, profile, self.processes)
        if self.docker.changes(container_id):
            raise Refused("container writable layer differs from immutable image")
        artifact = self.docker.measure(before["Image"])
        if artifact.image_id != before["Image"] or digest(artifact.manifest) != artifact.executed_build_sha256:
            raise Refused("fake or inconsistent artifact digest")
        self.docker.verify_runtime_etc(before, artifact)
        entries = {e["path"]: e for e in json.loads(artifact.manifest)["entries"]}
        resolved = _resolve(entries, "usr/local/bin/python3")
        executable = entries[resolved]
        if executable["type"] != "file" or not executable["mode"] & 0o111:
            raise Refused("unmeasured Python executable")
        if self.processes.executable(identity) != executable["sha256"]:
            raise Refused("actual process executable differs from image")
        for filename, permissions in self.processes.executable_mappings(identity):
            if filename in ("[vdso]", "[vsyscall]"):
                continue  # Trusted kernel, not an executable input.
            if "w" in permissions or not filename.startswith("/") or filename.endswith(" (deleted)"):
                raise Refused("anonymous/writable/deleted executable mapping")
            resolved = _resolve(entries, filename.lstrip("/"))
            if entries[resolved]["type"] != "file":
                raise Refused("unmeasured loaded executable dependency")
        for filename in (profile.entry, "/opt/ccc/ccc_server/execution_guard.py"):
            if entries.get(filename.lstrip("/"), {}).get("type") != "file":
                raise Refused("entry/guard absent from measured image")
        for _, filename in profile.generated_sources:
            if entries.get(filename.lstrip("/"), {}).get("type") != "file":
                raise Refused("generated-code source absent from measured image")
        if profile.network.startswith("container:"):
            sibling = self.docker.inspect_container(profile.network.split(":", 1)[1])
            if not sibling["State"].get("Running") or sibling["HostConfig"].get("NetworkMode") != "none":
                raise Refused("shared network namespace is not isolated")
            sibling_identity = self.processes.identity(sibling)
            if self.processes.network_namespace(identity) != self.processes.network_namespace(sibling_identity):
                raise Refused("actual network namespaces differ")
            if self.docker.inspect_container(sibling["Id"]) != sibling:
                raise Refused("network namespace owner changed during attestation")
        after = self.docker.inspect_container(container_id)
        if before != after or self.processes.identity(after) != identity or self.docker.changes(container_id):
            raise Refused("process/configuration changed during attestation")
        if expected_identity is not None and identity != expected_identity:
            raise Refused("PID/start/launch identity mismatch")
        return _verified_launch(identity, artifact.image_id, artifact.executed_build_sha256,
                              measured_profile_sha256(before, profile, self.processes.environment(identity)), executable["sha256"])

    def verify_transition(self, refs, profiles, tested_pair, expected_identities):
        """Re-observe all three roles at BOTH caller-controlled barrier checks.

        tested_pair maps worker/control/replacement to exact (artifact, profile).
        Control has its OWN profile: a dashboard entrypoint is not a worker.
        Worker->replacement is ordered; A->B never implies B->A or A->A.
        The replacement must be observed before recovered input admission.
        """
        roles = ("worker", "control", "replacement")
        if (set(refs) != set(roles) or set(profiles) != set(roles)
                or set(expected_identities) != set(roles) or set(tested_pair) != set(roles)):
            raise Refused("all three exact role identities are required")
        if len({(i.pid, i.start_ticks, i.boot_id) for i in expected_identities.values()}) != 3:
            raise Refused("worker/control/replacement must have distinct process identities")
        facts = {role: self.observe(refs[role], profiles[role], expected_identities[role])
                 for role in roles}
        for role in roles:
            fact = facts[role]
            if (fact.executed_build_sha256, fact.execution_profile_sha256) != tuple(tested_pair[role]):
                raise Refused("untested ordered transition: " + role)
        return facts
