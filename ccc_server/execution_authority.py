"""One-shot, root-Docker authenticated observation of immutable executions.

An operator must provision a root-owned anchor for the exact authority image
and rootfs hash. The client measures that pinned image before starting it and
receives its stdout through the root daemon's container log channel. The
authority independently checks its actual image, PID, mounts and entrypoint.
No self-reported ticket/worker JSON is an attestation. No daemon is installed.
"""

import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
from urllib.parse import quote

if __name__ == "__main__":
    sys.path.insert(0, "/opt/ccc")

from ccc_server.execution_artifact import (
    Artifact, ExecutionProfile, LinuxProcesses, ProcessIdentity, Refused,
    TrustedLaunchVerifier, VerifiedLaunch, canonical, digest, manifest_from_tar,
    _verified_launch,
)


SOCKET = "/var/run/docker.sock"
ANCHOR = "/run/ccc-anchor.json"
ENTRYPOINT = ["/usr/local/bin/python3", "-I", "-S", "-B",
              "/opt/ccc/ccc_server/execution_authority.py"]


def read_anchor(filename):
    """Root ownership includes all ancestors; symlinks/writeable anchors refuse."""
    filename = Path(filename).absolute()
    for candidate in (filename, *filename.parents):
        metadata = candidate.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or stat.S_ISLNK(metadata.st_mode):
            raise Refused("authority anchor is not exclusively root-controlled")
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8192:
            raise Refused("invalid authority anchor file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(8193)
    finally:
        os.close(fd)
    value = json.loads(data)
    if set(value) != {"format", "image_id", "executed_build_sha256", "execution_profile_sha256"} or value["format"] != "ccc-root-authority-v1":
        raise Refused("unsupported root authority anchor")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"]) or any(not re.fullmatch(r"[0-9a-f]{64}", value[k]) for k in ("executed_build_sha256", "execution_profile_sha256")):
        raise Refused("unmeasured authority image")
    return value


class _Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET)
        _, uid, _ = struct.unpack("3i", self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            self.sock.close()
            raise Refused("Docker endpoint is not root-authenticated")


class RootDocker:
    def changes(self, ident):
        return self.request("GET", "/containers/" + quote(ident, safe="") + "/changes")

    def request(self, method, endpoint, body=None, *, raw=False):
        connection = _Connection("localhost", timeout=45)
        try:
            connection.request(method, "/v1.45" + endpoint,
                               body=canonical(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
            if response.status >= 300:
                raise Refused(f"root Docker API failed ({response.status}): " + data[:300].decode(errors="replace"))
            return data if raw else (json.loads(data) if data else None)
        finally:
            connection.close()

    def inspect_container(self, ident):
        return self.request("GET", "/containers/" + quote(ident, safe="") + "/json")

    def measure(self, image):
        info = self.request("GET", "/images/" + quote(image, safe="") + "/json")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", info["Id"]) or info.get("Config", {}).get("Volumes"):
            raise Refused("unmeasured image or external volume")
        ident = self.request("POST", "/containers/create",
                             {"Image": info["Id"], "Entrypoint": ["/unused"]})["Id"]
        try:
            data = self.request("GET", "/containers/" + ident + "/export", raw=True)
            manifest = manifest_from_tar(io.BytesIO(data))
            return Artifact(info["Id"], digest(manifest), manifest)
        finally:
            self.request("DELETE", "/containers/" + ident)


def authority_config(anchor, anchor_source, query):
    return {"Image": anchor["image_id"], "Entrypoint": ENTRYPOINT, "Cmd": [],
            "User": "0:0", "WorkingDir": "/opt/ccc", "Env": ["CCC_ATTESTATION_QUERY=" + canonical(query).decode()],
            "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "none", "PidMode": "host",
                           "IpcMode": "none", "Runtime": "runc", "CapDrop": ["ALL"],
                           # Read-only /proc executable access to another uid.
                           "CapAdd": ["SYS_PTRACE"], "SecurityOpt": ["no-new-privileges"],
                           "Mounts": [{"Type": "bind", "Source": SOCKET, "Target": SOCKET, "ReadOnly": True},
                                      {"Type": "bind", "Source": str(anchor_source), "Target": ANCHOR, "ReadOnly": True}]}}


def authority_profile_sha256(container):
    """Root-measured authority settings; request bytes are data, never code."""
    config = dict(container["Config"])
    config.pop("Hostname", None)
    config["Env"] = ["CCC_ATTESTATION_QUERY=<request-data>"]
    return digest(canonical({"format": "ccc-authority-profile-v1", "config": config,
                             "host_config": container["HostConfig"],
                             "engine_runtime": {key: container.get(key) for key in
                                                ("Platform", "Driver", "AppArmorProfile", "ProcessLabel", "MountLabel")},
                             "mounts": sorted(container.get("Mounts", []), key=lambda m: m["Destination"])}))


def authenticate_self(docker, anchor, processes):
    """The pinned authority is itself an immutable, measured root launch."""
    if os.geteuid() != 0 or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise Refused("not running in the trusted isolated authority")
    containers = docker.request("GET", "/containers/json")
    matches = [docker.inspect_container(c["Id"]) for c in containers]
    matches = [c for c in matches if c["State"]["Pid"] == os.getpid()]
    if len(matches) != 1:
        raise Refused("authority PID is not a unique root-Docker launch")
    current = matches[0]
    if docker.changes(current["Id"]):
        raise Refused("authority writable layer differs from pinned image")
    if current["Image"] != anchor["image_id"] or not current["State"]["Running"]:
        raise Refused("authority was not launched from the pinned image")
    if authority_profile_sha256(current) != anchor["execution_profile_sha256"]:
        raise Refused("authority execution profile differs from root anchor")
    config, host = current["Config"], current["HostConfig"]
    if (config.get("Entrypoint") != ENTRYPOINT or config.get("Cmd") not in ([], None)
            or config.get("User") != "0:0" or config.get("WorkingDir") != "/opt/ccc"):
        raise Refused("unmeasured authority entrypoint")
    if any(not env.startswith("CCC_ATTESTATION_QUERY=") for env in config.get("Env") or []):
        raise Refused("unmeasured authority environment")
    expected = authority_config(anchor, "unused", {})["HostConfig"]
    for key in ("ReadonlyRootfs", "NetworkMode", "PidMode", "IpcMode", "Runtime"):
        if host.get(key) != expected[key]:
            raise Refused("unmeasured authority execution profile")
    if set(host.get("CapDrop") or []) != {"ALL"} or set(host.get("CapAdd") or []) != {"SYS_PTRACE"}:
        raise Refused("unmeasured authority capabilities")
    if set(host.get("SecurityOpt") or []) not in ({"no-new-privileges"}, {"no-new-privileges=true"}):
        raise Refused("authority privileges are not fixed")
    if any(host.get(k) for k in ("Privileged", "Init", "Binds", "VolumesFrom", "Devices", "DeviceRequests", "Tmpfs")):
        raise Refused("unmeasured authority facility")
    mounts = current.get("Mounts", [])
    if len(mounts) != 2 or {m["Destination"] for m in mounts} != {SOCKET, ANCHOR} or any(m["RW"] or m["Type"] != "bind" for m in mounts):
        raise Refused("unmeasured authority mounts")
    if next(m for m in mounts if m["Destination"] == SOCKET)["Source"] != SOCKET:
        raise Refused("untrusted Docker socket source")
    # A read-only root-owned anchor is independently checked inside the image.
    # The client additionally binds its exact host source and canonical bytes.
    artifact = docker.measure(anchor["image_id"])
    if artifact.executed_build_sha256 != anchor["executed_build_sha256"]:
        raise Refused("authority executable closure changed")
    identity = processes.identity(current)
    from ccc_server.execution_artifact import _resolve
    entries = {e["path"]: e for e in json.loads(artifact.manifest)["entries"]}
    python = entries[_resolve(entries, "usr/local/bin/python3")]
    if processes.executable(identity) != python["sha256"]:
        raise Refused("authority executable differs from pinned image")
    kernel_mounts = processes.mounts(identity)
    if "ro" not in kernel_mounts.get("/", set()) or "ro" not in kernel_mounts.get(ANCHOR, set()):
        raise Refused("authority/anchor is mutable")
    for destination in ("/etc/hosts", "/etc/hostname", "/etc/resolv.conf"):
        if destination in kernel_mounts and "ro" not in kernel_mounts[destination]:
            raise Refused("writable Docker-generated authority file")
    if any(m in kernel_mounts for m in ("/dev/shm", "/dev/mqueue")):
        raise Refused("authority has unmeasured writable IPC mounts")
    allowed = {"/", SOCKET, ANCHOR, "/proc", "/sys", "/dev", "/dev/pts", "/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}
    if any(m not in allowed and not m.startswith(("/proc/", "/sys/", "/dev/pts/")) for m in kernel_mounts):
        raise Refused("authority has unmeasured mounts")
    return identity


def _profile(value):
    profile = ExecutionProfile(value["entry"], tuple(value["argv"]),
                               tuple(tuple(x) for x in value["environment"]),
                               tuple(tuple(x) for x in value["data_mounts"]),
                               tuple(tuple(x) for x in value["commands"]),
                               value["network"], value["user"],
                               tuple(tuple(x) for x in value["generated_sources"]),
                               tuple(value["unix_sockets"]))
    if canonical(profile.value()) != canonical(value):
        raise Refused("unsupported profile format/fields")
    return profile


def _wire(fact):
    return {"identity": vars(fact.identity), "image_id": fact.image_id,
            "executed_build_sha256": fact.executed_build_sha256,
            "execution_profile_sha256": fact.execution_profile_sha256,
            "executable_sha256": fact.executable_sha256}


def _fact(value):
    return _verified_launch(ProcessIdentity(**value["identity"]), value["image_id"],
                          value["executed_build_sha256"], value["execution_profile_sha256"],
                          value["executable_sha256"])


def main():
    anchor = read_anchor(ANCHOR)
    docker, processes = RootDocker(), LinuxProcesses()
    authority = authenticate_self(docker, anchor, processes)
    query = json.loads(os.environ["CCC_ATTESTATION_QUERY"])
    verifier = TrustedLaunchVerifier(docker, processes)
    operation = query.pop("operation", None)
    if operation == "transition" and set(query) == {"refs", "profiles", "tested_pair", "identities"}:
        profiles = {role: _profile(value) for role, value in query["profiles"].items()}
        identities = {role: ProcessIdentity(**value) for role, value in query["identities"].items()}
        facts = verifier.verify_transition(query["refs"], profiles, query["tested_pair"], identities)
    elif operation == "prepare" and set(query) == {"launch_id", "image_id", "executed_build_sha256", "profile"}:
        fact = verifier.prepare_launch(query["launch_id"], query["image_id"],
                                        query["executed_build_sha256"], _profile(query["profile"]))
        facts = {"prepared": fact}
    elif operation == "release" and set(query) == {"fact", "profile"}:
        # Caller JSON cannot attest itself: release re-observes its complete
        # claimed binding and admission stop before SIGCONT.
        claim = query["fact"]
        profile = _profile(query["profile"])
        identity = ProcessIdentity(**claim["identity"])
        fact = verifier.observe(identity.container_id, profile, identity)
        if _wire(fact) != claim:
            raise Refused("claimed release binding differs from actual launch")
        verifier.release(fact, profile)
        facts = {"released": fact}
    else:
        raise Refused("unsupported attestation request")
    # Recheck the authority itself and anchor after every target observation.
    if read_anchor(ANCHOR) != anchor or authenticate_self(docker, anchor, processes) != authority:
        raise Refused("authority identity changed during observation")
    print(canonical({"authority": vars(authority), "facts": {r: _wire(f) for r, f in facts.items()}}).decode())


class AttestationClient:
    """Obtain facts via root Docker's authenticated one-shot image execution.

    There is intentionally no 'accept JSON receipt' or environment bypass API.
    Anchor provisioning and actual worker/control launches remain operator work.
    Calling twice (initial/final) performs two complete live observations.
    """
    def __init__(self, anchor_path):
        self.anchor_path = Path(anchor_path).absolute()

    def _request(self, query):
        anchor = read_anchor(self.anchor_path)
        docker = RootDocker()
        if docker.measure(anchor["image_id"]).executed_build_sha256 != anchor["executed_build_sha256"]:
            raise Refused("pinned authority rootfs mismatch")
        config = authority_config(anchor, self.anchor_path, query)
        ident = docker.request("POST", "/containers/create", config)["Id"]
        try:
            actual = docker.inspect_container(ident)
            if actual["Image"] != anchor["image_id"]:
                raise Refused("root daemon created the wrong authority image")
            if authority_profile_sha256(actual) != anchor["execution_profile_sha256"]:
                raise Refused("authority launch profile differs from root anchor")
            docker.request("POST", "/containers/" + ident + "/start")
            result = docker.request("POST", "/containers/" + ident + "/wait?condition=not-running")
            logs = docker.request("GET", "/containers/" + ident + "/logs?stdout=1&stderr=1", raw=True)
            streams = {1: bytearray(), 2: bytearray()}
            while logs:
                if len(logs) < 8:
                    raise Refused("truncated authenticated authority output")
                stream, length = logs[0], int.from_bytes(logs[4:8], "big")
                if stream not in streams or len(logs) < 8 + length:
                    raise Refused("invalid authority output framing")
                streams[stream].extend(logs[8:8 + length])
                logs = logs[8 + length:]
            if result.get("StatusCode") != 0:
                raise Refused("immutable authority refused: " + streams[2].decode(errors="replace")[-600:])
            value = json.loads(streams[1])
            if read_anchor(self.anchor_path) != anchor or value["authority"]["container_id"] != ident:
                raise Refused("authority anchor/launch mismatch")
            facts = {r: _fact(f) for r, f in value["facts"].items()}
            # No caller-provided receipt reaches this conversion: only stdout
            # attached to the exact image launch just created by root Docker.
            return facts
        finally:
            # Exact disposable authority only; never signal an observed target.
            docker.request("DELETE", "/containers/" + ident + "?force=1")

    def prepare_launch(self, launch_id, image_id, executed_build_sha256, profile):
        facts = self._request({"operation": "prepare", "launch_id": launch_id,
                               "image_id": image_id, "executed_build_sha256": executed_build_sha256,
                               "profile": profile.value()})
        if set(facts) != {"prepared"} or facts["prepared"].image_id != image_id or facts["prepared"].executed_build_sha256 != executed_build_sha256:
            raise Refused("authenticated prepared launch differs from request")
        return facts["prepared"]

    def release(self, fact, profile):
        facts = self._request({"operation": "release", "fact": _wire(fact), "profile": profile.value()})
        if facts != {"released": fact}:
            raise Refused("authenticated release differs from prepared identity")

    def verify_transition(self, refs, profiles, tested_pair, expected_identities):
        facts = self._request({"operation": "transition", "refs": refs,
                               "profiles": {r: p.value() for r, p in profiles.items()},
                               "tested_pair": tested_pair,
                               "identities": {r: vars(i) for r, i in expected_identities.items()}})
        if set(facts) != {"worker", "control", "replacement"}:
            raise Refused("missing authenticated role facts")
        for role, fact in facts.items():
            if fact.identity != expected_identities[role] or (fact.executed_build_sha256, fact.execution_profile_sha256) != tuple(tested_pair[role]):
                raise Refused("authenticated facts do not match requested transition")
        return facts


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("attestation refused: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
