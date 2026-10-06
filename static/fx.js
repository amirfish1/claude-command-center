/* CCC sound + motion kit.
 *
 * window.cccFx = {
 *   play(name)             // 'welcome' | 'step' | 'success' | 'coin' | 'error' | 'whoosh'
 *   confetti(opts)         // canvas burst; prefers-reduced-motion -> gentle static sparkle
 *   countUp(el, to, opts)  // animate el.textContent to `to`; returns a Promise
 *   reducedMotion()        // bool: OS "reduce motion" is on
 *   muted()                // bool: sounds switched off via the completion-sounds pref
 *   names                  // the list of sound names play() accepts
 *   unlock()               // pre-warm the AudioContext (call inside a click handler)
 * }
 *
 * Every cue is synthesized live in Web Audio: no audio files, nothing to
 * license, and it works offline. Sounds obey the existing
 * `ccc-sounds-enabled` localStorage pref (the same switch the footer pill and
 * Settings > Completion sounds drive). Motion obeys prefers-reduced-motion.
 */
(function () {
  'use strict';

  var SOUNDS_LS_KEY = 'ccc-sounds-enabled';
  var PALETTE = ['#d97757', '#f2b544', '#7bd88f', '#6cb6ff', '#f472b6', '#e8e6e3'];

  function muted() {
    try {
      return localStorage.getItem(SOUNDS_LS_KEY) === '0';
    } catch (_) {
      return false;
    }
  }

  var _motionMQ = null;
  function reducedMotion() {
    try {
      if (!_motionMQ && window.matchMedia) {
        _motionMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
      }
      return !!(_motionMQ && _motionMQ.matches);
    } catch (_) {
      return false;
    }
  }

  // ── Audio ────────────────────────────────────────────────────────────────
  // One lazy AudioContext shared by every cue, behind a compressor so layered
  // notes never clip. Browsers start it suspended until the first user
  // gesture: we resume on pointer/key input, and a play() that lands while
  // still suspended is held for ~1.5s and fired the moment the context wakes
  // (so a "welcome" queued by the click that enabled sound still sounds).

  var _ctx = null;
  var _master = null;
  var _pending = null;

  function _audioCtx() {
    if (_ctx) return _ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      _ctx = new AC();
    } catch (_) {
      return null;
    }
    var comp = _ctx.createDynamicsCompressor();
    comp.threshold.value = -18;
    comp.ratio.value = 6;
    _master = _ctx.createGain();
    _master.gain.value = 0.9;
    _master.connect(comp);
    comp.connect(_ctx.destination);
    _ctx.onstatechange = function () {
      if (_ctx.state === 'running' && _pending) {
        var p = Date.now() - _pending.at < 1500 ? _pending : null;
        _pending = null;
        if (p) _fire(p.fn, p.volume);
      }
    };
    return _ctx;
  }

  function unlock() {
    var ctx = _audioCtx();
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(function () {});
  }
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (evt) {
    document.addEventListener(evt, unlock, { once: false, passive: true });
  });

  // One enveloped oscillator. spec: {f, f2, t, dur, type, g, attack}
  function _tone(ctx, out, spec) {
    var t0 = spec.t;
    var osc = ctx.createOscillator();
    var g = ctx.createGain();
    osc.type = spec.type || 'sine';
    osc.frequency.setValueAtTime(spec.f, t0);
    if (spec.f2) {
      osc.frequency.exponentialRampToValueAtTime(spec.f2, t0 + spec.dur);
    }
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(spec.g || 0.12, t0 + (spec.attack || 0.008));
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + spec.dur);
    osc.connect(g);
    g.connect(out);
    osc.start(t0);
    osc.stop(t0 + spec.dur + 0.05);
  }

  var _noiseBuf = null;
  function _noise(ctx) {
    if (!_noiseBuf) {
      var len = Math.floor(ctx.sampleRate * 0.6);
      _noiseBuf = ctx.createBuffer(1, len, ctx.sampleRate);
      var data = _noiseBuf.getChannelData(0);
      for (var i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    }
    var src = ctx.createBufferSource();
    src.buffer = _noiseBuf;
    return src;
  }

  var SOUNDS = {
    // Warm rising arpeggio with a sparkle on top: "hello, welcome in".
    welcome: function (ctx, t, out) {
      var notes = [523.25, 659.26, 783.99, 1046.5]; // C5 E5 G5 C6
      _tone(ctx, out, { f: 130.81, t: t, dur: 0.9, type: 'sine', g: 0.05 });
      notes.forEach(function (f, i) {
        var nt = t + i * 0.095;
        _tone(ctx, out, { f: f, t: nt, dur: 0.55, type: 'triangle', g: 0.13 });
        _tone(ctx, out, { f: f * 2, t: nt, dur: 0.3, type: 'sine', g: 0.025 });
      });
      _tone(ctx, out, { f: 2093.0, t: t + 0.42, dur: 0.5, type: 'sine', g: 0.03 });
    },
    // Quick two-note tick: a step finished, on to the next.
    step: function (ctx, t, out) {
      _tone(ctx, out, { f: 880.0, t: t, dur: 0.07, type: 'sine', g: 0.1 });
      _tone(ctx, out, { f: 1174.66, t: t + 0.07, dur: 0.14, type: 'sine', g: 0.12 });
    },
    // Bright major chime with a bell tail: something real just worked.
    success: function (ctx, t, out) {
      var notes = [659.26, 830.61, 987.77]; // E5 G#5 B5
      notes.forEach(function (f, i) {
        _tone(ctx, out, { f: f, t: t + i * 0.06, dur: 0.4, type: 'triangle', g: 0.11 });
      });
      _tone(ctx, out, { f: 1318.51, t: t + 0.18, dur: 0.6, type: 'sine', g: 0.13 });
      _tone(ctx, out, { f: 2637.02, t: t + 0.18, dur: 0.35, type: 'sine', g: 0.025 });
    },
    // Two square blips: the little "money saved" ka-ching.
    coin: function (ctx, t, out) {
      _tone(ctx, out, { f: 987.77, t: t, dur: 0.08, type: 'square', g: 0.06 });
      _tone(ctx, out, { f: 1318.51, t: t + 0.08, dur: 0.26, type: 'square', g: 0.06 });
      _tone(ctx, out, { f: 1318.51, t: t + 0.08, dur: 0.26, type: 'sine', g: 0.05 });
    },
    // Soft descending pair: a gentle "hmm, that did not work". Never alarming.
    error: function (ctx, t, out) {
      _tone(ctx, out, { f: 329.63, t: t, dur: 0.18, type: 'triangle', g: 0.11 });
      _tone(ctx, out, { f: 261.63, t: t + 0.16, dur: 0.3, type: 'triangle', g: 0.11 });
    },
    // Filtered noise sweep: a transition swoosh for panels and celebrations.
    whoosh: function (ctx, t, out) {
      var src = _noise(ctx);
      var bp = ctx.createBiquadFilter();
      var g = ctx.createGain();
      bp.type = 'bandpass';
      bp.Q.value = 1.1;
      bp.frequency.setValueAtTime(420, t);
      bp.frequency.exponentialRampToValueAtTime(3200, t + 0.32);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.16, t + 0.1);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
      src.connect(bp);
      bp.connect(g);
      g.connect(out);
      src.start(t);
      src.stop(t + 0.55);
    },
  };

  function _fire(fn, volume) {
    var ctx = _audioCtx();
    if (!ctx || ctx.state !== 'running') return;
    var vg = ctx.createGain();
    vg.gain.value = volume;
    vg.connect(_master);
    fn(ctx, ctx.currentTime + 0.02, vg);
  }

  function play(name, opts) {
    var fn = SOUNDS[name];
    if (!fn || muted()) return false;
    var ctx = _audioCtx();
    if (!ctx) return false;
    var volume = opts && typeof opts.volume === 'number' ? opts.volume : 1;
    if (ctx.state === 'suspended') {
      _pending = { fn: fn, volume: volume, at: Date.now() };
      ctx.resume().catch(function () {});
      return true;
    }
    _fire(fn, volume);
    return true;
  }

  // ── Confetti ─────────────────────────────────────────────────────────────
  // One reused full-viewport canvas; each confetti() call pours another burst
  // into the same particle field, so overlapping celebrations just get fuller.

  var _fxCanvas = null;
  var _particles = [];
  var _raf = 0;

  function _confettiCanvas() {
    if (!_fxCanvas) {
      _fxCanvas = document.createElement('canvas');
      _fxCanvas.className = 'fx-confetti-canvas';
      _fxCanvas.setAttribute('aria-hidden', 'true');
    }
    if (!_fxCanvas.parentNode) document.body.appendChild(_fxCanvas);
    _fxCanvas.classList.remove('fx-fade');
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = window.innerWidth;
    var h = window.innerHeight;
    _fxCanvas.width = Math.round(w * dpr);
    _fxCanvas.height = Math.round(h * dpr);
    _fxCanvas.style.width = w + 'px';
    _fxCanvas.style.height = h + 'px';
    var ctx = _fxCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { canvas: _fxCanvas, ctx: ctx, w: w, h: h };
  }

  function _confettiFrame() {
    _raf = 0;
    if (!_fxCanvas || !_particles.length) return;
    var ctx = _fxCanvas.getContext('2d');
    var w = _fxCanvas.clientWidth || window.innerWidth;
    var h = _fxCanvas.clientHeight || window.innerHeight;
    ctx.clearRect(0, 0, w, h);
    var alive = [];
    for (var i = 0; i < _particles.length; i++) {
      var p = _particles[i];
      p.age++;
      p.vy += 0.32;
      p.vx *= 0.99;
      p.vy *= 0.995;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vrot;
      if (p.age > p.ttl * 0.6) p.alpha = Math.max(0, p.alpha - 0.03);
      if (p.alpha <= 0 || p.y > h + 40) continue;
      ctx.save();
      ctx.globalAlpha = p.alpha;
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      if (p.circle) {
        ctx.beginPath();
        ctx.arc(0, 0, p.size * 0.5, 0, Math.PI * 2);
        ctx.fill();
      } else {
        // Tumbling rectangle: squash one axis with the rotation phase.
        var squash = Math.abs(Math.sin(p.age * 0.12 + p.phase));
        ctx.fillRect(-p.size * 0.5, -p.size * 0.3 * squash, p.size, p.size * 0.6 * squash);
      }
      ctx.restore();
      alive.push(p);
    }
    _particles = alive;
    if (_particles.length) {
      _raf = requestAnimationFrame(_confettiFrame);
    } else {
      ctx.clearRect(0, 0, w, h);
    }
  }

  // Reduced-motion celebration: a ring of dots drawn once, then faded out by
  // opacity. No flying parts, still a visible "something good happened".
  function _gentleBurst(opts) {
    var c = _confettiCanvas();
    var ctx = c.ctx;
    var colors = opts.colors || PALETTE;
    var cx = c.w * (opts.origin && typeof opts.origin.x === 'number' ? opts.origin.x : 0.5);
    var cy = c.h * (opts.origin && typeof opts.origin.y === 'number' ? opts.origin.y : 0.4);
    ctx.clearRect(0, 0, c.w, c.h);
    for (var i = 0; i < 44; i++) {
      var a = (i / 44) * Math.PI * 2;
      var r = 30 + (i % 5) * 16;
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = colors[i % colors.length];
      ctx.beginPath();
      ctx.arc(cx + Math.cos(a) * r, cy + Math.sin(a) * r, 3, 0, Math.PI * 2);
      ctx.fill();
    }
    _fxCanvas.classList.add('fx-fade');
    setTimeout(function () {
      if (_fxCanvas) {
        ctx.clearRect(0, 0, c.w, c.h);
        _fxCanvas.classList.remove('fx-fade');
      }
    }, 700);
  }

  function confetti(opts) {
    opts = opts || {};
    if (reducedMotion()) {
      _gentleBurst(opts);
      return;
    }
    var c = _confettiCanvas();
    var count = Math.max(1, Math.min(600, Math.round(opts.count != null ? opts.count : 140)));
    var ox = opts.origin && typeof opts.origin.x === 'number' ? opts.origin.x : 0.5;
    var oy = opts.origin && typeof opts.origin.y === 'number' ? opts.origin.y : 0.4;
    // Direction cone: defaults burst upward; callers can aim sideways for
    // "cannon" effects via opts.angle (degrees, -90 = straight up).
    var baseAngle = ((opts.angle != null ? opts.angle : -90) * Math.PI) / 180;
    var spread = ((opts.spread != null ? opts.spread : 65) * Math.PI) / 180;
    var speed = (opts.speed != null ? opts.speed : 12) * (opts.scalar || 1);
    var colors = opts.colors || PALETTE;
    for (var i = 0; i < count; i++) {
      var a = baseAngle + (Math.random() - 0.5) * spread;
      var v = speed * (0.55 + Math.random() * 0.7);
      _particles.push({
        x: c.w * ox + (Math.random() - 0.5) * 14,
        y: c.h * oy + (Math.random() - 0.5) * 8,
        vx: Math.cos(a) * v,
        vy: Math.sin(a) * v,
        rot: Math.random() * Math.PI * 2,
        vrot: (Math.random() - 0.5) * 0.3,
        size: (5 + Math.random() * 7) * (opts.scalar || 1),
        color: colors[(Math.random() * colors.length) | 0],
        circle: Math.random() < 0.3,
        alpha: 1,
        age: 0,
        ttl: 90 + Math.random() * 80,
        phase: Math.random() * Math.PI * 2,
      });
    }
    if (!_raf) _raf = requestAnimationFrame(_confettiFrame);
  }

  // ── Count-up ─────────────────────────────────────────────────────────────

  function _decimalsOf(n) {
    var s = String(n);
    var dot = s.indexOf('.');
    return dot < 0 ? 0 : Math.min(2, s.length - dot - 1);
  }

  function _parseNum(text) {
    var m = String(text == null ? '' : text).replace(/,/g, '').match(/-?\d+(\.\d+)?/);
    return m ? Number(m[0]) : 0;
  }

  function countUp(el, to, opts) {
    if (!el) return Promise.resolve(null);
    opts = opts || {};
    var toN = Number(to);
    if (!isFinite(toN)) return Promise.resolve(el);
    var decimals = opts.decimals != null ? opts.decimals : _decimalsOf(toN);
    var prefix = opts.prefix || '';
    var suffix = opts.suffix || '';
    var fmt = new Intl.NumberFormat('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    var render = function (n) {
      el.textContent = prefix + fmt.format(n) + suffix;
    };
    if (el.__cccFxCountRaf) {
      cancelAnimationFrame(el.__cccFxCountRaf);
      el.__cccFxCountRaf = 0;
    }
    var from = opts.from != null ? Number(opts.from) : _parseNum(el.textContent);
    var dur = Math.max(0, opts.duration != null ? opts.duration : 900);
    if (reducedMotion() || dur === 0 || !isFinite(from)) {
      render(toN);
      return Promise.resolve(el);
    }
    var t0 = performance.now();
    return new Promise(function (resolve) {
      function frame(now) {
        var t = Math.min(1, (now - t0) / dur);
        var eased = 1 - Math.pow(1 - t, 3);
        render(from + (toN - from) * eased);
        if (t < 1) {
          el.__cccFxCountRaf = requestAnimationFrame(frame);
        } else {
          el.__cccFxCountRaf = 0;
          resolve(el);
        }
      }
      el.__cccFxCountRaf = requestAnimationFrame(frame);
    });
  }

  window.cccFx = {
    play: play,
    confetti: confetti,
    countUp: countUp,
    reducedMotion: reducedMotion,
    muted: muted,
    unlock: unlock,
    names: Object.keys(SOUNDS),
  };
})();
