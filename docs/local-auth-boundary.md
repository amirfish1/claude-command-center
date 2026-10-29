# CCC Local-Program Authentication Boundary & Threat Model

This document establishes the architecture, threat model, candidate comparison, and implementation of Claude Command Center's authentication and boundary protection against local-program bypass.

---

## 1. Executive Summary & Problem Statement

In single-user developer installations, Claude Command Center (`server.py`) operates under a **loopback-only trust model** (`127.0.0.1:8090`). Any local process capable of connecting to loopback can issue API calls and spawn agent tasks.

When deploying CCC behind an authenticating reverse proxy (such as Caddy or Nginx with Basic Auth, Authelia, or OAuth2) to protect remote visitors (e.g. PR #209), an implicit assumption often made is that containerizing CCC or placing it on a Docker bridge network without published host ports isolates the backend from unauthenticated local host programs.

**On native Linux, this assumption is false:**
Docker bridge networks (including `--internal` bridges) instantiate a virtual bridge device (`br-<id>`) in the host's root network namespace. Traffic originating from host processes hits the Linux kernel `OUTPUT` routing chain rather than the `FORWARD` filter chain. Consequently, **any unprivileged host process can connect directly to container bridge IP addresses (e.g., `http://172.20.0.2:8090`)**, completely bypassing the reverse proxy and all proxy-level authentication.

To address this, CCC introduces a lightweight, zero-dependency **Backend Shared Secret Boundary** (`CCC_PROXY_SECRET` / `CCC_PROXY_SECRET_FILE`), allowing the authenticating reverse proxy to inject a shared credential (`X-CCC-Proxy-Secret` or `Authorization: Bearer <secret>`). Direct requests lacking the secret are rejected with `401 Unauthorized`.

---

## 2. Threat Model Matrix

The security boundaries and attack classes are defined below:

| Threat Class / Attacker | Description | Without Proxy Secret (Bridge Only) | With Backend Shared Secret (`CCC_PROXY_SECRET`) | Supported Boundary? |
| :--- | :--- | :--- | :--- | :--- |
| **Remote Visitor** | External client over WAN or LAN attempting direct connection to CCC port. | **Blocked** by reverse proxy ingress (PR #209). | **Blocked** (Proxy authenticates visitor before injecting secret). | **Supported** |
| **Unprivileged Peer Container** | Other container on same Docker host / bridge network attempting direct HTTP access. | **Vulnerable** (Direct HTTP 200). | **Blocked** (Returns 401 Unauthorized; container lacks proxy secret). | **Supported** |
| **Unprivileged Host Process (Different UID)** | Non-owner user or daemon (e.g., `nobody`, `www-data`, compromised service) running on host. | **Vulnerable** (Kernel routes direct to `br-*` IP; HTTP 200). | **Blocked** (Returns 401; DAC file permissions `0600` prevent reading secret). | **Supported** |
| **Same-User Host Process (Same UID)** | Process running under the same user account (`UID == ccc_owner`). | **Vulnerable** (Direct HTTP 200). | **Outside Boundary** (Same UID can read `0600` secret file or inspect process `/proc/<pid>/environ`). | **Documented Limitation** |
| **Privileged Host Process (Root / CAP_SYS_ADMIN)** | Root user, sudoer, or process with host namespace administrative privileges. | **Vulnerable** (Full bypass). | **Outside Boundary** (Root can dump memory, read any secret, bypass DAC). | **Documented Limitation** |

### Boundary Scope

- **In Scope (Supported)**:
  1. Complete prevention of unauthorized remote ingress.
  2. Complete rejection of unauthenticated direct connections over Docker bridge networks from peer containers.
  3. Complete rejection of unauthenticated direct connections from different host UIDs when `CCC_PROXY_SECRET_FILE` is deployed with mode `0600`.
  4. Constant-time verification (`hmac.compare_digest`) against timing-attack side channels.

- **Out of Scope (Outside Boundary)**:
  1. *Same-UID processes*: In the standard Linux Discretionary Access Control (DAC) model, processes of the same UID share equal filesystem and memory inspection permissions. An unconfined process running under the same UID as CCC can read the secret file or read the environment. Protection against same-UID attackers requires OS-level Mandatory Access Control (MAC like AppArmor/SELinux) or dedicated user namespaces.
  2. *Host Root*: The host superuser has kernel-level control and memory access.

---

## 3. Comparison of Isolation & Authentication Architectures

We evaluated five architectural candidates for mitigating local-program bypass:

### Candidate A: Backend Shared Secret (`X-CCC-Proxy-Secret` / Bearer Token)
*Selected Architecture*
- **Mechanism**: The reverse proxy is configured with a high-entropy pre-shared secret. On successful client authentication, the proxy injects `X-CCC-Proxy-Secret: <secret>` or `Authorization: Bearer <secret>`. CCC verifies this header on all incoming requests using `hmac.compare_digest`.
- **Pros**:
  - Zero external dependencies: implemented in pure Python stdlib (`import hmac`).
  - Completely non-breaking: when `CCC_PROXY_SECRET` is unset, single-user dev loopback behaves identically with zero friction.
  - Native reverse-proxy integration: standard feature across Caddy (`header_up`), Nginx (`proxy_set_header`), Traefik, Apache, etc.
  - Works transparently across macOS, Linux, and Windows containers without platform-specific socket mounting headaches.
  - Leaves `ccc` CLI, local hooks, and EventSource SSE unaffected when configured with secret.
- **Cons**:
  - Processes running as the same UID with read access to the environment or secret file can read the token.

### Candidate B: UNIX Domain Socket (UDS) Listener
- **Mechanism**: CCC binds exclusively to a UNIX domain socket (e.g. `/var/run/ccc/ccc.sock`) with file permissions `0600` (owner) or `0660` (shared proxy group), omitting TCP listeners entirely.
- **Tradeoffs**:
  - *Pros*: Leverages filesystem permissions (DAC) to restrict connection initiation to authorized UIDs/groups.
  - *Cons*:
    - High architectural breakage: `http.server.HTTPServer` in Python stdlib is bound to `socket.AF_INET`/`AF_INET6`. Replacing it with `AF_UNIX` requires subclassing `socketserver.UnixStreamServer`, rewriting request handler assumptions (`self.client_address` is a string instead of `(host, port)`), and refactoring remote/phone IP detection.
    - Broken CLI tooling: Python's `urllib.request` cannot connect to UNIX domain sockets out of the box (requires custom HTTPConnection/HTTPHandler).
    - Cross-platform VM boundary failures: Docker Desktop on macOS and Windows mounts host directories via virtiofs/gRPC-FUSE, which historically suffers from broken or fragile UNIX socket forwarding between host and VM.
    - Container volume complexity: Requires managing shared volume ownership and UID mapping between proxy container, backend container, and host.

### Candidate C: Native In-Process HTTP Auth in `server.py`
- **Mechanism**: CCC natively handles user login (HTTP Basic Auth, session cookies, user database).
- **Tradeoffs**:
  - *Pros*: Eliminates dependence on external reverse proxies for user authentication.
  - *Cons*:
    - Contradicts the CCC architectural philosophy: `server.py` is an unbloated single-user command center engine, not a multi-user auth portal.
    - Breaks JavaScript `EventSource` SSE streams (browsers do not support custom headers on `EventSource`, requiring cookie management or query param token hacks).
    - Substantially increases attack surface and maintenance burden for password hashing, rate limiting, and session invalidation.

### Candidate D: Host Firewall Rules (`iptables` / `nftables`)
- **Mechanism**: Add host firewall rules on the `br-*` interface to drop packets from the host to the container IP.
- **Tradeoffs**:
  - *Pros*: Enforces isolation at network layer.
  - *Cons*: Requires host `root` privileges to configure, highly prone to breaking Docker networking, fragile across daemon restarts and network recreations.

### Candidate E: Status Quo (Remote-Visitor Proxy Only - Q353)
- **Mechanism**: Retain single-user loopback model; document that proxy protects remote visitors only.
- **Tradeoffs**:
  - *Pros*: No code changes.
  - *Cons*: Leaves unprivileged host processes and peer containers able to bypass proxy authentication by connecting directly to the bridge IP.

### Summary Evaluation & Recommendation

**Recommendation**: **Candidate A (Backend Shared Secret)** provides the strongest security-to-complexity ratio. It delivers complete isolation against peer containers and other host UIDs with zero platform-specific socket fragility and zero breaking changes to existing developer workflows.

---

## 4. Native Linux Bypass Reproduction & Test Evidence

Integration tests were executed on native Linux (Kernel 6.8, Docker 27.x) in `tests/test_local_auth_bypass.py`.

### Attempt 1: Baseline Bridge Bypass (Vulnerability Confirmed)
- **Setup**: Container started on `--internal` Docker bridge network with no published ports.
- **Result**: Native Linux host process ran `curl http://<container-bridge-ip>:8090/api/sessions`.
- **Observation**: Request succeeded with **HTTP 200 OK**.
- **Conclusion**: Confirmed that an internal Docker bridge alone offers 0% protection against host processes on Linux.

### Attempt 2: Hardened Deployment with Shared Secret (Vulnerability Mitigated)
- **Setup**: Container started with `CCC_PROXY_SECRET=test-secret` behind Caddy reverse proxy.
- **Verification Matrix**:
  1. Direct host access to bridge IP without secret: **HTTP 401 Unauthorized** (Blocked ✓).
  2. Direct host access to bridge IP with invalid secret: **HTTP 401 Unauthorized** (Blocked ✓).
  3. Direct host access to bridge IP with `X-CCC-Proxy-Secret`: **HTTP 200 OK** (Allowed ✓).
  4. Direct host access to bridge IP with `Authorization: Bearer <secret>`: **HTTP 200 OK** (Allowed ✓).
  5. Peer container on same bridge network without secret: **HTTP 401 Unauthorized** (Blocked ✓).
  6. Non-owner host UID (`65534 nobody`) attempting to read mode `0600` secret file: **EACCES Permission Denied** (Blocked ✓).
  7. Client accessing via Caddy reverse proxy without Basic Auth: **HTTP 401 Unauthorized** (Blocked ✓).
  8. Client accessing via Caddy reverse proxy with Basic Auth: **HTTP 200 OK** (Caddy injects header; Allowed ✓).

All 9 automated unit and Linux integration tests passed (`Ran 9 tests in 15.388s. OK`).

---

## 5. Deployment & Configuration Guide

### 1. Generating a Secure Secret
Generate a cryptographically random 32-byte hex secret:
```bash
openssl rand -hex 32 > ~/.claude/command-center/proxy_secret
chmod 0600 ~/.claude/command-center/proxy_secret
```

### 2. Configuring CCC (`server.py`)
Set either environment variable:
- `CCC_PROXY_SECRET`: The plaintext secret string.
- `CCC_PROXY_SECRET_FILE`: Absolute path to a mode `0600` file containing the secret (recommended for production containers).

When configured, CCC enforces that every incoming request carries matching credentials:
- Header: `X-CCC-Proxy-Secret: <secret>`
- OR Header: `Authorization: Bearer <secret>`

### 3. Configuring Reverse Proxy (Caddy Example)
In `Caddyfile`:
```caddy
:8080 {
    basic_auth {
        admin $2a$14$exampleHashedPasswordHere...
    }

    reverse_proxy ccc-backend:8090 {
        header_up X-CCC-Proxy-Secret {$CCC_PROXY_SECRET}
    }
}
```

### 4. Configuring Docker Compose
```yaml
services:
  caddy:
    image: caddy:2
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      - CCC_PROXY_SECRET=${CCC_PROXY_SECRET}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
    networks:
      - ccc_internal

  ccc:
    image: claude-command-center
    environment:
      - CCC_PROXY_SECRET=${CCC_PROXY_SECRET}
    networks:
      - ccc_internal

networks:
  ccc_internal:
    internal: true
```

---

## 6. Rollback & Migration Procedures

1. **Zero-Downtime Rollback**:
   To disable the backend secret boundary and return to open loopback mode:
   - Remove or unset `CCC_PROXY_SECRET` and `CCC_PROXY_SECRET_FILE`.
   - Restart the CCC server process: `ccc restart dashboard`.
   - No database schema migrations or state alterations exist; the fallback is immediate.

2. **Secret Rotation**:
   - Update the secret file on disk (`~/.claude/command-center/proxy_secret`).
   - Reload Caddy (`caddy reload`).
   - Restart CCC (`ccc restart dashboard`).
