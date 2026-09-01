/* ------------------------------------------------------------------
   Alfred's interface.

   One page, one socket, no framework. The data is small enough to hold
   in memory and re-render a panel outright, so there is nothing here
   that a virtual DOM would save - and a build step for a window that
   opens on your own machine would be a tax paid every time you wanted
   to change a colour.
   ------------------------------------------------------------------ */

import { icon } from "/static/icons.js";
import { Reactor } from "/static/reactor.js";
import { SOUND } from "/static/sound.js";

const KEY = new URLSearchParams(location.search).get("k") || "";
const $ = (id) => document.getElementById(id);

const PANES = [
  { id: "home",   icon: "activity",       label: "overview" },
  { id: "talk",   icon: "message-square", label: "talk" },
  { id: "logs",   icon: "terminal",       label: "logs" },
  { id: "memory", icon: "brain",          label: "memory" },
  { id: "life",   icon: "heart-pulse",    label: "your life" },
  { id: "knows",  icon: "zap",            label: "skills" },
  { id: "tasks",  icon: "list-checks",    label: "tasks" },
  { id: "autos",  icon: "alarm-clock",    label: "automations" },
  { id: "screen", icon: "monitor",        label: "screen" },
];

const app = {
  data: {},
  pane: "home",
  socket: null,
  reactor: null,
  logs: [],
  chat: [],
  listening: false,
  alfred: { running: false, abilities: {} },
  filters: { memory: "", logs: "", skills: "" },
};

/* ------------------------------------------------------------ helpers */

async function api(path, options = {}) {
  const join = path.includes("?") ? "&" : "?";
  const res = await fetch(path + join + "k=" + encodeURIComponent(KEY), {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await res.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch (e) { body = { raw: text }; }
  if (!res.ok) throw new Error(body.error || `failed (${res.status})`);
  return body;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  $("toasts").appendChild(el);
  if (kind === "bad") SOUND.bad(); else SOUND.tick();
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 300);
  }, 3600);
}

/** pywebview has no window.prompt, so editing needs a real dialog. */
function ask(title, value = "") {
  return new Promise((resolve) => {
    $("modalTitle").textContent = title;
    $("modalInput").value = value;
    $("modal").hidden = false;
    $("modalInput").focus();
    const done = (result) => {
      $("modal").hidden = true;
      $("modalOk").onclick = null;
      $("modalCancel").onclick = null;
      resolve(result);
    };
    $("modalOk").onclick = () => done($("modalInput").value);
    $("modalCancel").onclick = () => done(null);
  });
}

function ago(iso) {
  if (!iso) return "";
  const then = new Date(iso.replace(" ", "T"));
  if (isNaN(then)) return String(iso).slice(0, 16);
  const s = (Date.now() - then.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`;
  return then.toLocaleDateString();
}

function when(iso) {
  if (!iso) return "";
  const at = new Date(iso.replace(" ", "T"));
  if (isNaN(at)) return String(iso).slice(0, 16);
  const days = Math.round((at - new Date()) / 86400000);
  const time = at.toTimeString().slice(0, 5);
  if (days < -1) return `${-days} days ago`;
  if (days === -1) return "yesterday";
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  if (days < 7) return at.toLocaleDateString(undefined, { weekday: "long" });
  return at.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

function empty(text) { return `<div class="empty">${esc(text)}</div>`; }

/* --------------------------------------------------------------- boot */

const BOOT_LINES = [
  "waking", "reading memory", "checking the diary",
  "asking what it can do", "ready",
];

async function boot() {
  SOUND.restore();
  let step = 0;
  const timer = setInterval(() => {
    step++;
    if (step < BOOT_LINES.length) $("bootLine").textContent = BOOT_LINES[step];
  }, 260);

  buildNav();
  app.reactor = new Reactor($("reactor"));
  app.reactor.start();
  wireDock();

  try { await refresh(); } catch (e) { /* shown by the connection dot */ }

  setTimeout(() => {
    clearInterval(timer);
    $("boot").classList.add("gone");
    $("app").hidden = false;
    SOUND.boot();
    connect();
  }, 1250);
}

function buildNav() {
  $("nav").innerHTML = PANES.map((p) => `
    <button data-go="${p.id}" class="${p.id === app.pane ? "on" : ""}">
      ${icon(p.icon)}<span class="tip">${esc(p.label)}</span>
      <span class="badge" data-badge="${p.id}" hidden></span>
    </button>`).join("");

  $("nav").addEventListener("click", (e) => {
    const button = e.target.closest("[data-go]");
    if (button) show(button.dataset.go);
  });

  $("soundBtn").innerHTML = icon(SOUND.enabled ? "volume-2" : "volume-x");
  $("soundBtn").onclick = () => {
    SOUND.set(!SOUND.enabled);
    $("soundBtn").innerHTML = icon(SOUND.enabled ? "volume-2" : "volume-x");
  };
}

function show(id) {
  if (!PANES.some((p) => p.id === id)) return;
  app.pane = id;
  SOUND.select();
  document.querySelectorAll(".pane").forEach((el) => {
    el.hidden = el.dataset.pane !== id;
  });
  document.querySelectorAll("[data-go]").forEach((b) => {
    b.classList.toggle("on", b.dataset.go === id);
  });
  const meta = PANES.find((p) => p.id === id);
  $("paneName").textContent = meta ? meta.label : id;
  const badge = document.querySelector(`[data-badge="${id}"]`);
  if (badge) badge.hidden = true;
  if (id === "screen") lookNow();
  if (id === "logs") drawLogs();
}

/* ------------------------------------------------------------ refresh */

async function refresh() {
  const data = await api("/api/all");
  app.data = data;
  app.alfred = data.alfred || { running: false, abilities: {} };
  drawEverything();
}

function drawEverything() {
  drawStats();
  drawHomeLife();
  drawLife();
  drawMemory();
  drawSkills();
  drawLimits();
  drawTasks();
  drawAutos();
  drawTicker();
  drawState();
  drawAbilities();
}

/* -------------------------------------------------------------- panels */

function drawStats() {
  const o = app.data.overview || {};
  const cells = [
    ["facts", o.facts], ["skills", o.skills], ["cannot", o.limitations],
    ["tasks", o.tasks], ["apps", o.apps], ["automations", o.automations],
    ["due", o.due], ["overdue", o.overdue], ["people", o.people],
  ];
  $("stats").innerHTML = cells.map(([k, v]) => `
    <div class="stat">
      <div class="n ${k === "overdue" && v > 0 ? "hot" : ""}">${v ?? 0}</div>
      <div class="k">${esc(k)}</div>
    </div>`).join("");
}

function drawHomeLife() {
  const life = app.data.life || {};
  const items = [...(life.overdue || []), ...(life.due || [])].slice(0, 6);
  $("homeLife").innerHTML = items.length
    ? items.map((m) => `
        <div class="m ${(life.overdue || []).includes(m) ? "late" : ""}">
          <span>${esc(m.name)}</span>
          <span class="when">${esc(when(m.due))}</span>
        </div>`).join("")
    : empty("nothing on your plate");
}

function drawLife() {
  const life = app.data.life || {};
  const row = (m) => `
    <div class="row">
      <div class="main-text">
        <div class="t">${esc(m.name)}</div>
        <div class="s">${esc(m.source || "")}${m.detail ? " &middot; " + esc(m.detail) : ""}${m.due ? " &middot; " + esc(when(m.due)) : ""}</div>
      </div>
      <div class="acts">
        <button class="iconbtn" title="Done" data-act="settle" data-id="${esc(m.id)}" data-state="done">${icon("check")}</button>
        <button class="iconbtn danger" title="Not mine" data-act="settle" data-id="${esc(m.id)}" data-state="dropped">${icon("x")}</button>
      </div>
    </div>`;

  const due = [...(life.overdue || []), ...(life.due || [])];
  $("lifeDue").innerHTML = due.length ? due.map(row).join("") : empty("nothing due");
  $("lifePeople").innerHTML = (life.people || []).length
    ? life.people.map(row).join("") : empty("nobody waiting");
  $("lifeDoing").innerHTML = (life.doing || []).length
    ? life.doing.map(row).join("") : empty("nothing noted");
}

function drawMemory() {
  const term = app.filters.memory.toLowerCase();
  const facts = (app.data.memory || []).filter(
    (f) => !term || (f.content || "").toLowerCase().includes(term)
  );
  $("memory").innerHTML = facts.length ? facts.map((f) => `
    <div class="row">
      <div class="main-text">
        <div class="t">${esc(f.content)}</div>
        <div class="s">${esc(f.category || "general")} &middot; seen ${f.seen ?? 1}&times; &middot; ${esc(f.source || "")} &middot; ${esc(ago(f.updated_at))}</div>
      </div>
      <div class="acts">
        <button class="iconbtn" title="Correct" data-act="edit-fact" data-id="${f.id}">${icon("pencil")}</button>
        <button class="iconbtn danger" title="Forget" data-act="forget-fact" data-id="${f.id}">${icon("trash-2")}</button>
      </div>
    </div>`).join("") : empty(term ? "nothing matches" : "no facts yet");
}

function drawSkills() {
  const term = app.filters.skills.toLowerCase();
  const skills = (app.data.skills || []).filter(
    (s) => !term || (s.name + " " + (s.keywords || "")).toLowerCase().includes(term)
  );
  $("skills").innerHTML = skills.length ? skills.map((s) => `
    <div class="row ${s.disabled ? "off" : ""}">
      <div class="main-text">
        <div class="t">${esc(s.template || s.name)}</div>
        <div class="s">
          ${esc(s.app || "any")} &middot; ${s.step_count} step${s.step_count === 1 ? "" : "s"}
          &middot; ${s.success ?? 0} ok / ${s.fail ?? 0} failed
          ${s.unconfirmed ? '&middot; <span class="tag warn">unproven</span>' : ""}
        </div>
      </div>
      <div class="acts">
        <button class="iconbtn" title="${s.disabled ? "Enable" : "Disable"}" data-act="toggle-skill" data-id="${esc(s.id)}" data-on="${s.disabled ? 1 : 0}">${icon("power")}</button>
        <button class="iconbtn danger" title="Delete" data-act="delete-skill" data-id="${esc(s.id)}">${icon("trash-2")}</button>
      </div>
    </div>`).join("") : empty("no skills yet");
}

function drawLimits() {
  const limits = app.data.limitations || [];
  $("limits").innerHTML = limits.length ? limits.map((l) => `
    <div class="row">
      <div class="main-text">
        <div class="t">${esc(l.detail || l.signature)}</div>
        <div class="s">${esc(l.tool || "")}${l.app ? " &middot; " + esc(l.app) : ""} &middot; hit ${l.hits ?? 1}&times;${l.workaround ? " &middot; has a way round" : ""}</div>
      </div>
      <div class="acts">
        <button class="iconbtn danger" title="Let it try again" data-act="clear-limit" data-sig="${esc(l.signature)}">${icon("refresh-cw")}</button>
      </div>
    </div>`).join("") : empty("nothing it thinks it cannot do");
}

function drawTasks() {
  const tasks = app.data.tasks || [];
  const badge = { done: "good", failed: "bad", running: "warn" };
  $("tasks").innerHTML = tasks.length ? tasks.map((t) => `
    <div class="row">
      <div class="main-text">
        <div class="t">${esc(t.goal)}</div>
        <div class="s">
          <span class="tag ${badge[t.status] || ""}">${esc(t.status)}</span>
          &nbsp;${esc(t.source || "")} &middot; ${esc(ago(t.created_at))}
          ${t.summary ? "<br>" + esc(t.summary) : ""}
        </div>
      </div>
      <div class="acts">
        <button class="iconbtn danger" title="Remove from the record" data-act="forget-task" data-id="${esc(t.id)}">${icon("trash-2")}</button>
      </div>
    </div>`).join("") : empty("no tasks yet");
}

function drawAutos() {
  const autos = app.data.automations || [];
  $("autos").innerHTML = autos.length ? autos.map((a) => `
    <div class="row ${a.enabled ? "" : "off"}">
      <div class="main-text">
        <div class="t">${esc(a.goal || a.said)}</div>
        <div class="s">
          ${esc(a.kind || "once")}${a.repeat ? " &middot; " + esc(a.repeat) : ""}
          &middot; ${esc(when(a.due))} &middot; run ${a.runs ?? 0}&times;
        </div>
      </div>
      <div class="acts">
        <button class="iconbtn" title="${a.enabled ? "Turn off" : "Turn on"}" data-act="toggle-auto" data-id="${esc(a.id)}" data-on="${a.enabled ? 1 : 0}">${icon("power")}</button>
        <button class="iconbtn danger" title="Delete" data-act="delete-auto" data-id="${esc(a.id)}">${icon("trash-2")}</button>
      </div>
    </div>`).join("") : empty("nothing scheduled");
}

function drawTicker() {
  const o = app.data.overview || {};
  const life = app.data.life || {};
  const next = (life.overdue || [])[0] || (life.due || [])[0];
  $("ticker").innerHTML = next
    ? `next up <b>${esc(next.name)}</b> ${esc(when(next.due))}`
    : `<b>${o.facts ?? 0}</b> facts &middot; <b>${o.skills ?? 0}</b> skills &middot; <b>${o.tasks_today ?? 0}</b> tasks today`;
}

function drawState() {
  const task = app.alfred.task;
  if (task) {
    app.reactor.set("working");
    $("rrState").textContent = "working";
    $("rrSub").textContent = task.goal || "";
  } else if (app.listening) {
    app.reactor.set("listening");
    $("rrState").textContent = "listening";
    $("rrSub").textContent = "go ahead";
  } else if (app.alfred.speaking) {
    app.reactor.set("speaking");
    $("rrState").textContent = "speaking";
    $("rrSub").textContent = "";
  } else {
    app.reactor.set("idle");
    $("rrState").textContent = app.alfred.running ? "idle" : "offline";
    $("rrSub").textContent = app.alfred.running
      ? "nothing running" : "alfred is not running";
  }
}

function drawAbilities() {
  const can = app.alfred.abilities || {};
  $("micBtn").disabled = !can.mic;
  $("sendBtn").disabled = !can.talk;
  $("say").disabled = !can.talk;
  $("say").placeholder = can.talk
    ? "say something to Alfred"
    : "Alfred is not running - start it to talk";
  $("micBtn").innerHTML = icon(app.listening ? "mic" : "mic-off");
  $("sendBtn").innerHTML = icon("send");
}

/* ---------------------------------------------------------------- logs */

function drawLogs() {
  const term = app.filters.logs.toLowerCase();
  const lines = app.logs.filter((l) => !term || l.line.toLowerCase().includes(term));
  const box = $("logs");
  box.innerHTML = lines.slice(-600).map((l) => `
    <div class="l ${l.stream === "err" ? "err" : ""} ${/\[(Alfred|Brain|Task)\]/.test(l.line) ? "hot" : ""}">
      <span class="ts">${new Date(l.at * 1000).toTimeString().slice(0, 8)}</span>
      <span class="tx">${esc(l.line)}</span>
    </div>`).join("") || empty("nothing logged yet");
  if ($("logFollow").checked) box.scrollTop = box.scrollHeight;
}

function drawHomeLog() {
  const recent = app.logs.slice(-8).reverse();
  $("homeLog").innerHTML = recent.length
    ? recent.map((l) => `<div>${esc(l.line.slice(0, 90))}</div>`).join("")
    : empty("nothing logged yet");
}

function pushLog(event) {
  app.logs.push(event);
  if (app.logs.length > 1200) app.logs.splice(0, 400);
  drawHomeLog();
  if (app.pane === "logs") drawLogs();
}

/* ---------------------------------------------------------------- chat */

function pushChat(who, text) {
  app.chat.push({ who, text });
  $("chat").innerHTML = app.chat.map((m) => `
    <div class="bub ${m.who === "you" ? "you" : "him"}">
      <div class="who">${m.who === "you" ? "you" : "alfred"}</div>
      ${esc(m.text)}
    </div>`).join("");
  $("chat").scrollTop = $("chat").scrollHeight;
  if (app.pane !== "talk") {
    const badge = document.querySelector('[data-badge="talk"]');
    if (badge) { badge.hidden = false; badge.textContent = "1"; }
  }
}

/* ------------------------------------------------------------- socket */

function connect() {
  const url = `ws://${location.host}/ws?k=${encodeURIComponent(KEY)}`;
  let socket;
  try { socket = new WebSocket(url); } catch (e) { return retry(); }
  app.socket = socket;

  socket.onopen = () => {
    $("connText").textContent = "live";
    $("connDot").className = "conn-dot live";
    $("pulse").className = "pulse live";
  };

  socket.onclose = () => {
    $("connText").textContent = "offline";
    $("connDot").className = "conn-dot dead";
    $("pulse").className = "pulse dead";
    retry();
  };

  socket.onerror = () => { try { socket.close(); } catch (e) {} };

  socket.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch (e) { return; }
    handle(event);
  };
}

let retryIn = 1000;
function retry() {
  setTimeout(() => {
    retryIn = Math.min(retryIn * 1.6, 15000);
    connect();
  }, retryIn);
}

function handle(event) {
  switch (event.kind) {
    case "hello":
      retryIn = 1000;
      app.alfred.running = event.running;
      app.alfred.abilities = event.abilities || {};
      (event.history || []).forEach((e) => {
        if (e.kind === "log") app.logs.push(e);
      });
      drawLogs();
      drawHomeLog();
      drawAbilities();
      drawState();
      break;

    case "log": pushLog(event); break;

    case "level": app.reactor.hear(event.level); break;

    // The wake word and the hotkey open the mic too. The button used to
    // track only its own clicks and so drifted out of step with Alfred.
    case "listening":
      app.listening = event.listening;
      $("micBtn").classList.toggle("live", app.listening);
      $("micBtn").innerHTML = icon(app.listening ? "mic" : "mic-off");
      if (app.listening) SOUND.listen();
      drawState();
      break;

    case "speaking":
      app.alfred.speaking = event.speaking;
      SOUND.duck(event.speaking);
      drawState();
      break;

    case "you_said": pushChat("you", event.text); break;
    case "alfred_said": pushChat("alfred", event.text); break;

    case "task_started":
      app.alfred.task = { id: event.id, goal: event.goal };
      drawState();
      SOUND.notify();
      break;

    case "task_step":
      $("rrSub").textContent = event.what || "";
      break;

    case "task_ended":
      app.alfred.task = null;
      drawState();
      if (event.status === "done") SOUND.done(); else SOUND.bad();
      refresh().catch(() => {});
      break;

    case "changed": refresh().catch(() => {}); break;

    // Asked to open when it is already open: the window process is
    // still alive with this page loaded, merely hidden. Raise it.
    case "show_window":
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.show();
      }
      SOUND.boot();
      refresh().catch(() => {});
      break;
  }
}

/* ------------------------------------------------------------ actions */

const ACTS = {
  "forget-fact": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "forget_fact", payload: { id: Number(el.dataset.id) } }) });
    toast("forgotten", "good");
  },
  "edit-fact": async (el) => {
    const row = el.closest(".row");
    const now = row.querySelector(".t").textContent.trim();
    const next = await ask("correct this", now);
    if (next === null || next.trim() === now) return;
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "correct_fact",
      payload: { id: Number(el.dataset.id), content: next } }) });
    toast("corrected", "good");
  },
  "clear-limit": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "clear_limitation",
      payload: { signature: el.dataset.sig } }) });
    toast("it will try again", "good");
  },
  "toggle-skill": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: el.dataset.on === "1" ? "enable_skill" : "disable_skill",
      payload: { id: el.dataset.id } }) });
  },
  "delete-skill": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "delete_skill", payload: { id: el.dataset.id } }) });
    toast("skill deleted", "good");
  },
  "settle": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "settle_matter",
      payload: { id: el.dataset.id, state: el.dataset.state } }) });
  },
  "toggle-auto": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: el.dataset.on === "1" ? "disable_automation" : "enable_automation",
      payload: { id: el.dataset.id } }) });
  },
  "delete-auto": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "delete_automation", payload: { id: el.dataset.id } }) });
    toast("automation deleted", "good");
  },
  "forget-task": async (el) => {
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "forget_task", payload: { id: el.dataset.id } }) });
  },
};

document.addEventListener("click", async (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const run = ACTS[el.dataset.act];
  if (!run) return;
  try {
    await run(el);
    await refresh();
  } catch (err) {
    toast(err.message, "bad");
  }
});

/* --------------------------------------------------------------- dock */

function wireDock() {
  $("sendBtn").onclick = send;
  $("say").addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });

  $("micBtn").onclick = async () => {
    const want = !app.listening;
    try {
      await api("/api/mic", { method: "POST",
        body: JSON.stringify({ listening: want }) });
      app.listening = want;
      $("micBtn").classList.toggle("live", want);
      $("micBtn").innerHTML = icon(want ? "mic" : "mic-off");
      if (want) SOUND.listen();
      drawState();
    } catch (err) { toast(err.message, "bad"); }
  };

  $("memFilter").oninput = (e) => { app.filters.memory = e.target.value; drawMemory(); };
  $("skillFilter").oninput = (e) => { app.filters.skills = e.target.value; drawSkills(); };
  $("logFilter").oninput = (e) => { app.filters.logs = e.target.value; drawLogs(); };

  $("addFactBtn").onclick = async () => {
    const text = await ask("tell Alfred something to remember");
    if (!text || !text.trim()) return;
    try {
      await api("/api/act", { method: "POST", body: JSON.stringify({
        action: "add_fact", payload: { content: text, category: "you" } }) });
      toast("remembered", "good");
      await refresh();
    } catch (err) { toast(err.message, "bad"); }
  };

  $("shotBtn").onclick = lookNow;

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("modal").hidden) $("modalCancel").click();
    // Number keys jump between panels, the way a HUD ought to.
    if (e.altKey && /^[1-9]$/.test(e.key)) {
      const pane = PANES[Number(e.key) - 1];
      if (pane) show(pane.id);
    }
  });
}

async function send() {
  const box = $("say");
  const text = box.value.trim();
  if (!text) return;
  box.value = "";
  try {
    await api("/api/say", { method: "POST", body: JSON.stringify({ text }) });
  } catch (err) {
    toast(err.message, "bad");
    box.value = text;
  }
}

async function lookNow() {
  const img = $("shot");
  img.src = `/api/screen?k=${encodeURIComponent(KEY)}&t=${Date.now()}`;
  try {
    const found = await api("/api/windows");
    const windows = found.windows || [];
    $("windows").innerHTML = windows.length ? windows.map((w) => `
      <div class="row"><div class="main-text">
        <div class="t">${esc(w.title || w)}</div>
        <div class="s">${esc(w.app || "")}</div>
      </div></div>`).join("") : empty("no windows seen");
  } catch (err) {
    $("windows").innerHTML = empty(err.message);
  }
}

/* ---------------------------------------------------------------- go */

boot();
