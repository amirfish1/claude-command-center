# Browser voice

**Settings → Browser voice** enables Phonon 31M English read-aloud on this
device. It is on by default; each browser can turn it off independently.
The read-aloud master switch disables it too. Initial preparation downloads approximately 65 MB of
model data, plus the small WASM runtime, to the CCC host. The browser caches the
verified model too. Reads use the existing voice while the model loads, for
Hebrew, and after any failure. Turning browser voice off releases its worker/model.

The browser voice uses one WASM thread in a dedicated worker, with an AudioWorklet
player and at most one second of queued PCM. It adds no cross-origin isolation
headers and changes no CPU priority. Pause applies backpressure to synthesis;
Stop cancels it. Repeated underruns stop browser voice on that browser and restore
the existing voice. The switch can retry it. Word highlighting is an estimate,
Phonon supplies no exact word alignment.

Browser voice is the default for English. Check real Mac and iPhone
playback, including long English reads, numbers/dates, code prose, a busy page,
stop/resume, and phone backgrounding. Empty-cache download, resident-model
start, and the first non-silent output are separate measurements. The existing
start and gap targets still apply; no target-device or listening-quality pass
is implied by the switch being available. Browser-voice events retain first
PCM timing, audio-clock start, underruns, cancellations and buffer high-water
marks in the normal speech diagnostics.

Resident model starts are tagged `engine=browser` and `mode=browser`.
Include `target!=selection` when comparing the resident start gate. Do not exclude browser starts or failed
reads from the browser voice population. Preserve the separate cold-download window.

## Distribution and upstream notices

No model or WASM binary is stored in this repository. Default preparation calls
`POST /api/browser-voice/install`; the stdlib installer downloads only the
allowlisted assets, verifies their pinned SHA-256 hashes, and publishes a
complete pack atomically. GET status does not start downloads. The server
serves the installed pack on versioned local URLs. The cache lives under
`~/.ccc/browser-voice/` (`CCC_BROWSER_VOICE_DIR` overrides it).

- Runtime: [phonon-tts 0.7.1](https://www.npmjs.com/package/phonon-tts/v/0.7.1),
  MIT OR Apache-2.0. Archive SHA-256:
  `f2c936ad4e0caeb60afd9b704157708eb2b32ba361ff4c29e0c3960e87de4f88`.
  Its `LICENSE-MIT`, `LICENSE-APACHE` and `THIRD_PARTY_LICENSES.md` accompany
  the downloaded runtime, accessible under the local pack's `runtime/` path.
- Weights, tokenizer, config and default catalogue voice:
  [gradium/phonon-31m-en at 92741c5b07d085ff840e26fea8ce43110b87852f](https://huggingface.co/gradium/phonon-31m-en/tree/92741c5b07d085ff840e26fea8ce43110b87852f),
  Apache-2.0. The model card accompanies the downloaded pack as
  `model/README.md`; individual size/hash pins are in
  `ccc_server/browser_voice.py`. This voice never downloads 40M models.

The existing CCC origin, proxy-secret and optional phone PIN checks also apply
to these endpoints. No listening text goes to an upstream download service.
