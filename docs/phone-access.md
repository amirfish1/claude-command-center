# Phone access

Open Claude Command Center on your phone in about a minute, with no
networking knowledge needed. CCC puts itself on your private
[Tailscale](https://tailscale.com) network (your "tailnet") and shows a QR
code. Scan it on a phone that is signed in to the same Tailscale account and
you have the full dashboard: read sessions, send messages, answer agents.

Nothing goes through a CCC server or any new cloud service. Traffic moves
between your own devices over Tailscale's encrypted WireGuard tunnel, and CCC
itself still only listens on `127.0.0.1`.

## Turn it on

1. **Settings > Experimental > Phone access** (the flag is off by default).
2. **Settings > Phone access…** opens the wizard. It checks, live:
   - **Tailscale on this computer**: installed, running, and signed in. If
     not, it links to the download page (or the Mac App Store) and to the
     sign-in page Tailscale reports.
   - **Put CCC on your tailnet**: one button. CCC runs
     `tailscale serve --bg --https=<port> http://127.0.0.1:<ccc-port>` and
     records exactly what it created.
   - **Your phone URL**: shown as a QR code with a copy button, for example
     `https://my-laptop.example-tailnet.ts.net:8443/`.
   - **Test**: sends a real POST through that URL and reports pass or the
     exact reason it failed.
3. **On your phone**: install Tailscale (App Store / Google Play), sign in
   with the **same account** you used on the computer, then scan the QR
   code. Add it to your home screen if you like.

### What the button does, and doesn't

- **Picks a free HTTPS port.** 443 gives the cleanest URL. If any other
  `tailscale serve` entry already uses 443 (even a stale one pointing at a
  process that no longer exists), CCC leaves it alone and tries 8443, 10000,
  4443, 9443, 10443 in turn. Busy ports are listed in the wizard so you can
  see what else is being served.
- **Reuses an existing entry** that already points at this CCC's port,
  instead of creating a second one.
- **Trusts the new address immediately.** The `https://<name>.ts.net[:port]`
  origin is added to CCC's same-origin allowlist without a restart. So is
  anything you add in **Network access…**: `network.json` is re-read when it
  changes.
- **Turn off** removes only the serve entry CCC created, and only if it still
  points at CCC. An entry you made yourself (or edited since) is forgotten by
  CCC, never deleted.
- **Also serves the conversation-reader port.** Conversation opens come from
  a helper process on `<ccc-port> + 10000`, so a busy dashboard doesn't
  delay them. CCC adds a second tailnet-only entry,
  `tailscale serve --bg --https=<ccc-port + 10000> http://127.0.0.1:<ccc-port + 10000>`,
  on the same HTTPS port number, and Turn off removes it with the first.
  Installs that turned Phone access on before this existed get the entry at
  the next dashboard start. If that port is taken, conversations still open,
  just through the dashboard. **Set up `tailscale serve` by hand?** Add that
  second entry yourself; without it the UI quietly uses the dashboard port.
- **Never uses Funnel.** `tailscale serve` is tailnet-only; nothing is
  published to the public internet.

### First-time Tailscale prompts

The wizard translates the common first-run errors:

| Message | What to do |
|---|---|
| *Serve is not enabled on your tailnet* | Open the link shown (Tailscale admin console) and enable Serve, then press the button again. |
| *HTTPS certificates are not enabled* | Enable HTTPS on the admin console's DNS page. |
| *No MagicDNS name* | Enable MagicDNS on the same page. |
| *Access denied* (Linux) | Run `sudo tailscale set --operator=$USER` once, so CCC can manage serve without root. |

## Test failures, explained

| Result | Meaning |
|---|---|
| `cross_origin` | The URL reached CCC but CCC refused the phone's origin. Turn phone access off and on again to re-record the current address (for example after the machine was renamed). |
| `serve_conflict` | Something other than this CCC answered on that URL. Another serve entry owns the port. |
| `serve_backend_down` | Tailscale answered but CCC behind it didn't (wrong local port, CCC restarting). |
| `tailscale_down` / `dns` / `timeout` | This computer can't reach its own tailnet name: Tailscale is stopped, signed out, or MagicDNS is off. |

## Remote nodes (Fleet)

If you have paired other CCC nodes (a cloud VM, a home server; see
[`federation.md`](federation.md)), the **Fleet** page lists each node's phone
URL and status. **Set up…** / **Details…** opens the same wizard for that
node; its buttons (turn on, Test, Turn off) run on that node over the existing pairing channel, against that node's own
Tailscale. Each node needs Tailscale installed and signed in to the same
account as your phone.

## Security

**Anyone on your tailnet who has the URL can use CCC, and CCC has no login.**
Using CCC means running commands as your user on that machine. Only turn this
on for a tailnet you control, and keep it that way:

- **Use Tailscale ACLs** to limit who can reach this machine, for example
  only your own devices (`"src": ["autogroup:member"]` is often too broad on a
  shared tailnet; prefer your own user or a tag). Shared-in nodes and
  invited users are "on your tailnet" too.
- **Set a PIN (optional).** In the wizard, **PIN for phone access** requires
  a PIN (4 to 64 characters, no spaces) from any request that arrives from off this
  machine: through `tailscale serve`, a tunnel, or a non-loopback bind.
  Requests from this computer (`127.0.0.1`) are never asked. The phone enters
  the PIN once and gets a 30-day, HttpOnly, `SameSite=Strict` session cookie.
  The PIN is stored salted and hashed (PBKDF2), wrong guesses are rate
  limited, and changing the PIN signs out every phone.
- **Settings stay local.** Turning phone access on or off, changing the PIN,
  and editing **Network access…** only work from this computer, never from
  the phone, so a device you let in can't widen its own access.

See [`SECURITY.md`](../SECURITY.md) for the full model.

## Without Tailscale

These work, but you give up something Tailscale provides. Read the risks.

### Same Wi-Fi (LAN)

```bash
CCC_BIND_HOST=0.0.0.0 CCC_ALLOWED_ORIGIN=http://192.168.1.20:8090 ./run.sh
```

Then open `http://192.168.1.20:8090` on the phone (use your computer's LAN
address).

**Risks:** plain HTTP, so anyone on the same network can read the traffic.
Every device on that Wi-Fi can reach CCC and run commands as you. Only do this
on a network you fully trust (never a café or office Wi-Fi), and set a PIN:
the PIN gate applies to non-loopback callers here too. Your address changes
when the router hands out a new one.

### Cloudflare Tunnel

`cloudflared tunnel` can front `http://127.0.0.1:8090` with an HTTPS
hostname on a domain you own. Add that origin under **Network access…**
(it takes effect immediately), and set a PIN.

**Risks:** this puts CCC on the **public internet**. Anyone who finds the
hostname can reach it, and the PIN is then the only thing standing between
them and your machine. Traffic is decrypted at Cloudflare's edge. Put
[Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
(an identity login in front of the tunnel) in front of it, or don't do this.
CCC never sets this up for you.
