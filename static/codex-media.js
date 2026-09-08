/* Copyright (c) 2026 Amir Fish. All rights reserved. SPDX-License-Identifier: LicenseRef-CCC-Software-License */
(function () {
  'use strict';

  const OUTPUT_LIMIT = 256 * 1024;
  const TRANSCRIPT_LIMIT = 64 * 1024;
  const AUDIO_SAMPLE_RATE = 24000;
  const AUDIO_QUEUE_LIMIT = 6;
  const PLAYBACK_SOURCE_LIMIT = 24;
  const PLAYBACK_AHEAD_LIMIT_SECONDS = 5;
  const PLAYBACK_CHUNK_BYTES_LIMIT = 512 * 1024;
  const REALTIME_CLOSE_TIMEOUT_MS = 10000;
  const EXEC_TIMEOUT_MS = 30 * 60 * 1000;
  const EXEC_OUTPUT_CAP = 1024 * 1024;
  const RESIZE_DELAY_MS = 140;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(text, attr) {
    const node = element('button', 'codex-media-button', text);
    node.type = 'button';
    if (attr) node.setAttribute(attr, '');
    return node;
  }

  function available(catalog, method) {
    return !!((catalog && catalog.methods) || []).find(row => row && row.method === method && row.available !== false);
  }

  function uuid(prefix) {
    const id = window.crypto && typeof window.crypto.randomUUID === 'function'
      ? window.crypto.randomUUID()
      : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    return prefix + '-' + id;
  }

  function conciseError(error) {
    if (!error) return 'Unknown native error';
    if (error.uncertain || error.payload && error.payload.uncertain) return 'Codex could not confirm whether that action completed. Check the task before trying again.';
    return String(error.message || error.error || error).replace(/\s+/g, ' ').slice(0, 500);
  }

  function uncertainError(error) {
    if (!error) return false;
    if (error.uncertain || error.payload && error.payload.uncertain) return true;
    return /could not confirm|state is uncertain|may still be running/i.test(String(error.message || error.error || error));
  }

  function bytesToBase64(bytes) {
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  }

  function base64ToBytes(value) {
    const binary = atob(String(value || ''));
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function parseArgv(value) {
    const input = String(value || '');
    const args = [];
    let current = '';
    let quote = '';
    let escaped = false;
    let started = false;
    for (const char of input) {
      if (escaped) { current += char; escaped = false; started = true; continue; }
      if (char === '\\' && quote !== "'") { escaped = true; started = true; continue; }
      if (quote) {
        if (char === quote) quote = '';
        else current += char;
        started = true;
        continue;
      }
      if (char === '"' || char === "'") { quote = char; started = true; continue; }
      if (/\s/.test(char)) {
        if (started) { args.push(current); current = ''; started = false; }
        continue;
      }
      current += char;
      started = true;
    }
    if (escaped) current += '\\';
    if (quote) throw new Error('Close the quoted argument before running the command.');
    if (started) args.push(current);
    if (!args.length) throw new Error('Enter a program and its arguments.');
    return args;
  }

  function shellArgv(command, mode) {
    if (mode === 'powershell') return ['powershell.exe', '-NoLogo', '-NoProfile', '-Command', command];
    return ['/bin/sh', '-lc', command];
  }

  function terminalSize(rect) {
    return {
      cols: Math.max(20, Math.min(500, Math.floor((rect && rect.width || 640) / 8))),
      rows: Math.max(2, Math.min(200, Math.floor((rect && rect.height || 288) / 18))),
    };
  }

  function resampleMono(inputBuffer, targetRate) {
    const channelCount = Math.max(1, Number(inputBuffer.numberOfChannels) || 1);
    const sourceLength = inputBuffer.getChannelData(0).length;
    const mono = new Float32Array(sourceLength);
    for (let channel = 0; channel < channelCount; channel++) {
      const samples = inputBuffer.getChannelData(channel);
      for (let index = 0; index < sourceLength; index++) mono[index] += samples[index] / channelCount;
    }
    const sourceRate = Number(inputBuffer.sampleRate) || Number(targetRate);
    if (sourceRate === targetRate) return mono;
    const length = Math.max(1, Math.round(mono.length * targetRate / sourceRate));
    const output = new Float32Array(length);
    const ratio = sourceRate / targetRate;
    for (let index = 0; index < length; index++) {
      const position = index * ratio;
      const low = Math.min(mono.length - 1, Math.floor(position));
      const high = Math.min(mono.length - 1, low + 1);
      const fraction = position - low;
      output[index] = mono[low] + (mono[high] - mono[low]) * fraction;
    }
    return output;
  }

  function pcm16Base64(samples) {
    const buffer = new ArrayBuffer(samples.length * 2);
    const view = new DataView(buffer);
    for (let index = 0; index < samples.length; index++) {
      const sample = Math.max(-1, Math.min(1, samples[index]));
      view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return bytesToBase64(new Uint8Array(buffer));
  }

  function attach(ports) {
    if (!ports || !ports.root || !ports.context || typeof ports.operation !== 'function') {
      throw new TypeError('CCCodexMedia.attach requires root, context, and operation ports.');
    }
    const catalog = ports.catalog || {};
    const context = ports.context;
    const realtimeCapabilities = {
      audio: available(catalog, 'thread/realtime/appendAudio'),
      text: available(catalog, 'thread/realtime/appendText'),
      speech: available(catalog, 'thread/realtime/appendSpeech'),
      stop: available(catalog, 'thread/realtime/stop'),
    };
    const listeners = [];
    const timers = new Set();
    const state = {
      disposed: false,
      disposePromise: null,
      terminal: null,
      realtime: { active: false, token: 0, nativeToken: null, queue: [], uploadingToken: null,
        dropped: 0, playbackDropped: 0, stream: null, audioContext: null, source: null,
        processor: null, playback: new Set(), playbackAt: 0, startAttempt: null,
        phase: 'idle', startedSeq: null, closeFenceSeq: null, stoppingToken: null,
        stoppingNeedsStarted: false, pendingStopStatus: null, disposeClose: null },
    };

    const root = element('section', 'codex-media');
    root.setAttribute('aria-label', 'Terminal and voice');
    ports.root.append(root);

    function listen(node, type, handler) {
      node.addEventListener(type, handler);
      listeners.push(() => node.removeEventListener(type, handler));
    }

    function later(handler, delay) {
      const timer = window.setTimeout(() => { timers.delete(timer); handler(); }, delay);
      timers.add(timer);
      return timer;
    }

    function report(message, statusNode) {
      const text = String(message || 'Native operation failed.');
      if (statusNode && !state.disposed) statusNode.textContent = text;
      if (!state.disposed && typeof ports.error === 'function') ports.error(text);
    }

    function invoke(method, params, statusNode) {
      try {
        return Promise.resolve(ports.operation(method, params)).catch(error => {
          report(conciseError(error), statusNode);
          throw error;
        });
      } catch (error) {
        report(conciseError(error), statusNode);
        return Promise.reject(error);
      }
    }

    function rawOperation(method, params) {
      try { return Promise.resolve(ports.operation(method, params)); }
      catch (error) { return Promise.reject(error); }
    }

    const terminalPanel = element('section', 'codex-media-panel');
    terminalPanel.setAttribute('aria-labelledby', 'codex-media-terminal-title');
    const terminalTitle = element('h3', 'codex-media-title', 'Terminal');
    terminalTitle.id = 'codex-media-terminal-title';
    const terminalForm = element('form', 'codex-media-terminal-form');
    terminalForm.setAttribute('data-codex-terminal-form', '');
    const terminalFields = element('div', 'codex-media-fields');
    const backend = element('select', 'codex-media-select');
    backend.setAttribute('aria-label', 'Terminal backend');
    backend.setAttribute('data-codex-terminal-backend', '');
    backend.append(new Option('Sandboxed command', 'command'));
    if (available(catalog, 'process/spawn')) backend.append(new Option('Host process (preview)', 'process'));
    const mode = element('select', 'codex-media-select');
    mode.setAttribute('aria-label', 'Command entry mode');
    mode.setAttribute('data-codex-command-mode', '');
    mode.append(new Option('Program and arguments', 'program'));
    const serverPlatform = String(catalog.server_platform || catalog.platform || '').toLowerCase();
    if (/^(win32|windows)$/.test(serverPlatform)) {
      mode.append(new Option('PowerShell command', 'powershell'), new Option('POSIX shell command', 'posix-shell'));
    } else {
      mode.append(new Option('POSIX shell command', 'posix-shell'), new Option('PowerShell command', 'powershell'));
    }
    const ttyLabel = element('label', 'codex-media-check');
    const tty = element('input'); tty.type = 'checkbox'; tty.checked = true; tty.setAttribute('data-codex-tty', '');
    ttyLabel.append(tty, document.createTextNode(' Interactive'));
    const command = element('input', 'codex-media-command');
    command.type = 'text'; command.autocomplete = 'off'; command.placeholder = 'Program and arguments';
    command.setAttribute('aria-label', 'Program or shell command'); command.setAttribute('data-codex-command', '');
    const run = element('button', 'codex-media-button is-primary', 'Run'); run.type = 'submit';
    terminalFields.append(backend, mode, ttyLabel, command, run);
    terminalForm.append(terminalFields);
    const output = element('pre', 'codex-media-output');
    output.tabIndex = 0; output.setAttribute('aria-label', 'Terminal output'); output.setAttribute('data-codex-terminal-output', '');
    const terminalStatus = element('div', 'codex-media-status', 'Ready');
    terminalStatus.setAttribute('role', 'status'); terminalStatus.setAttribute('data-codex-terminal-status', '');
    const running = element('div', 'codex-media-running'); running.hidden = true; running.setAttribute('data-codex-terminal-running', '');
    const stdin = element('input', 'codex-media-stdin'); stdin.type = 'text'; stdin.placeholder = 'Send input'; stdin.setAttribute('data-codex-stdin', '');
    const sendStdin = button('Send', 'data-codex-stdin-send');
    const closeStdin = button('Close input', 'data-codex-stdin-close');
    const stopTerminalButton = button('Stop', 'data-codex-terminal-stop');
    running.append(stdin, sendStdin, closeStdin, stopTerminalButton);
    terminalPanel.append(terminalTitle, terminalForm, output, terminalStatus, running);
    root.append(terminalPanel);

    function appendOutput(text, capReached) {
      if (state.disposed || !text && !capReached) return;
      let next = output.textContent + String(text || '');
      if (capReached) next += '\n[Output limit reached]\n';
      if (next.length > OUTPUT_LIMIT) next = '[Earlier output discarded]\n' + next.slice(-(OUTPUT_LIMIT - 28));
      output.textContent = next;
      output.scrollTop = output.scrollHeight;
    }

    function terminalFollowupFor(session, commandMethod, processMethod, extra) {
      if (!session || state.terminal !== session || !session.running || state.disposed) return Promise.resolve();
      const method = session.backend === 'process' ? processMethod : commandMethod;
      const handle = session.backend === 'process' ? { processHandle: session.id } : { processId: session.id };
      return invoke(method, Object.assign(handle, extra || {}), terminalStatus).catch(() => {});
    }

    function terminalFollowup(commandMethod, processMethod, extra) {
      return terminalFollowupFor(state.terminal, commandMethod, processMethod, extra);
    }

    function cancelTerminalResize(session) {
      if (!session || !session.resizeTimer) return;
      window.clearTimeout(session.resizeTimer);
      timers.delete(session.resizeTimer);
      session.resizeTimer = null;
    }

    function finishTerminal(exitCode, stdout, stderr) {
      const session = state.terminal;
      if (!session || !session.running) return;
      cancelTerminalResize(session);
      appendOutput(session.stdoutDecoder.decode());
      appendOutput(session.stderrDecoder.decode());
      appendOutput(stdout || ''); appendOutput(stderr || '');
      session.running = false;
      running.hidden = true;
      terminalStatus.textContent = 'Exited ' + exitCode;
    }

    function startTerminal(event) {
      event.preventDefault();
      if (state.disposed || state.terminal && state.terminal.running) return;
      let argv;
      try {
        argv = mode.value !== 'program'
          ? shellArgv(command.value, mode.value)
          : parseArgv(command.value);
      } catch (error) { report(error.message, terminalStatus); return; }
      const selectedBackend = backend.value === 'process' ? 'process' : 'command';
      const method = selectedBackend === 'process' ? 'process/spawn' : 'command/exec';
      if (!available(catalog, method)) { report('This Codex server does not offer ' + method + '.', terminalStatus); return; }
      cancelTerminalResize(state.terminal);
      const id = uuid(selectedBackend === 'process' ? 'ccc-process' : 'ccc-command');
      const size = terminalSize(output.getBoundingClientRect());
      const params = {
        command: argv, cwd: context.repoPath, streamStdoutStderr: true, streamStdin: true,
        tty: !!tty.checked, timeoutMs: EXEC_TIMEOUT_MS, outputBytesCap: EXEC_OUTPUT_CAP,
      };
      if (tty.checked) params.size = size;
      if (selectedBackend === 'process') params.processHandle = id;
      else params.processId = id;
      state.terminal = {
        id, backend: selectedBackend, tty: !!tty.checked, running: true, stopSent: false, resizeTimer: null,
        stdoutDecoder: new TextDecoder(), stderrDecoder: new TextDecoder(),
      };
      output.textContent = '';
      running.hidden = false;
      terminalStatus.textContent = 'Running';
      let pending;
      try { pending = Promise.resolve(ports.operation(method, params)); }
      catch (error) { pending = Promise.reject(error); }
      pending.then(result => {
        if (state.disposed || !state.terminal || state.terminal.id !== id) return;
        if (selectedBackend === 'command') finishTerminal(result && result.exitCode, result && result.stdout, result && result.stderr);
        else terminalStatus.textContent = 'Running';
      }).catch(error => {
        if (state.disposed || !state.terminal || state.terminal.id !== id) return;
        if (selectedBackend === 'command' && uncertainError(error)) {
          state.terminal.uncertainMessage = conciseError(error);
          report(state.terminal.uncertainMessage, terminalStatus);
          return;
        }
        state.terminal.running = false; running.hidden = true;
        report(conciseError(error), terminalStatus);
      });
    }

    listen(terminalForm, 'submit', startTerminal);
    listen(mode, 'change', () => { command.placeholder = mode.value === 'program' ? 'Program and arguments' : 'Shell command'; });
    listen(sendStdin, 'click', () => {
      if (!stdin.value) return;
      const line = stdin.value.endsWith('\n') ? stdin.value : stdin.value + '\n';
      const deltaBase64 = bytesToBase64(new TextEncoder().encode(line));
      stdin.value = '';
      terminalFollowup('command/exec/write', 'process/writeStdin', { deltaBase64, closeStdin: false });
    });
    listen(closeStdin, 'click', () => terminalFollowup('command/exec/write', 'process/writeStdin', { closeStdin: true }));
    listen(stopTerminalButton, 'click', () => {
      if (!state.terminal || state.terminal.stopSent) return;
      state.terminal.stopSent = true;
      terminalStatus.textContent = state.terminal.uncertainMessage
        ? state.terminal.uncertainMessage + ' Stop requested.'
        : 'Stopping…';
      terminalFollowup('command/exec/terminate', 'process/kill');
    });

    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
      const session = state.terminal;
      if (!session || !session.running || !session.tty || !entries[0]) return;
      cancelTerminalResize(session);
      const size = terminalSize(entries[0].contentRect);
      session.resizeTimer = later(() => {
        session.resizeTimer = null;
        terminalFollowupFor(session, 'command/exec/resize', 'process/resizePty', { size });
      }, RESIZE_DELAY_MS);
    }) : null;
    if (resizeObserver) resizeObserver.observe(output);

    let realtimePanel = null;
    let realtimeStatus = null;
    let transcript = null;
    let voiceSelect = null;
    let realtimeStart = null;
    let realtimeStop = null;

    function stopStream(stream) {
      if (stream && typeof stream.getTracks === 'function') stream.getTracks().forEach(track => {
        try { track.stop(); } catch (_) {}
      });
    }

    function releaseRealtimeMedia() {
      const media = state.realtime;
      stopStream(media.stream); media.stream = null;
      if (media.processor) { media.processor.onaudioprocess = null; try { media.processor.disconnect(); } catch (_) {} }
      if (media.source) { try { media.source.disconnect(); } catch (_) {} }
      media.processor = null; media.source = null;
      media.playback.forEach(source => { try { source.stop(); } catch (_) {} });
      media.playback.clear();
      media.playbackAt = 0;
      if (media.audioContext) { try { media.audioContext.close(); } catch (_) {} }
      media.audioContext = null;
      media.queue.length = 0;
    }

    function beginDisposeCloseBarrier() {
      const media = state.realtime;
      if (media.disposeClose) return media.disposeClose.promise;
      let resolve;
      let reject;
      const barrier = { settled: false, timer: null, promise: null };
      barrier.promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
      });
      barrier.resolve = () => {
        if (barrier.settled) return;
        barrier.settled = true;
        window.clearTimeout(barrier.timer);
        resolve();
      };
      barrier.timer = window.setTimeout(() => {
        if (barrier.settled) return;
        barrier.settled = true;
        reject(new Error('Realtime session did not close before the cleanup deadline.'));
      }, REALTIME_CLOSE_TIMEOUT_MS);
      media.disposeClose = barrier;
      return barrier.promise;
    }

    function resolveDisposeCloseBarrier() {
      const barrier = state.realtime.disposeClose;
      if (barrier) barrier.resolve();
    }

    function setRealtimeActive(active) {
      const media = state.realtime;
      media.active = active;
      if (realtimeStart) realtimeStart.disabled = active || media.phase === 'stopping' || !realtimeCapabilities.stop;
      if (realtimeStop) realtimeStop.disabled = !active || media.phase === 'stopping' || !realtimeCapabilities.stop;
    }

    function stopNativeRealtime(token) {
      const media = state.realtime;
      if (media.nativeToken !== token || !realtimeCapabilities.stop) return Promise.resolve();
      media.nativeToken = null;
      return invoke('thread/realtime/stop', { threadId: context.threadId }, realtimeStatus).catch(() => {});
    }

    function failRealtime(message, token) {
      const media = state.realtime;
      token = token === undefined ? media.token : token;
      if (token !== media.token) return Promise.resolve();
      const expectsClose = media.nativeToken === token;
      const wasStarting = media.phase === 'starting';
      media.token++;
      media.phase = expectsClose ? 'stopping' : 'idle';
      media.stoppingToken = expectsClose ? token : null;
      media.stoppingNeedsStarted = expectsClose && wasStarting;
      media.pendingStopStatus = message;
      setRealtimeActive(false);
      releaseRealtimeMedia();
      report(message, realtimeStatus);
      return stopNativeRealtime(token);
    }

    async function pumpAudio(token) {
      const media = state.realtime;
      if (media.uploadingToken !== null || state.disposed) return;
      media.uploadingToken = token;
      try {
        while (!state.disposed && media.active && media.token === token && media.queue.length) {
          const entry = media.queue.shift();
          if (!entry || entry.token !== token) continue;
          try { await ports.operation('thread/realtime/appendAudio', { threadId: context.threadId, audio: entry.audio }); }
          catch (error) {
            if (media.token === token) failRealtime(conciseError(error), token);
            break;
          }
        }
      } finally {
        if (media.uploadingToken === token) media.uploadingToken = null;
        if (!state.disposed && media.active && media.queue.length) pumpAudio(media.token);
      }
    }

    function queueAudio(inputBuffer, sourceRate) {
      const media = state.realtime;
      if (!media.active || state.disposed) return;
      const proxy = {
        numberOfChannels: inputBuffer.numberOfChannels,
        sampleRate: sourceRate,
        getChannelData: channel => inputBuffer.getChannelData(channel),
      };
      const samples = resampleMono(proxy, AUDIO_SAMPLE_RATE);
      const audio = { data: pcm16Base64(samples), numChannels: 1, sampleRate: AUDIO_SAMPLE_RATE, samplesPerChannel: samples.length };
      if (media.queue.length >= AUDIO_QUEUE_LIMIT) {
        media.dropped++;
        realtimeStatus.textContent = 'Listening · ' + media.dropped + ' audio chunk' + (media.dropped === 1 ? '' : 's') + ' dropped';
        return;
      }
      const token = media.token;
      media.queue.push({ token, audio });
      pumpAudio(token);
    }

    async function ensurePlaybackContext(token) {
      const media = state.realtime;
      if (media.audioContext) return media.audioContext;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error('Web Audio is unavailable in this browser.');
      const audioContext = new AudioContextClass();
      media.audioContext = audioContext;
      if (typeof audioContext.resume === 'function') await audioContext.resume();
      if (state.disposed || token !== media.token || !media.active) {
        try { audioContext.close(); } catch (_) {}
        if (media.audioContext === audioContext) media.audioContext = null;
        return null;
      }
      return audioContext;
    }

    async function acquireMicrophone(token) {
      if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') throw new Error('Microphone capture is unavailable in this browser.');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (state.disposed || token !== state.realtime.token || !state.realtime.active) { stopStream(stream); return; }
      state.realtime.stream = stream;
      const audioContext = await ensurePlaybackContext(token);
      if (!audioContext) return;
      const source = audioContext.createMediaStreamSource(stream);
      state.realtime.source = source;
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      state.realtime.processor = processor;
      processor.onaudioprocess = event => queueAudio(event.inputBuffer, audioContext.sampleRate);
      source.connect(processor); processor.connect(audioContext.destination);
      realtimeStatus.textContent = 'Listening';
    }

    async function startRealtime() {
      if (state.disposed || state.realtime.active || state.realtime.phase === 'stopping' || !realtimeCapabilities.stop) return;
      const token = ++state.realtime.token;
      state.realtime.phase = 'starting';
      state.realtime.startedSeq = null;
      state.realtime.stoppingToken = null;
      state.realtime.stoppingNeedsStarted = false;
      state.realtime.pendingStopStatus = null;
      state.realtime.dropped = 0;
      state.realtime.playbackDropped = 0;
      setRealtimeActive(true);
      realtimeStatus.textContent = 'Starting voice…';
      const params = { threadId: context.threadId, outputModality: 'audio' };
      if (voiceSelect && voiceSelect.value) params.voice = voiceSelect.value;
      const attempt = { token, settled: false, promise: null };
      try {
        if (!realtimeCapabilities.audio) {
          realtimeStatus.textContent = 'Starting audio output…';
          await ensurePlaybackContext(token);
          if (state.disposed || token !== state.realtime.token || !state.realtime.active) return;
        }
        attempt.promise = rawOperation('thread/realtime/start', params);
        state.realtime.startAttempt = attempt;
        await attempt.promise;
        attempt.settled = true;
        if (state.disposed) return;
        state.realtime.nativeToken = token;
        if (token !== state.realtime.token || !state.realtime.active) {
          await stopNativeRealtime(token);
          return;
        }
        if (realtimeCapabilities.audio) {
          realtimeStatus.textContent = 'Starting microphone…';
          await acquireMicrophone(token);
        } else realtimeStatus.textContent = 'Connected · microphone input unavailable';
      } catch (error) {
        attempt.settled = true;
        if (!state.disposed && token === state.realtime.token) failRealtime(conciseError(error), token);
        else if (!state.disposed && state.realtime.phase === 'stopping' && state.realtime.stoppingToken === token && state.realtime.nativeToken !== token) {
          state.realtime.phase = 'idle';
          state.realtime.stoppingToken = null;
          state.realtime.stoppingNeedsStarted = false;
          setRealtimeActive(false);
          realtimeStatus.textContent = 'Voice stopped';
        }
      }
    }

    function stopRealtime(sendNative) {
      const token = state.realtime.token;
      const wasActive = state.realtime.active;
      const wasStarting = state.realtime.phase === 'starting';
      state.realtime.token++;
      state.realtime.phase = wasActive ? 'stopping' : 'idle';
      state.realtime.stoppingToken = wasActive ? token : null;
      state.realtime.stoppingNeedsStarted = wasActive && wasStarting;
      state.realtime.pendingStopStatus = null;
      setRealtimeActive(false);
      releaseRealtimeMedia();
      if (realtimeStatus && !state.disposed) realtimeStatus.textContent = wasActive ? 'Stopping voice… waiting for native close' : 'Voice stopped';
      if (sendNative && wasActive) stopNativeRealtime(token);
    }

    function appendTranscript(value) {
      if (!transcript || !value) return;
      let next = transcript.textContent + String(value);
      if (next.length > TRANSCRIPT_LIMIT) next = '[Earlier transcript discarded]\n' + next.slice(-(TRANSCRIPT_LIMIT - 32));
      transcript.textContent = next;
      transcript.scrollTop = transcript.scrollHeight;
    }

    function playPcm(audio) {
      const media = state.realtime;
      if (!media.active || !media.audioContext || !audio || !audio.data) return;
      try {
        if (String(audio.data).length > Math.ceil(PLAYBACK_CHUNK_BYTES_LIMIT * 4 / 3) + 4) {
          throw new RangeError('Realtime audio chunk exceeds the playback limit.');
        }
        const bytes = base64ToBytes(audio.data);
        const channels = Math.max(1, Number(audio.numChannels) || 1);
        const frames = Math.floor(bytes.length / 2 / channels);
        if (!frames) return;
        const sampleRate = Number(audio.sampleRate) || AUDIO_SAMPLE_RATE;
        const currentTime = Number(media.audioContext.currentTime) || 0;
        const startAt = Math.max(currentTime, media.playbackAt || 0);
        const duration = frames / sampleRate;
        if (bytes.byteLength > PLAYBACK_CHUNK_BYTES_LIMIT || media.playback.size >= PLAYBACK_SOURCE_LIMIT || startAt + duration - currentTime > PLAYBACK_AHEAD_LIMIT_SECONDS) {
          media.playbackDropped++;
          realtimeStatus.textContent = 'Listening · ' + media.playbackDropped + ' output chunk' + (media.playbackDropped === 1 ? '' : 's') + ' dropped';
          return;
        }
        const buffer = media.audioContext.createBuffer(channels, frames, sampleRate);
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        for (let channel = 0; channel < channels; channel++) {
          const values = new Float32Array(frames);
          for (let frame = 0; frame < frames; frame++) values[frame] = view.getInt16((frame * channels + channel) * 2, true) / 0x8000;
          if (typeof buffer.copyToChannel === 'function') buffer.copyToChannel(values, channel);
          else buffer.getChannelData(channel).set(values);
        }
        const source = media.audioContext.createBufferSource();
        source.buffer = buffer; source.connect(media.audioContext.destination);
        media.playbackAt = startAt + duration;
        media.playback.add(source); source.onended = () => media.playback.delete(source); source.start(startAt);
      } catch (error) { failRealtime('Could not play realtime audio: ' + conciseError(error), media.token); }
    }

    if (available(catalog, 'thread/realtime/start')) {
      realtimePanel = element('section', 'codex-media-panel');
      realtimePanel.setAttribute('aria-labelledby', 'codex-media-realtime-title');
      const title = element('h3', 'codex-media-title', 'Voice (preview)'); title.id = 'codex-media-realtime-title';
      const voiceControls = element('div', 'codex-media-fields');
      voiceSelect = element('select', 'codex-media-select'); voiceSelect.setAttribute('aria-label', 'Voice'); voiceSelect.setAttribute('data-codex-voice', '');
      realtimeStart = button('Start', 'data-codex-realtime-start');
      realtimeStop = button('Stop', 'data-codex-realtime-stop'); realtimeStop.disabled = true;
      realtimeStart.disabled = !realtimeCapabilities.stop;
      voiceControls.append(voiceSelect, realtimeStart, realtimeStop);
      transcript = element('div', 'codex-media-transcript'); transcript.tabIndex = 0; transcript.setAttribute('aria-label', 'Voice transcript'); transcript.setAttribute('data-codex-realtime-transcript', '');
      const textRow = element('div', 'codex-media-text-row');
      const textInput = element('input', 'codex-media-command'); textInput.type = 'text'; textInput.placeholder = 'Text for the voice session'; textInput.setAttribute('data-codex-realtime-text', '');
      const sendText = button('Send text', 'data-codex-realtime-send-text');
      sendText.disabled = !realtimeCapabilities.text;
      textRow.append(textInput, sendText);
      const speakButton = button('Speak', 'data-codex-realtime-speak');
      speakButton.disabled = !realtimeCapabilities.speech;
      textRow.append(speakButton);
      realtimeStatus = element('div', 'codex-media-status', 'Voice ready'); realtimeStatus.setAttribute('role', 'status'); realtimeStatus.setAttribute('data-codex-realtime-status', '');
      const unsupported = [];
      if (!realtimeCapabilities.audio) unsupported.push('Microphone input unavailable');
      if (!realtimeCapabilities.text) unsupported.push('Text input unavailable');
      if (!realtimeCapabilities.speech) unsupported.push('Speech input unavailable');
      if (!realtimeCapabilities.stop) unsupported.push('Stop unavailable; voice cannot start safely');
      const support = element('div', 'codex-media-support', unsupported.join(' · '));
      support.setAttribute('data-codex-realtime-support', '');
      support.hidden = unsupported.length === 0;
      realtimePanel.append(title, voiceControls, transcript, textRow, support, realtimeStatus); root.append(realtimePanel);
      listen(realtimeStart, 'click', startRealtime);
      listen(realtimeStop, 'click', () => stopRealtime(true));
      if (realtimeCapabilities.text) listen(sendText, 'click', () => {
        const text = textInput.value.trim(); if (!text || !state.realtime.active) return;
        textInput.value = '';
        const token = state.realtime.token;
        try {
          Promise.resolve(ports.operation('thread/realtime/appendText', { threadId: context.threadId, text, role: 'user' }))
            .catch(error => failRealtime(conciseError(error), token));
        } catch (error) { failRealtime(conciseError(error), token); }
      });
      const speak = realtimePanel.querySelector('[data-codex-realtime-speak]');
      if (realtimeCapabilities.speech) listen(speak, 'click', () => {
        const text = textInput.value.trim(); if (!text || !state.realtime.active) return;
        textInput.value = '';
        const token = state.realtime.token;
        try {
          Promise.resolve(ports.operation('thread/realtime/appendSpeech', { threadId: context.threadId, text }))
            .catch(error => failRealtime(conciseError(error), token));
        } catch (error) { failRealtime(conciseError(error), token); }
      });
      if (available(catalog, 'thread/realtime/listVoices')) {
        invoke('thread/realtime/listVoices', {}, realtimeStatus).then(result => {
          if (state.disposed) return;
          const voices = result && result.voices || {};
          const options = Array.isArray(voices.v2) && voices.v2.length ? voices.v2 : (Array.isArray(voices.v1) ? voices.v1 : []);
          voiceSelect.replaceChildren(...options.map(name => new Option(name, name)));
          const preferred = options.includes(voices.defaultV2) ? voices.defaultV2 : voices.defaultV1;
          if (preferred && options.includes(preferred)) voiceSelect.value = preferred;
          if (!options.length) voiceSelect.append(new Option('Server default', ''));
        }).catch(() => { if (!state.disposed && !voiceSelect.options.length) voiceSelect.append(new Option('Server default', '')); });
      } else voiceSelect.append(new Option('Server default', ''));
    }

    function handleEvents(events) {
      if (!Array.isArray(events) || state.disposed && !state.realtime.disposeClose) return;
      events.forEach(event => {
        if (!event || typeof event !== 'object') return;
        const params = event.params && typeof event.params === 'object' ? event.params : event;
        const method = String(event.method || '');
        if (state.disposed && method !== 'thread/realtime/started' && method !== 'thread/realtime/closed') return;
        const seq = Number(event.seq);
        const hasSeq = Number.isFinite(seq);
        const session = state.terminal;
        if (method === 'command/exec/outputDelta' && session && session.backend === 'command' && params.processId === session.id) {
          const decoder = params.stream === 'stderr' ? session.stderrDecoder : session.stdoutDecoder;
          appendOutput(decoder.decode(base64ToBytes(params.deltaBase64), { stream: true }), !!params.capReached);
        } else if (method === 'process/outputDelta' && session && session.backend === 'process' && params.processHandle === session.id) {
          const decoder = params.stream === 'stderr' ? session.stderrDecoder : session.stdoutDecoder;
          appendOutput(decoder.decode(base64ToBytes(params.deltaBase64), { stream: true }), !!params.capReached);
        } else if (method === 'process/exited' && session && session.backend === 'process' && params.processHandle === session.id) {
          finishTerminal(params.exitCode, params.stdout, params.stderr);
        }
        if (!realtimePanel || params.threadId !== context.threadId) return;
        const media = state.realtime;
        if (method === 'thread/realtime/started') {
          if (media.phase === 'starting') {
            media.phase = 'active';
            media.startedSeq = hasSeq ? seq : null;
          } else if (media.phase === 'stopping' && media.stoppingNeedsStarted) {
            media.startedSeq = hasSeq ? seq : null;
            media.stoppingNeedsStarted = false;
          }
        } else if (method === 'thread/realtime/outputAudio/delta') playPcm(params.audio);
        else if (method === 'thread/realtime/item/transcript/delta' || method === 'thread/realtime/transcript/delta') appendTranscript(params.delta);
        else if (method === 'thread/realtime/transcript/done') appendTranscript((transcript.textContent ? '\n' : '') + (params.role ? params.role + ': ' : '') + params.text + '\n');
        else if (method === 'thread/realtime/error') {
          // Errors do not acknowledge a stop. Keep the ordered close barrier
          // until native closed arrives, even when the retiring session fails.
          if (media.phase === 'stopping') {
            realtimeStatus.textContent = params.message || 'Waiting for realtime to close.';
          } else failRealtime(params.message || 'Realtime session failed.', media.token);
        }
        else if (method === 'thread/realtime/closed') {
          if (hasSeq && media.closeFenceSeq !== null && seq <= media.closeFenceSeq) return;
          if (media.phase === 'starting' || media.phase === 'stopping' && media.stoppingNeedsStarted) return;
          if (media.phase !== 'stopping' && !media.active) {
            if (hasSeq) media.closeFenceSeq = Math.max(media.closeFenceSeq === null ? seq : media.closeFenceSeq, seq);
            return;
          }
          if (hasSeq) media.closeFenceSeq = Math.max(media.closeFenceSeq === null ? seq : media.closeFenceSeq, seq);
          if (media.phase === 'active' && hasSeq && media.startedSeq !== null && seq <= media.startedSeq) return;
          const pendingStatus = media.pendingStopStatus;
          media.nativeToken = null;
          media.phase = 'idle';
          media.stoppingToken = null;
          media.stoppingNeedsStarted = false;
          media.pendingStopStatus = null;
          media.token++; setRealtimeActive(false); releaseRealtimeMedia();
          realtimeStatus.textContent = pendingStatus || (params.reason ? 'Voice closed: ' + params.reason : 'Voice closed');
          resolveDisposeCloseBarrier();
        }
      });
    }

    function dispose() {
      if (state.disposePromise) return state.disposePromise;
      if (state.disposed) return Promise.resolve();
      const cleanupRequests = [];
      const session = state.terminal;
      if (session && session.running && !session.stopSent) {
        session.stopSent = true;
        const method = session.backend === 'process' ? 'process/kill' : 'command/exec/terminate';
        const params = session.backend === 'process' ? { processHandle: session.id } : { processId: session.id };
        cleanupRequests.push([method, params]);
      }
      const media = state.realtime;
      const pendingStart = media.startAttempt && !media.startAttempt.settled
        ? media.startAttempt.promise : null;
      const expectsRealtimeClose = !!(realtimeCapabilities.stop && (
        media.nativeToken !== null || pendingStart || media.phase === 'stopping'
      ));
      if (expectsRealtimeClose) {
        const wasStarting = media.phase === 'starting';
        media.phase = 'stopping';
        media.stoppingToken = media.token;
        media.stoppingNeedsStarted = wasStarting && media.startedSeq === null;
        media.pendingStopStatus = null;
      }
      const closeBarrier = expectsRealtimeClose ? beginDisposeCloseBarrier() : null;
      let stopRealtimeNow = false;
      if (media.nativeToken !== null && realtimeCapabilities.stop) {
        media.nativeToken = null;
        stopRealtimeNow = true;
      }
      state.disposed = true;
      media.token++;
      releaseRealtimeMedia();
      listeners.splice(0).forEach(remove => remove());
      timers.forEach(timer => window.clearTimeout(timer)); timers.clear();
      if (resizeObserver) resizeObserver.disconnect();
      root.remove();
      const cleanup = cleanupRequests.map(([method, params]) => rawOperation(method, params));
      let realtimeCleanup = null;
      if (stopRealtimeNow) {
        realtimeCleanup = rawOperation('thread/realtime/stop', { threadId: context.threadId }).catch(() => undefined);
      } else if (pendingStart && realtimeCapabilities.stop) {
        realtimeCleanup = pendingStart.then(
          () => rawOperation('thread/realtime/stop', { threadId: context.threadId }).catch(() => undefined),
          error => { if (!uncertainError(error)) resolveDisposeCloseBarrier(); },
        );
      }
      if (realtimeCleanup) cleanup.push(realtimeCleanup);
      if (closeBarrier) cleanup.push(closeBarrier);
      state.disposePromise = Promise.all(cleanup).then(() => undefined);
      return state.disposePromise;
    }

    function isActive() {
      if (state.disposed) return false;
      const terminalActive = !!(state.terminal && state.terminal.running);
      const realtimeActive = state.realtime.active || state.realtime.phase === 'starting' || state.realtime.phase === 'stopping';
      return terminalActive || realtimeActive;
    }

    return { dispose, handleEvents, isActive };
  }

  window.CCCCodexMedia = Object.freeze({ attach });
})();
