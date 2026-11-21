// Pit wall dashboard. Receives live updates from server.py over Server-Sent Events.

const $ = (id) => document.getElementById(id);
const TRAIL_MAX = 1200;
const STALE_AFTER_S = 1.5;  // no packet for this long: show "Stale" before "No signal"
const SPEED_COLORS = ["#3b82f6", "#06b6d4", "#22c55e", "#eab308", "#ef4444"];

// Stroke icons (Lucide-style), so status never depends on color alone.
const ICON_PATHS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  danger: '<path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9z"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
};

function icon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"`
    + ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name]}</svg>`;
}

const view = {
  snap: null,
  trail: [],
  gHistory: [],
  lastEventAt: 0,   // when the last message from the server arrived (any kind)
  lastPacketAt: 0,  // when the last kart packet arrived
  serverUp: false,
  frozen: false,
  lapsKey: "",
  linkState: "",
  controlsBuilt: false,
};

// ---------- formatting ----------

function fmtLap(ms, decimals = 3) {
  if (!ms) return "–";
  const m = Math.floor(ms / 60000);
  const s = (ms % 60000) / 1000;
  const width = decimals ? 3 + decimals : 2;
  return `${m}:${s.toFixed(decimals).padStart(width, "0")}`;
}

function fmtGap(ms) {
  if (ms === 0) return "–";
  return `${ms > 0 ? "+" : "−"}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

function setText(id, text) {
  const el = $(id);
  if (el.textContent !== text) el.textContent = text;
}

function setHTML(el, html) {
  if (el.dataset.html !== html) {
    el.dataset.html = html;
    el.innerHTML = html;
  }
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function levelOf(value, warnAt, dangerAt) {
  if (value >= dangerAt) return "danger";
  if (value >= warnAt) return "warn";
  return "ok";
}

// ---------- live connection ----------

function connect() {
  const es = new EventSource("/events");
  es.addEventListener("init", (e) => {
    const snap = JSON.parse(e.data);
    view.trail = snap.trail || [];
    view.lapsKey = "";
    view.serverUp = true;
    apply(snap);
  });
  es.addEventListener("update", (e) => {
    const snap = JSON.parse(e.data);
    if (view.snap && snap.session !== view.snap.session) {
      // Kart started a new session: clear the old map and G history.
      view.trail = [];
      view.gHistory = [];
      view.lapsKey = "";
    }
    if (snap.point) {
      view.trail.push(snap.point);
      if (view.trail.length > TRAIL_MAX) view.trail.shift();
    }
    if (snap.new_packet && snap.latest) {
      view.lastPacketAt = performance.now();
      view.gHistory.push([snap.latest.max_lat_g, snap.latest.max_long_g]);
      if (view.gHistory.length > 40) view.gHistory.shift();
    }
    apply(snap);
  });
  es.onopen = () => { view.serverUp = true; };
  es.onerror = () => { view.serverUp = false; renderLink(); };  // EventSource retries by itself
}

function apply(snap) {
  view.snap = snap;
  view.lastEventAt = performance.now();
  buildControls();
  // Safety info stays live even while the display is frozen.
  renderLink();
  renderAlerts();
  renderMessages();
  if (!view.frozen) renderData();
}

function renderData() {
  renderLive();
  renderBattery();
  renderStrategy();
  renderLaps();
  drawTrack();
  drawGG();
}

// ---------- top bar ----------

function secondsSincePacket() {
  const s = view.snap;
  if (!s || s.since_last_rx_s === null) return null;
  return s.since_last_rx_s + (performance.now() - view.lastEventAt) / 1000;
}

function renderLink() {
  const s = view.snap;
  const el = $("link");
  let state;
  let text;
  if (!view.serverUp) {
    state = "bad";
    text = "Pit server offline";
  } else if (!s) {
    return;
  } else {
    const age = secondsSincePacket();
    if (age === null) {
      state = "";
      text = "Waiting for kart";
    } else if (age > s.config.link_timeout_s) {
      state = "bad";
      text = `No signal · ${Math.floor(age)} s`;
    } else if (age > STALE_AFTER_S) {
      state = "stale";
      text = `Stale · ${age.toFixed(1)} s`;
    } else {
      state = "ok";
      text = `Live · ${age.toFixed(1)} s ago`;
    }
  }
  el.className = `link-pill ${state}`;
  setText("link-text", text);
  if (state !== view.linkState) {
    view.linkState = state;
    // Announce only state changes, not every tick of the timer.
    setText("link-announce", { ok: "Kart link live", stale: "Kart data stale", bad: text }[state] || text);
  }
  if (!s) return;
  const l = s.link;
  const loss = l.loss_pct === null ? "–" : `${l.loss_pct}%`;
  setText("link-stats", `${l.rate_hz.toFixed(1)} pkt/s · ${loss} lost`);
  setText("source", s.source === "simulator" ? "Simulated kart" : `Radio: ${s.source}`);
  setText("session", `Session ${s.session}`);
}

function renderAlerts() {
  const alerts = view.snap.alerts;
  const html = alerts.length
    ? alerts.map((a) => `<div class="alert ${a.level}">${icon(a.level)}<span>${a.text}</span></div>`).join("")
    : `<div class="alert ok">${icon("check")}<span>All systems normal</span></div>`;
  setHTML($("alerts"), html);
}

// ---------- data panels ----------

function renderLive() {
  const t = view.snap.latest;
  if (!t) return;
  setText("lap", String(t.lap));
  setText("last-lap", fmtLap(t.last_lap_ms));
  setText("best-lap", fmtLap(t.best_lap_ms));
  setText("speed", t.speed_kmh.toFixed(0));
  setText("lat-g", `${Math.abs(t.max_lat_g).toFixed(2)} G`);
  setText("long-g", `${t.max_long_g >= 0 ? "+" : "−"}${Math.abs(t.max_long_g).toFixed(2)} G`);
  tickClock();
}

// Lap clock runs smoothly between the 4-per-second packets.
function tickClock() {
  const s = view.snap;
  if (!s || !s.latest || view.frozen) return;
  let ms = s.latest.current_lap_ms;
  const age = secondsSincePacket();
  if (age !== null && age < s.config.link_timeout_s && view.lastPacketAt) {
    ms += performance.now() - view.lastPacketAt;
  }
  setText("current-lap", fmtLap(ms, 1));
}

function setMeter(fill, fraction, level) {
  fill.style.transform = `scaleX(${Math.max(0, Math.min(1, fraction))})`;
  fill.style.backgroundColor = `var(--${level})`;
}

function renderBattery() {
  const s = view.snap;
  const t = s.latest;
  if (!t) return;
  const c = s.config;
  const level = t.charge_pct < 15 ? "danger" : t.charge_pct < 30 ? "warn" : "ok";
  setText("charge", `${t.charge_pct}%`);
  $("charge").className = `hero-num ${level}-text`;
  const chip = $("charge-state");
  setText("charge-state", { ok: "Good", warn: "Low", danger: "Critical" }[level]);
  chip.className = `chip ${level}`;
  setMeter($("charge-bar"), t.charge_pct / 100, level);
  setText("volts", `${t.pack_v.toFixed(1)} V`);
  setText("amps", `${t.pack_a.toFixed(0)} A`);
  setText("power", `${(t.pack_v * t.pack_a / 1000).toFixed(1)} kW`);

  const rows = [
    ["Battery", t.batt_temp_c, c.batt_temp_max_c],
    ["Motor", t.motor_temp_c, c.motor_temp_max_c],
    ["Controller", t.ctrl_temp_c, c.ctrl_temp_max_c],
  ];
  const box = $("temps");
  if (box.children.length !== rows.length) {
    box.innerHTML = rows.map(([name]) => `<div class="temp"><span class="name">${name}</span>`
      + '<div class="meter"><div class="meter-fill"></div></div><span class="value"></span><span class="state"></span></div>').join("");
  }
  rows.forEach(([, temp, max], i) => {
    const row = box.children[i];
    const lvl = levelOf(temp, max - c.temp_warn_margin_c, max);
    setMeter(row.querySelector(".meter-fill"), (temp - 20) / (max + 10 - 20), lvl);
    row.querySelector(".value").textContent = `${temp} °C`;
    const state = row.querySelector(".state");
    state.textContent = { ok: "OK", warn: "Warm", danger: "Hot" }[lvl];
    state.className = `state ${lvl}-text`;
  });
}

function renderStrategy() {
  const s = view.snap;
  const st = s.strategy;
  let level = "";
  let iconName = "";
  let text;
  let sub;
  if (st.wh_per_lap === null) {
    text = "Collecting data";
    sub = "Needs one full lap to estimate energy use";
  } else if (st.margin < 0) {
    level = "danger"; iconName = "danger";
    text = "Won't finish";
    sub = `Short by ${(-st.margin).toFixed(1)} laps at this pace`;
  } else if (st.margin < 1) {
    level = "warn"; iconName = "warn";
    text = "Tight";
    sub = `Only ${st.margin.toFixed(1)} laps of spare energy`;
  } else {
    level = "ok"; iconName = "check";
    text = "On target";
    sub = `${st.margin.toFixed(1)} laps of spare energy, room to push`;
  }
  $("verdict").className = `verdict ${level ? level + "-text" : ""}`;
  setHTML($("verdict-icon"), iconName ? icon(iconName) : "");
  setText("verdict-text", text);
  setText("verdict-sub", sub);
  setText("wh-lap", st.wh_per_lap === null ? "–" : `${st.wh_per_lap.toFixed(1)} Wh`);
  setText("laps-possible", st.laps_possible === null ? "–" : st.laps_possible.toFixed(1));
  setText("laps-left", st.laps_left === null ? "–" : String(st.laps_left));
  setText("pct-lap", st.wh_per_lap === null ? "–"
    : `${(st.wh_per_lap / s.config.pack_capacity_wh * 100).toFixed(1)}%`);

  // Don't overwrite a box the engineer is typing in.
  for (const [id, key] of [["race-laps", "race_laps"], ["pack-wh", "pack_capacity_wh"]]) {
    const input = $(id);
    if (document.activeElement !== input) input.value = s.config[key];
  }
}

function renderLaps() {
  const laps = view.snap.laps;
  const key = `${view.snap.session}:${laps.length}`;
  if (key === view.lapsKey) return;
  view.lapsKey = key;
  const body = $("lap-rows");
  if (!laps.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">No laps yet</td></tr>';
    return;
  }
  const best = Math.min(...laps.map((l) => l.time_ms).filter(Boolean));
  body.innerHTML = laps.slice().reverse().map((l) => {
    const isBest = l.time_ms === best;
    const energy = l.complete ? `${l.wh.toFixed(1)} Wh` : '<span class="muted">partial</span>';
    const used = l.charge_used === null ? "–" : `${l.charge_used}%`;
    return `<tr class="${isBest ? "best" : ""}"><td>${l.lap}</td>`
      + `<td>${fmtLap(l.time_ms)}${isBest ? '<span class="best-pill">BEST</span>' : ""}</td>`
      + `<td>${fmtGap(l.time_ms - best)}</td><td>${energy}</td><td>${used}</td>`
      + `<td>${l.max_speed_kmh.toFixed(0)} km/h</td></tr>`;
  }).join("");
}

// ---------- driver messages ----------

function buildControls() {
  if (view.controlsBuilt) return;
  view.controlsBuilt = true;
  const entries = Object.entries(view.snap.catalog).map(([code, text]) => [Number(code), text]);
  const button = ([code, text], label, aria) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.code = String(code);
    b.textContent = label;
    if (aria) b.setAttribute("aria-label", aria);
    b.addEventListener("click", () => post("/api/message", { code }));
    return b;
  };
  // Main calls first; the per-sector calls grouped as one control below.
  $("msg-primary").replaceChildren(...entries.filter(([, t]) => !t.startsWith("SECTOR")).map((e) => button(e, e[1])));
  $("msg-sector").replaceChildren(...entries.filter(([, t]) => t.startsWith("SECTOR"))
    .map((e) => button(e, e[1].replace("SECTOR ", ""), `Losing time in ${e[1].toLowerCase()}`)));
}

function renderMessages() {
  const m = view.snap.message;
  const pending = m && (m.status === "sending" || m.status === "delivered");
  document.querySelectorAll("#msg-primary button, #msg-sector button").forEach((b) => {
    b.classList.toggle("active", Boolean(pending && Number(b.dataset.code) === m.code));
  });

  // Steps: 0 Sent, 1 On screen, 2 Driver OK. [last step done, step in progress, step that failed]
  const [done, current, failedStep] = !m ? [-1, -1, -1] : {
    sending: [0, 1, -1],
    delivered: [1, 2, -1],
    acknowledged: [2, -1, -1],
    failed: [0, -1, 1],
  }[m.status];
  document.querySelectorAll("#msg-steps li").forEach((li) => {
    const step = Number(li.dataset.step);
    li.className = step === failedStep ? "failed" : step <= done ? "done" : step === current ? "current" : "";
  });

  const el = $("msg-status");
  if (!m) {
    setText("msg-status", "No message sent yet");
    el.className = "msg-status muted";
    return;
  }
  const after = (t) => `${(t - m.sent_at).toFixed(1)} s`;
  const text = {
    sending: `Sending ${m.text} to the kart (try ${m.attempts})…`,
    delivered: `${m.text} is on the driver's screen. Waiting for them to press OK.`,
    acknowledged: `Driver acknowledged ${m.text} after ${after(m.acked_at)}.`,
    failed: `${m.text} never reached the kart. Try again or use the pit board.`,
  }[m.status];
  setText("msg-status", text);
  el.className = `msg-status ${m.status === "acknowledged" ? "ok-text" : m.status === "failed" ? "danger-text" : ""}`;
}

// ---------- drawing ----------

function fitCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(rect.width * dpr);
  const h = Math.round(rect.height * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  return { ctx, w: rect.width, h: rect.height };
}

function speedColor(f) {
  const x = Math.max(0, Math.min(1, f)) * (SPEED_COLORS.length - 1);
  return SPEED_COLORS[Math.round(x)];
}

function drawTrack() {
  const { ctx, w, h } = fitCanvas($("track"));
  const pts = view.trail;
  if (pts.length < 2) {
    ctx.fillStyle = cssVar("--text-2");
    ctx.font = "14px system-ui, sans-serif";
    ctx.fillText("Waiting for GPS…", 4, 20);
    return;
  }
  // GPS degrees -> meters around the first point, then fit to the canvas.
  const lat0 = pts[0][0];
  const lon0 = pts[0][1];
  const kx = 111320 * Math.cos(lat0 * Math.PI / 180);
  const xy = pts.map((p) => [(p[1] - lon0) * kx, (p[0] - lat0) * 111320]);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [x, y] of xy) {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  const pad = 18;
  const scale = Math.min((w - 2 * pad) / Math.max(maxX - minX, 1), (h - 2 * pad) / Math.max(maxY - minY, 1));
  const offX = (w - (maxX - minX) * scale) / 2;
  const offY = (h - (maxY - minY) * scale) / 2;
  const px = ([x, y]) => [offX + (x - minX) * scale, h - (offY + (y - minY) * scale)];

  // Scale colors to the typical speed range, so a standing start or one
  // odd reading doesn't wash the whole map out.
  const speeds = pts.map((p) => p[2]).sort((a, b) => a - b);
  const vMin = speeds[Math.floor(speeds.length * 0.05)];
  const vMax = speeds[Math.floor(speeds.length * 0.95)];
  setText("legend-min", `${vMin.toFixed(0)} km/h`);
  setText("legend-max", `${vMax.toFixed(0)} km/h`);

  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  for (let i = 1; i < xy.length; i++) {
    if (Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]) > 50) continue;  // GPS jump
    const [x1, y1] = px(xy[i - 1]);
    const [x2, y2] = px(xy[i]);
    ctx.strokeStyle = speedColor((pts[i][2] - vMin) / Math.max(vMax - vMin, 1));
    ctx.globalAlpha = 0.3 + 0.7 * (i / xy.length);  // older laps fade
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const [cx, cy] = px(xy[xy.length - 1]);
  ctx.beginPath();
  ctx.arc(cx, cy, 8, 0, Math.PI * 2);
  ctx.fillStyle = cssVar("--text");
  ctx.fill();
  ctx.lineWidth = 3;
  ctx.strokeStyle = cssVar("--surface");
  ctx.stroke();
}

function drawGG() {
  const { ctx, w, h } = fitCanvas($("gg"));
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.min(w, h) / 2 - 14;
  const perG = r / 1.5;  // outer ring = 1.5 G
  ctx.strokeStyle = cssVar("--track-bg");
  ctx.fillStyle = cssVar("--text-2");
  ctx.font = "11px system-ui, sans-serif";
  ctx.lineWidth = 1;
  for (const g of [0.5, 1.0, 1.5]) {
    ctx.beginPath();
    ctx.arc(cx, cy, g * perG, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(`${g}G`, cx + g * perG * 0.71 + 3, cy - g * perG * 0.71);
  }
  ctx.beginPath();
  ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy);
  ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r);
  ctx.stroke();
  ctx.fillText("accel", cx + 4, cy - r + 10);
  ctx.fillText("brake", cx + 4, cy + r - 2);

  const trailColor = cssVar("--text-2");
  view.gHistory.forEach(([lat, long], i) => {
    const last = i === view.gHistory.length - 1;
    ctx.globalAlpha = last ? 1 : 0.12 + 0.45 * (i / view.gHistory.length);
    ctx.beginPath();
    ctx.arc(cx - lat * perG, cy - long * perG, last ? 7 : 4, 0, Math.PI * 2);
    ctx.fillStyle = last ? cssVar("--text") : trailColor;
    ctx.fill();
  });
  ctx.globalAlpha = 1;
}

// ---------- actions ----------

async function post(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).error || res.statusText);
  } catch (err) {
    alert(`Couldn't reach the pit server: ${err.message}`);
  }
}

$("race-laps").addEventListener("change", (e) => post("/api/config", { race_laps: Number(e.target.value) }));
$("pack-wh").addEventListener("change", (e) => post("/api/config", { pack_capacity_wh: Number(e.target.value) }));

function setFrozen(frozen) {
  view.frozen = frozen;
  const b = $("freeze");
  b.setAttribute("aria-pressed", String(frozen));
  b.innerHTML = frozen ? `${icon("play")}<span>Resume</span>` : `${icon("pause")}<span>Freeze</span>`;
  b.setAttribute("aria-label", frozen ? "Resume live display" : "Freeze display");
  b.title = b.getAttribute("aria-label");
  $("frozen-chip").hidden = !frozen;
  if (!frozen && view.snap) renderData();
}
$("freeze").addEventListener("click", () => setFrozen(!view.frozen));

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const b = $("theme-toggle");
  b.innerHTML = theme === "sun" ? `${icon("moon")}<span>Dark mode</span>` : `${icon("sun")}<span>Sun mode</span>`;
  b.setAttribute("aria-label", theme === "sun" ? "Switch to dark mode" : "Switch to Sun mode (high contrast)");
  b.title = b.getAttribute("aria-label");
  try { localStorage.setItem("pit-theme", theme); } catch (e) { /* private mode */ }
  if (view.snap) { drawTrack(); drawGG(); }
}
$("theme-toggle").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "sun" ? "dark" : "sun");
});

let savedTheme = "dark";
try { savedTheme = localStorage.getItem("pit-theme") || "dark"; } catch (e) { /* private mode */ }
setTheme(savedTheme);
setFrozen(false);

// Edge shadow under the translucent header only once content scrolls beneath it.
window.addEventListener("scroll", () => {
  $("topbar").classList.toggle("scrolled", window.scrollY > 4);
}, { passive: true });
window.addEventListener("resize", () => { if (view.snap && !view.frozen) { drawTrack(); drawGG(); } });
setInterval(() => { tickClock(); renderLink(); }, 100);

connect();
