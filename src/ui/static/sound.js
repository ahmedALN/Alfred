/* ------------------------------------------------------------------
   The noises the interface makes.

   These are synthesised rather than downloaded. A sample pack would
   have meant licence terms to honour, files to ship, and someone
   else's idea of what a confirmation sounds like; an oscillator and an
   envelope is a few hundred bytes, needs no network, and can be tuned
   to the rest of the display exactly.

   Everything ducks while Alfred is speaking. He is the reason you are
   here, and a UI chirping over the top of him is the fastest way to
   make a person turn sound off for good.
   ------------------------------------------------------------------ */

let ctx = null;
let master = null;
let enabled = true;
let ducked = false;

function audio() {
  if (ctx) return ctx;
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  ctx = new Ctx();
  master = ctx.createGain();
  master.gain.value = 0.30;
  master.connect(ctx.destination);
  return ctx;
}

/** A single shaped voice: the whole vocabulary is built from these. */
function tone({ from, to = from, dur = 0.16, type = "sine", peak = 0.5,
                delay = 0, sweep = 0 }) {
  const c = audio();
  if (!c || !enabled || ducked) return;
  const t0 = c.currentTime + delay;

  const osc = c.createOscillator();
  osc.type = type;
  osc.frequency.setValueAtTime(from, t0);
  if (to !== from) osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);

  const gain = c.createGain();
  // A tiny attack rather than none: an instant start clicks.
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

  let node = osc;
  if (sweep) {
    const filter = c.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(from * 2, t0);
    filter.frequency.exponentialRampToValueAtTime(sweep, t0 + dur);
    filter.Q.value = 3;
    osc.connect(filter);
    node = filter;
  }
  node.connect(gain);
  gain.connect(master);

  osc.start(t0);
  osc.stop(t0 + dur + 0.03);
}

/** Filtered noise, for the sweep that plays as the window arrives. */
function noise({ dur = 0.5, peak = 0.13, from = 300, to = 5200 }) {
  const c = audio();
  if (!c || !enabled || ducked) return;
  const t0 = c.currentTime;
  const frames = Math.floor(c.sampleRate * dur);
  const buffer = c.createBuffer(1, frames, c.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;

  const src = c.createBufferSource();
  src.buffer = buffer;

  const filter = c.createBiquadFilter();
  filter.type = "bandpass";
  filter.Q.value = 1.7;
  filter.frequency.setValueAtTime(from, t0);
  filter.frequency.exponentialRampToValueAtTime(to, t0 + dur);

  const gain = c.createGain();
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(peak, t0 + dur * 0.28);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

  src.connect(filter); filter.connect(gain); gain.connect(master);
  src.start(t0); src.stop(t0 + dur);
}

export const SOUND = {
  get enabled() { return enabled; },

  set(on) {
    enabled = !!on;
    try { localStorage.setItem("alfred.sound", enabled ? "1" : "0"); } catch (e) {}
    if (enabled) this.tick();
  },

  restore() {
    try {
      const kept = localStorage.getItem("alfred.sound");
      if (kept !== null) enabled = kept === "1";
    } catch (e) { /* private window, or storage blocked - sound stays on */ }
    return enabled;
  },

  /** Alfred is talking. Everything else gets out of the way. */
  duck(on) { ducked = !!on; },

  /** The window arriving: a rising sweep under two rising blips. */
  boot() {
    noise({ dur: 0.62, from: 220, to: 4800, peak: 0.11 });
    tone({ from: 320, to: 780, dur: 0.30, type: "triangle", peak: 0.26 });
    tone({ from: 640, to: 1560, dur: 0.34, type: "sine", peak: 0.17, delay: 0.10 });
  },

  /** Closing: the same shape, downwards. */
  close() {
    tone({ from: 720, to: 260, dur: 0.26, type: "triangle", peak: 0.24 });
  },

  tick()   { tone({ from: 1750, dur: 0.035, type: "square", peak: 0.05 }); },
  select() { tone({ from: 880, to: 1240, dur: 0.09, type: "sine", peak: 0.15 }); },

  /** Something finished, and it worked. A rising third. */
  done() {
    tone({ from: 660, dur: 0.13, type: "sine", peak: 0.2 });
    tone({ from: 990, dur: 0.20, type: "sine", peak: 0.17, delay: 0.10 });
  },

  /** Something wants you. Two-tone, unhurried. */
  notify() {
    tone({ from: 1050, dur: 0.11, type: "sine", peak: 0.19 });
    tone({ from: 780, dur: 0.19, type: "sine", peak: 0.16, delay: 0.13 });
  },

  /** Something is wrong. Low, and deliberately not pretty. */
  bad() {
    tone({ from: 300, to: 160, dur: 0.30, type: "sawtooth", peak: 0.14 });
  },

  /** The microphone opened. */
  listen() {
    tone({ from: 520, to: 1180, dur: 0.17, type: "sine", peak: 0.2, sweep: 2600 });
  },
};
