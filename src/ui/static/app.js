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
  memoryGroup: "you",
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


/* ------------------------------------------------------------- rows */

/** One row. `detail` makes it expandable; nothing else has to know how. */
function row({ id, title, meta, detail, acts = "", cls = "" }) {
  const openable = detail ? "can-open" : "";
  return `
    <div class="row ${cls} ${openable}" ${id ? `data-row="${esc(id)}"` : ""}>
      <div class="main-text">
        <div class="t">${title}</div>
        ${meta ? `<div class="s">${meta}</div>` : ""}
        ${detail ? `<div class="more" hidden>${detail}</div>` : ""}
      </div>
      <div class="acts">${acts}</div>
    </div>`;
}

function bits(...parts) {
  return parts.filter(Boolean).map((p) => `<span>${p}</span>`).join("");
}

/** Clicking a row opens it. Buttons inside it do not. */
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-act]") || e.target.closest("button")) return;
  const el = e.target.closest(".row.can-open");
  if (!el) return;
  const more = el.querySelector(".more");
  if (!more) return;
  const opening = more.hidden;
  more.hidden = !opening;
  el.classList.toggle("open", opening);
  SOUND.tick();
});

/** Tool steps, said the way a person would say them. */
function inWords(steps) {
  return (steps || []).map((s) => {
    const a = s.args || {};
    switch (s.tool) {
      case "power":      return `${a.action} the PC`;
      case "weather":    return `look up the weather for ${a.place ?? "somewhere"}`;
      case "open_app":   return `open ${a.name ?? a.app ?? "an app"}`;
      case "web":        return a.action === "fetch"
                           ? `read ${a.url ?? "a page"}`
                           : `search the web for "${a.query ?? ""}"`;
      case "powershell": return `run: ${(a.command ?? "").slice(0, 70)}`;
      case "ui_control": return `${a.action ?? "use"} ${a.name ?? a.window ?? "a control"}`;
      case "system_info":return `check ${a.query ?? "the system"}`;
      case "mail":       return `${a.action ?? "read"} your mail`;
      case "calendar":   return `${a.action ?? "check"} your calendar`;
      case "skill":      return `${a.action ?? "use"} a routine`;
      default:           return `${s.tool}${a.action ? " " + a.action : ""}`;
    }
  });
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

  const one = (m, late) => row({
    id: `matter-${m.id}`,
    title: esc(m.name),
    meta: bits(
      m.due ? `<span class="${late ? "late" : ""}">${esc(when(m.due))}</span>` : null,
      m.detail ? esc(m.detail) : null,
      m.source ? `from your ${esc(m.source)}` : null,
    ),
    acts: `
      <button class="iconbtn" title="Done" data-act="settle" data-id="${esc(m.id)}" data-state="done">${icon("check")}</button>
      <button class="iconbtn danger" title="Not mine" data-act="settle" data-id="${esc(m.id)}" data-state="dropped">${icon("x")}</button>`,
  });

  const late = life.overdue || [];
  const due = [...late, ...(life.due || [])];

  $("lifeDue").innerHTML = due.length
    ? due.map((m) => one(m, late.includes(m))).join("")
    : empty("nothing due");
  $("lifePeople").innerHTML = (life.people || []).length
    ? life.people.map((m) => one(m)).join("")
    : empty("nobody waiting on you");
  $("lifeDoing").innerHTML = (life.doing || []).length
    ? life.doing.map((m) => one(m)).join("")
    : empty("nothing noted");
}

// Memory is three different things wearing one label. Of 134 "facts",
// 64 were the built-in Windows playbook, 69 were notes about tool
// failures, and exactly one was about the user - under a heading that
// said "what Alfred believes about you". Grouped, the panel finally
// answers the question it claims to.
const MEMORY_GROUPS = [
  { id: "you",     label: "about you",
    hint: "what Alfred has picked up about you and how you work",
    match: (f) => !["correction", "system", "tool"].includes(f.category) },
  { id: "lessons", label: "lessons",
    hint: "what it learned from things that went wrong. Delete one and it will try that way again.",
    match: (f) => f.category === "correction" },
  { id: "ref",     label: "reference",
    hint: "the built-in Windows playbook it shipped with - not learned, and safe to leave alone",
    match: (f) => ["system", "tool"].includes(f.category) },
];

function drawMemory() {
  const all = app.data.memory || [];
  const term = app.filters.memory.toLowerCase();
  const group = MEMORY_GROUPS.find((g) => g.id === app.memoryGroup)
    || MEMORY_GROUPS[0];

  const tabs = MEMORY_GROUPS.map((g) => {
    const n = all.filter(g.match).length;
    return `<button class="tab ${g.id === group.id ? "on" : ""}"
      data-memgroup="${g.id}">${esc(g.label)} <b>${n}</b></button>`;
  }).join("");

  const facts = all
    .filter(group.match)
    .filter((f) => !term || (f.content || "").toLowerCase().includes(term));

  $("memTabs").innerHTML = tabs;
  $("memHint").textContent = group.hint;

  $("memory").innerHTML = facts.length ? facts.map((f) => row({
    id: `fact-${f.id}`,
    title: esc(f.content),
    meta: bits(
      f.seen > 1 ? `seen ${f.seen} times` : null,
      whereFrom(f.source),
      ago(f.updated_at),
    ),
    // The full text, for the ones that run long - which is most of the
    // lessons, because they quote the tool call that failed.
    detail: (f.content || "").length > 150 ? esc(f.content) : "",
    acts: `
      <button class="iconbtn" title="Correct this" data-act="edit-fact" data-id="${f.id}">${icon("pencil")}</button>
      <button class="iconbtn danger" title="Forget this" data-act="forget-fact" data-id="${f.id}">${icon("trash-2")}</button>`,
  })).join("") : empty(
    term ? "nothing matches" :
    group.id === "you" ? "Alfred has not learned anything about you yet"
                       : "nothing here"
  );
}

function whereFrom(source) {
  return {
    playbook: "built in",
    task_reflection: "learned from a job",
    learned_workaround: "learned the hard way",
    conversation: "you told it",
    you: "you told it",
    "you (corrected)": "you corrected it",
  }[source] || source || "";
}

function drawSkills() {
  const term = app.filters.skills.toLowerCase();
  const skills = (app.data.skills || []).filter(
    (s) => !term || (s.name + " " + (s.template || "") + " " + (s.keywords || ""))
      .toLowerCase().includes(term)
  );

  $("skills").innerHTML = skills.length ? skills.map((s) => {
    const steps = s.steps || [];
    const words = inWords(steps);
    const runs = (s.success ?? 0) + (s.fail ?? 0);

    return row({
      id: `skill-${s.id}`,
      title: esc(s.template || s.name),
      // What it DOES, not how many steps it has. "1 step, 0 ok" told
      // you nothing about whether the routine was any good or what it
      // would do if you let it run.
      meta: bits(
        words.length ? esc(words.join(" → ")) : `${steps.length} steps`,
        runs === 0 ? "never run"
          : `${s.success ?? 0} of ${runs} worked`,
        s.unconfirmed ? '<span class="tag warn">unproven</span>' : null,
        s.disabled ? '<span class="tag bad">off</span>' : null,
      ),
      detail: steps.length ? steps.map((st, i) =>
        `${i + 1}. ${esc(st.tool)}  ${esc(JSON.stringify(st.args || {}))}`
      ).join("\n") : "",
      cls: s.disabled ? "off" : "",
      acts: `
        <button class="iconbtn" title="Rename what this is for" data-act="edit-skill" data-id="${esc(s.id)}">${icon("pencil")}</button>
        <button class="iconbtn" title="${s.disabled ? "Turn on" : "Turn off"}" data-act="toggle-skill" data-id="${esc(s.id)}" data-on="${s.disabled ? 1 : 0}">${icon("power")}</button>
        <button class="iconbtn danger" title="Forget this routine" data-act="delete-skill" data-id="${esc(s.id)}">${icon("trash-2")}</button>`,
    });
  }).join("") : empty(term ? "nothing matches" : "no routines yet");
}

function drawLimits() {
  const limits = app.data.limitations || [];

  $("limits").innerHTML = limits.length ? limits.map((l) => row({
    id: `limit-${l.signature}`,
    title: esc(plainly(l)),
    meta: bits(
      l.hits > 1 ? `hit ${l.hits} times` : "hit once",
      l.workaround ? "found a way round" : null,
      ago(l.last_seen),
    ),
    detail: esc(l.detail || l.signature) +
      (l.workaround ? `

what worked instead:
${esc(l.workaround)}` : ""),
    acts: `<button class="iconbtn danger" title="Let it try again"
             data-act="clear-limit" data-sig="${esc(l.signature)}">${icon("refresh-cw")}</button>`,
  })).join("") : empty("nothing it thinks it cannot do");
}

/** A tool failure, said as the thing it stops Alfred doing. */
function plainly(l) {
  const detail = (l.detail || l.signature || "").trim();
  const app_ = l.app ? ` in ${l.app}` : "";

  if (/no control matches/i.test(detail)) {
    const name = (detail.match(/name='([^']+)'|name="([^"]+)"/) || [])
      .slice(1).find(Boolean);
    return `Can't find ${name ? `"${name}"` : "a control"}${app_} without looking at the window first`;
  }
  if (/timed out/i.test(detail)) return `Times out${app_}`;
  if (/must be a non-empty string/i.test(detail)) return "Was asked to open an app with no name";
  if (/must be one of/i.test(detail)) return "Was given an action that does not exist";
  if (/^failed$/i.test(detail)) return `Something${app_} failed without saying why`;
  return detail.length > 110 ? detail.slice(0, 110) + "…" : detail;
}

function drawTasks() {
  const tasks = app.data.tasks || [];
  const badge = { done: "good", failed: "bad", error: "bad",
                  running: "warn", exhausted: "warn", partial: "warn" };
  const plain = { done: "done", failed: "failed", error: "failed",
                  running: "running now", exhausted: "ran out of time",
                  partial: "partly done", queued: "waiting" };

  $("tasks").innerHTML = tasks.length ? tasks.map((t) => row({
    id: `task-${t.id}`,
    title: esc(t.goal),
    meta: bits(
      `<span class="tag ${badge[t.status] || ""}">${esc(plain[t.status] || t.status)}</span>`,
      t.source === "whatsapp" ? "asked on WhatsApp"
        : t.source === "voice" ? "asked out loud"
        : t.source === "interface" ? "asked here"
        : t.source === "brain" ? "its own idea" : esc(t.source || ""),
      ago(t.created_at),
    ),
    // The summary is the interesting part and it was squeezed into the
    // metadata line at 11px, which is where it went to be unread.
    detail: t.summary ? esc(t.summary) : "",
    acts: `<button class="iconbtn danger" title="Remove from the record"
             data-act="forget-task" data-id="${esc(t.id)}">${icon("trash-2")}</button>`,
  })).join("") : empty("nothing has been asked of it yet");
}


/** "every weekdays" is not a thing anybody says. */
function howOften(a) {
  const r = (a.repeat || "").toLowerCase();
  if (!r) return "once";
  if (r === "weekdays") return "every weekday";
  if (r === "daily" || r === "day") return "every day";
  if (r === "weekly" || r === "week") return "every week";
  if (r === "hourly" || r === "hour") return "every hour";
  return `every ${r}`;
}

function drawAutos() {
  const autos = app.data.automations || [];

  $("autos").innerHTML = autos.length ? autos.map((a) => row({
    id: `auto-${a.id}`,
    title: esc(a.goal || a.said),
    meta: bits(
      esc(howOften(a)),
      when(a.due),
      a.runs === 1 ? "run once"
        : a.runs ? `run ${a.runs} times` : "not run yet",
      a.enabled ? null : '<span class="tag">off</span>',
    ),
    detail: a.said && a.said !== a.goal ? `you said: ${esc(a.said)}` : "",
    cls: a.enabled ? "" : "off",
    acts: `
      <button class="iconbtn" title="${a.enabled ? "Turn off" : "Turn on"}" data-act="toggle-auto" data-id="${esc(a.id)}" data-on="${a.enabled ? 1 : 0}">${icon("power")}</button>
      <button class="iconbtn danger" title="Delete" data-act="delete-auto" data-id="${esc(a.id)}">${icon("trash-2")}</button>`,
  })).join("") : empty("nothing scheduled - ask Alfred to remind you of something");
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

    case "hello_said":
      pushChat("alfred", event.text);
      if (!event.aloud) toast("greeting kept quiet - night hours");
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
  "edit-skill": async (el) => {
    const now = el.closest(".row").querySelector(".t").textContent.trim();
    const next = await ask("what is this routine for?", now);
    if (next === null || next.trim() === now) return;
    await api("/api/act", { method: "POST", body: JSON.stringify({
      action: "rename_skill",
      payload: { id: el.dataset.id, template: next } }) });
    toast("renamed", "good");
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
  $("memTabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-memgroup]");
    if (!tab) return;
    app.memoryGroup = tab.dataset.memgroup;
    SOUND.select();
    drawMemory();
  });
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
