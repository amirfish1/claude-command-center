# Voice mode (Codex realtime)

Talk to Claude Command Center by voice: tap the floating phone button on mobile,
or press the mic button in the desktop top bar,
speak, and a voice answers questions about your board: what's waiting on
you, what a session is doing, how the queues look. It can also act: "send
the ads session a nudge" becomes a confirmation card in the dashboard that
runs only when you click **Confirm**.

## Requirements

- **Codex CLI 0.160.0 or newer** installed (`codex app-server` ships the
  realtime voice host this builds on).
- **Signed in to Codex with a ChatGPT account** (`codex login`). That is
  all the default path needs: realtime voice over WebRTC works on the
  ChatGPT subscription, subject to your plan's usage limits.
- **Optional:** an OpenAI API key in a BYOK profile (Settings > BYOK,
  provider `openai`) enables the paid websocket fallback described below.
- **A WebRTC-capable browser** (Chrome, Edge, Safari, Firefox) and a
  microphone.

> **Experimental:** `thread/realtime/*` is an experimental Codex app-server
> API. Method names and event shapes can change between CLI releases.

## Two transports, two ways to pay

- **WebRTC (default, subscription).** The browser's `RTCPeerConnection`
  peers directly with the Codex voice host on loopback; audio never
  transits CCC. Auth is your ChatGPT login: CCC starts the Codex child
  with `OPENAI_API_KEY` *removed* from its environment, so billing can
  never silently switch to an API key. The usage line in the panel timer
  shows audio duration and plan headroom reported by the realtime lane;
  CCC records duration only, no dollar charge.
- **Websocket (optional fallback, billed per use).** If the subscription
  call can't connect *and* a BYOK profile holds an OpenAI key, CCC
  retries once on the websocket transport with the key injected into the
  child environment only (never argv, logs, disk, or API responses). The
  panel shows a "billed per use" label whenever this path is active.
  Disable it in Settings > Voice mode with the "Allow paid API-key
  fallback" toggle.

## Using it

1. On mobile, tap the floating green **CALL** button to open the panel and
   start calling. Drag the button to move it; its position is remembered.
   On desktop, click the **mic button** in the top bar, then press **CALL**.
2. Allow microphone access.
   The panel shows `connecting` then `listening`. Once audio connects, the
   voice welcomes you: "Hi, what's up?"
3. Speak naturally. Transcript lines for you and the voice stream into
   the panel in real time, and the reply is spoken aloud.
4. If the voice proposes an action (injecting into a session, spawning
   one, filing a WatchTower ticket), a card appears in the panel. Nothing
   runs until you click **Confirm** or **Dismiss**: spoken "yes" is never
   enough, by design.
5. Press **END CALL** (or close the tab) to end the session. The red dot on
   the mic button means a session is live.

Hide the panel to keep using the dashboard during a call. The floating
button then says **IN CALL**; tapping it reopens the panel.

You can ask "Why does QUEUE-123 need input?" to hear its recorded question
and recent comments, or "Which tickets in this queue need me?" for a queue
summary. If live ticket details cannot be fetched, the voice labels cached
details as stale.

Ask "Start a Deep session to triage this queue" to propose a focused triage
session in the queue's configured repository. Configure the **Deep** model
profile in CCC Settings first, or request an explicit engine and model.
The session checks blockers, human input requests, and worker claims, then
reports priorities. It starts only after you press **Confirm** on its card.

## Settings (Settings > Voice mode)

- **Voice**: pick any realtime voice the installed Codex advertises
  (defaults to `cove`).
- **Allow paid API-key fallback**: retry on the websocket transport with
  a BYOK OpenAI key when the subscription call fails (default on).
- **Fallback profile**: which keychain profile supplies the OpenAI key
  for the fallback. Auto picks the first profile that has one.
- **Max session**: hard stop (default 15 minutes).
- **Stop after silence**: auto-stop after this much quiet (default 120
  seconds).
- **Save transcripts**: off by default. When on, transcripts land in
  `~/.claude/command-center/voice/transcripts/` (mode 0600). Raw audio is
  never stored anywhere.

## How it works

- CCC relays only the SDP offer/answer plus control calls; on the
  subscription path, mic and reply audio flow browser <-> voice host
  directly. Transcript/state events stream to the panel over SSE
  (`/api/voice/events`).
- The backing Codex thread is ephemeral, sandboxed read-only, with
  `approvalPolicy: never`. Its window into CCC is the dynamic
  tools: `ccc_attention`, `ccc_session`, `ccc_sessions`, `ccc_queues`,
  `ccc_ticket`, `ccc_jobs` (scheduled jobs: systemd timers and cron),
  `ccc_models` (all read-only; each result names its source and as-of time), plus `ccc_triage_queue` and
  `ccc_propose_action`, which can only create a pending-action card
  through the same confirmation path the Ask agent uses. Approval
  requests from the model are denied outright.
- Each session seeds a small briefing from already-cached board data
  (attention feed + queue rollup) so the voice can answer "what needs
  me" without any scanning.
- **One voice session at a time** per CCC server; a second start
  returns `voice_busy`.
- Sessions end on stop, on silence, at the max length, on tab close
  (beacon + heartbeat watchdog), or when the server exits.

## API

| Route | Purpose |
|---|---|
| `POST /api/voice/start` | Start a session. Body: `sdp_offer` (WebRTC offer), optional `transport` (`auto`|`webrtc`|`websocket`), `voice`, `profile`. Returns `session_id`, `transport`, `billing` (`subscription`|`api_key`), and `sdp_answer` for WebRTC. |
| `POST /api/voice/stop` | Stop the active session (`session_id`, `reason`). |
| `POST /api/voice/heartbeat` | Browser liveness check-in (`session_id`, optional `audio_ms`). Set `connected: true` after audio connects to request the one-time welcome. |
| `POST /api/voice/audio` | Mic chunk for the websocket fallback only: base64 PCM16, `sampleRate`, `numChannels`. |
| `GET /api/voice/status` | Active/last session, config, which BYOK profiles hold an OpenAI key. |
| `GET /api/voice/events?session_id=…&after=N` | SSE: `state`, `transcript_delta`, `transcript`, `action`, `tool`, `audio` (websocket only), `error`, `closed`. |
| `GET /api/voice/voices` | Voice catalog (live `listVoices` once a session has run, else built-in list). |
| `GET/POST /api/voice/config` | Read/write voice settings. |

Typed errors: `voice_no_openai_key`, `voice_busy`, `voice_no_codex`,
`voice_bad_request`, `voice_sideband_failed`, `voice_connect_timeout`,
`voice_audio_failed`, `voice_start_failed`, `voice_no_session`.

## Cost and privacy

- The default path bills against your ChatGPT subscription plan limits,
  not a metered API key; the panel timer shows the audio duration and
  plan headroom the realtime lane reports. The 15-minute cap and
  2-minute silence timeout still apply so a forgotten session can't run
  for hours. If the websocket fallback kicks in, OpenAI bills realtime
  audio per minute against the BYOK key and the panel says "billed per
  use".
- Mic audio goes browser -> local voice host -> OpenAI realtime API (on
  the fallback it is relayed through CCC's app-server child instead).
  CCC sees transcripts only, keeps them in memory for the session, and
  never stores raw audio.
- Board data reaches the model only inside the realtime session you
  started.

## Troubleshooting

- **"did not answer the WebRTC offer in time"** (`voice_connect_timeout`)
  or **sideband errors**: some ChatGPT accounts hit a known upstream
  issue (openai/codex #35094): the realtime call is created but the
  sideband join fails with 404 `call_id_not_found` or 403, and the
  app-server may go silent. CCC waits ~20s, then reports a typed error
  instead of spinning retries. If a BYOK OpenAI key exists and the
  fallback toggle is on, CCC retries once on the billed websocket path;
  otherwise re-try later or check realtime availability for your plan.
- **"Codex CLI not found"**: install/update Codex CLI (0.160.0+); CCC's
  usual codex binary resolution applies.
- **Mic permission denied**: the browser blocked it; allow mic for the
  CCC origin and retry.
- **Nothing is heard back**: check the panel state pill: `thinking`
  means the backing thread is working; `listening` with silence after a
  reply usually means system output volume or a muted speaker icon in
  the panel.
- **`voice_busy`**: another tab or window has the live session; only one
  runs at a time.
- **"requires API key auth"**: you asked for the websocket transport
  explicitly (or the fallback ran) with no OpenAI key configured; add
  one under Settings > BYOK.

## Known limits

- Experimental API surface; Codex CLI upgrades can shift behavior.
- Some ChatGPT accounts hit the upstream sideband join failure above;
  the API-key fallback is the workaround until OpenAI fixes it.
- Single session, single dashboard instance.
- The voice can describe board state but not read full transcripts aloud
  (by design: the read-only tools return summaries, not raw logs).
