/* ------------------------------------------------------------------
   The thing in the middle of the home panel.

   Concentric arcs at different speeds, a ring of spokes that answers
   the microphone, a tick scale, and a sweep going round like a radar.
   Canvas rather than SVG because the spokes redraw every frame and
   sixty-four elements changing height sixty times a second is how you
   make a window that idles at eight percent of a CPU.

   It has four moods and they are only colour and energy: idle, hearing
   you, speaking, and working. You should be able to tell which from
   the far side of the room without reading a word.
   ------------------------------------------------------------------ */

const MOODS = {
  idle:      { hue: "#4de3ff", energy: 0.30, spin: 1.0 },
  listening: { hue: "#4dffa6", energy: 1.00, spin: 1.7 },
  speaking:  { hue: "#ffb648", energy: 0.85, spin: 1.35 },
  working:   { hue: "#4de3ff", energy: 0.62, spin: 2.4 },
};

const SPOKES = 64;

export class Reactor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.state = "idle";
    this.level = 0;
    this.t = 0;
    // Each spoke falls back on its own, so the ring settles rather
    // than snapping flat the instant you stop speaking.
    this.bars = new Float32Array(SPOKES);
    this.running = false;
    this._resize();
    window.addEventListener("resize", () => this._resize());
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const box = this.canvas.getBoundingClientRect();
    const size = Math.max(240, Math.min(box.width || 420, 560));
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.size = size;
  }

  set(state) { if (MOODS[state]) this.state = state; }
  hear(level) { this.level = Math.max(0, Math.min(1, level)); }

  start() {
    if (this.running) return;
    this.running = true;
    const tick = () => {
      if (!this.running) return;
      this._frame();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  stop() { this.running = false; }

  _frame() {
    const g = this.ctx;
    const s = this.size;
    const cx = s / 2;
    const cy = s / 2;
    const mood = MOODS[this.state] || MOODS.idle;

    this.t += 0.016;
    g.clearRect(0, 0, s, s);

    const base = s * 0.5;
    const breathe = 1 + Math.sin(this.t * 1.5) * 0.012;

    // ---------------------------------------------------- spoke ring
    const rIn = base * 0.50 * breathe;
    const drive = mood.energy * (0.30 + this.level * 0.95);
    for (let i = 0; i < SPOKES; i++) {
      const a = (i / SPOKES) * Math.PI * 2 - Math.PI / 2;
      // A standing wave plus the live level, so it is never dead but
      // still visibly answers your voice.
      const wave =
        Math.sin(this.t * 2.4 + i * 0.38) * 0.34 +
        Math.sin(this.t * 1.1 - i * 0.19) * 0.24 + 0.42;
      const want = wave * drive;
      this.bars[i] += (want - this.bars[i]) * 0.22;

      const len = base * 0.055 + this.bars[i] * base * 0.155;
      const x1 = cx + Math.cos(a) * rIn;
      const y1 = cy + Math.sin(a) * rIn;
      const x2 = cx + Math.cos(a) * (rIn + len);
      const y2 = cy + Math.sin(a) * (rIn + len);

      g.strokeStyle = mood.hue;
      g.globalAlpha = 0.22 + this.bars[i] * 0.62;
      g.lineWidth = s * 0.0062;
      g.lineCap = "round";
      g.beginPath();
      g.moveTo(x1, y1);
      g.lineTo(x2, y2);
      g.stroke();
    }
    g.globalAlpha = 1;

    // -------------------------------------------------- rotating arcs
    const arcs = [
      { r: 0.86, from: 0.00, len: 1.15, w: 0.0040, a: 0.50, sp:  0.22 },
      { r: 0.86, from: 2.40, len: 0.75, w: 0.0040, a: 0.34, sp:  0.22 },
      { r: 0.78, from: 1.20, len: 2.10, w: 0.0026, a: 0.26, sp: -0.15 },
      { r: 0.70, from: 4.10, len: 1.05, w: 0.0034, a: 0.42, sp:  0.34 },
      { r: 0.36, from: 0.60, len: 2.60, w: 0.0030, a: 0.38, sp: -0.46 },
      { r: 0.29, from: 3.60, len: 1.50, w: 0.0030, a: 0.30, sp:  0.60 },
    ];
    for (const arc of arcs) {
      const r = base * arc.r * breathe;
      const start = arc.from + this.t * arc.sp * mood.spin;
      g.strokeStyle = mood.hue;
      g.globalAlpha = arc.a;
      g.lineWidth = s * arc.w;
      g.lineCap = "butt";
      g.beginPath();
      g.arc(cx, cy, r, start, start + arc.len);
      g.stroke();
    }
    g.globalAlpha = 1;

    // ------------------------------------------------------ tick ring
    const rTick = base * 0.93;
    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2;
      const big = i % 6 === 0;
      const len = big ? base * 0.036 : base * 0.018;
      g.strokeStyle = mood.hue;
      g.globalAlpha = big ? 0.42 : 0.16;
      g.lineWidth = big ? s * 0.0034 : s * 0.0020;
      g.beginPath();
      g.moveTo(cx + Math.cos(a) * rTick, cy + Math.sin(a) * rTick);
      g.lineTo(cx + Math.cos(a) * (rTick - len), cy + Math.sin(a) * (rTick - len));
      g.stroke();
    }
    g.globalAlpha = 1;

    // ---------------------------------------------------- radar sweep
    const sweep = this.t * 0.55 * mood.spin;
    const grad = g.createConicGradient
      ? g.createConicGradient(sweep, cx, cy)
      : null;
    if (grad) {
      grad.addColorStop(0.00, this._rgba(mood.hue, 0.20));
      grad.addColorStop(0.06, this._rgba(mood.hue, 0.0));
      grad.addColorStop(1.00, this._rgba(mood.hue, 0.0));
      g.fillStyle = grad;
      g.beginPath();
      g.arc(cx, cy, base * 0.88, 0, Math.PI * 2);
      g.fill();
    }

    // ----------------------------------------------------------- core
    const pulse = 0.5 + Math.sin(this.t * 2.1) * 0.5;
    const rCore = base * (0.10 + this.level * 0.035 + pulse * 0.012);

    const glow = g.createRadialGradient(cx, cy, 0, cx, cy, rCore * 3.4);
    glow.addColorStop(0, this._rgba(mood.hue, 0.42 + this.level * 0.3));
    glow.addColorStop(1, this._rgba(mood.hue, 0));
    g.fillStyle = glow;
    g.beginPath();
    g.arc(cx, cy, rCore * 3.4, 0, Math.PI * 2);
    g.fill();

    g.strokeStyle = mood.hue;
    g.globalAlpha = 0.85;
    g.lineWidth = s * 0.0032;
    g.beginPath();
    g.arc(cx, cy, rCore, 0, Math.PI * 2);
    g.stroke();
    g.globalAlpha = 1;
  }

  _rgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
}
