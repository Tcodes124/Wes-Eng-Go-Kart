// Pit wall dashboard. Receives live updates from server.py over Server-Sent Events.

const $ = (id) => document.getElementById(id);
const TRAIL_MAX = 1200;
const SPEED_COLORS = ["#3b82f6", "#06b6d4", "#22c55e", "#eab308", "#ef4444"];

const view = {
  snap: null,
  trail: [],
  gHistory: [],
  lastEventAt: 0,   // when the last message from the server arrived (any kind)
  lastPacketAt: 0,  // when the last kart packet arrived
  serverUp: false,
  lapsShown: -1,
  buttonsBuilt: false,
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
  if (ms === 0) return "best";
  return `${ms > 0 ? "+" : "−"}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

function setText(id, text) {
  const el = $(id);
  if (el.textContent !== text) el.textContent = text;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---------- live connection ----------

function connect() {
  const es = new EventSource("/events");
  es.addEventListener("init", (e) => {
    const snap = JSON.parse(e.data);
    view.trail = snap.trail || [];
    view.lapsShown = -1;
    view.serverUp = true;
    apply(snap);
  });
  es.addEventListener("update", (e) => {
    const snap = JSON.parse(e.data);
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
  renderLink();
  renderAlerts();
  renderLive();
  renderBattery();
  renderStrategy();
  renderMessages();
  renderLaps();
  drawTrack();
  drawGG();
}

// ---------- panels ----------

function secondsSincePacket() {
  const s = view.snap;
  if (!s || s.since_last_rx_s === null) return null;
  return s.since_last_rx_s + (performance.now() - view.lastEventAt) / 1000;
}

function renderLink() {
  const s = view.snap;
  const el = $("link");
  el.className = "link";
  if (!view.serverUp) {
    el.classList.add("bad");
    setText("link-text", "Pit server offline");
    return;
  }
  if (!s) return;
  const age = secondsSincePacket();
  if (age === null) {
    setText("link-text", "Waiting for kart…");
  } else if (age > s.config.link_timeout_s) {
    el.classList.add("bad");
    setText("link-text", `NO SIGNAL ${Math.floor(age)} s`);
  } else {
    el.classList.add("ok");
    setText("link-text", "LIVE");
  }
  const l = s.link;
  const loss = l.loss_pct === null ? "–" : `${l.loss_pct}%`;
  setText("link-stats", `${l.rate_hz.toFixed(1)} packets/s · ${loss} lost · ${l.bad} corrupted`);
  setText("source", `Source: ${s.source} (${s.source_status})`);
}

function renderAlerts() {
  const box = $("alerts");
  const alerts = view.snap.alerts;
  const key = JSON.stringify(alerts);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(...alerts.map((a) => {
    const div = document.createElement("div");
    div.className = `alert ${a.level}`;
    div.textContent = a.text;
    return div;
  }));
}

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
  if (!s || !s.latest) return;
  let ms = s.latest.current_lap_ms;
  const age = secondsSincePacket();
  if (age !== null && age < s.config.link_timeout_s && view.lastPacketAt) {
    ms += performance.now() - view.lastPacketAt;
  }
  setText("current-lap", fmtLap(ms, 1));
}

function levelClass(value, warnAt, dangerAt) {
  if (value >= dangerAt) return "danger";
  if (value >= warnAt) return "warn";
  return "ok";
}

function renderBattery() {
  const s = view.snap;
  const t = s.latest;
  if (!t) return;
  const c = s.config;
  const chargeLevel = t.charge_pct < 15 ? "danger" : t.charge_pct < 30 ? "warn" : "ok";
  const charge = $("charge");
  setText("charge", `${t.charge_pct}%`);
  charge.className = `charge ${chargeLevel}-text`;
  const bar = $("charge-bar");
  bar.style.width = `${t.charge_pct}%`;
  bar.style.background = `var(--${chargeLevel})`;
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
    box.replaceChildren(...rows.map(() => {
      const row = document.createElement("div");
      row.className = "temp";
      row.innerHTML = '<label></label><div class="bar"><div class="fill"></div></div><b></b>';
      return row;
    }));
  }
  rows.forEach(([name, temp, max], i) => {
    const row = box.children[i];
    const level = levelClass(temp, max - c.temp_warn_margin_c, max);
    row.querySelector("label").textContent = name;
    const fill = row.querySelector(".fill");
    fill.style.width = `${Math.max(0, Math.min(100, (temp - 20) / (max + 10 - 20) * 100))}%`;
    fill.style.background = `var(--${level})`;
    const b = row.querySelector("b");
    b.textContent = `${temp} °C`;
    b.className = level === "ok" ? "" : `${level}-text`;
  });
}

function renderStrategy() {
  const s = view.snap;
  const st = s.strategy;
  const verdict = $("verdict");
  if (st.wh_per_lap === null) {
    verdict.className = "verdict";
    setText("verdict", "Collecting data");
    setText("verdict-sub", "Needs one full lap to estimate energy use");
  } else if (st.margin < 0) {
    verdict.className = "verdict danger-text";
    setText("verdict", "WON'T FINISH");
    setText("verdict-sub", `Short by ${(-st.margin).toFixed(1)} laps at this pace`);
  } else if (st.margin < 1) {
    verdict.className = "verdict warn-text";
    setText("verdict", "TIGHT");
    setText("verdict-sub", `Only ${st.margin.toFixed(1)} laps of spare energy`);
  } else {
    verdict.className = "verdict ok-text";
    setText("verdict", "ON TARGET");
    setText("verdict-sub", `${st.margin.toFixed(1)} laps of spare energy, room to push`);
  }
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

function renderMessages() {
  const s = view.snap;
  if (!view.buttonsBuilt) {
    view.buttonsBuilt = true;
    $("msg-buttons").replaceChildren(...Object.entries(s.catalog).map(([code, text]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      b.addEventListener("click", () => sendMessage(Number(code)));
      return b;
    }));
  }
  const m = s.message;
  const el = $("msg-status");
  if (!m) return;
  const after = (t) => `${(t - m.sent_at).toFixed(1)} s`;
  const text = {
    sending: `${m.text}: sending (try ${m.attempts})…`,
    delivered: `${m.text}: on the driver's screen, waiting for them to press OK`,
    acknowledged: `✓ ${m.text}: driver acknowledged after ${after(m.acked_at)}`,
    failed: `✗ ${m.text}: never reached the kart. Try again or use the pit board`,
  }[m.status];
  setText("msg-status", text);
  el.className = `msg-status ${m.status === "acknowledged" ? "ok-text" : m.status === "failed" ? "danger-text" : "warn-text"}`;
}

function renderLaps() {
  const laps = view.snap.laps;
  if (laps.length === view.lapsShown) return;
  view.lapsShown = laps.length;
  const body = $("lap-rows");
  if (!laps.length) return;
  const best = Math.min(...laps.map((l) => l.time_ms).filter(Boolean));
  body.replaceChildren(...laps.slice().reverse().map((l) => {
    const tr = document.createElement("tr");
    if (l.time_ms === best) tr.className = "best";
    const energy = l.complete ? `${l.wh.toFixed(1)} Wh` : "partial";
    const used = l.charge_used === null ? "–" : `${l.charge_used}%`;
    tr.innerHTML = `<td>${l.lap}</td><td>${fmtLap(l.time_ms)}</td><td>${fmtGap(l.time_ms - best)}</td>`
      + `<td>${energy}</td><td>${used}</td><td>${l.max_speed_kmh.toFixed(0)} km/h</td>`;
    return tr;
  }));
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
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "14px sans-serif";
    ctx.fillText("Waiting for GPS…", 12, 24);
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
  const pad = 16;
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

  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  for (let i = 1; i < xy.length; i++) {
    if (pts[i][3] !== pts[i - 1][3] && Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]) > 50) continue;
    const [x1, y1] = px(xy[i - 1]);
    const [x2, y2] = px(xy[i]);
    ctx.strokeStyle = speedColor((pts[i][2] - vMin) / Math.max(vMax - vMin, 1));
    ctx.globalAlpha = 0.35 + 0.65 * (i / xy.length);  // older laps fade
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
  ctx.strokeStyle = cssVar("--bg");
  ctx.stroke();
}

function drawGG() {
  const { ctx, w, h } = fitCanvas($("gg"));
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.min(w, h) / 2 - 14;
  const perG = r / 1.5;  // outer ring = 1.5 G
  ctx.strokeStyle = cssVar("--grid-line");
  ctx.fillStyle = cssVar("--muted");
  ctx.font = "11px sans-serif";
  ctx.lineWidth = 1;
  for (const g of [0.5, 1.0, 1.5]) {
    ctx.beginPath();
    ctx.arc(cx, cy, g * perG, 0, Math.PI * 2);
    ctx.stroke();
    ctx.fillText(`${g}G`, cx + g * perG * 0.71 + 2, cy - g * perG * 0.71);
  }
  ctx.beginPath();
  ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy);
  ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r);
  ctx.stroke();
  ctx.fillText("accel", cx + 4, cy - r + 10);
  ctx.fillText("brake", cx + 4, cy + r - 2);

  const accent = cssVar("--accent");
  view.gHistory.forEach(([lat, long], i) => {
    const last = i === view.gHistory.length - 1;
    ctx.globalAlpha = last ? 1 : 0.15 + 0.5 * (i / view.gHistory.length);
    ctx.beginPath();
    ctx.arc(cx - lat * perG, cy - long * perG, last ? 7 : 4, 0, Math.PI * 2);
    ctx.fillStyle = last ? cssVar("--text") : accent;
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

function sendMessage(code) {
  post("/api/message", { code });
}

$("race-laps").addEventListener("change", (e) => post("/api/config", { race_laps: Number(e.target.value) }));
$("pack-wh").addEventListener("change", (e) => post("/api/config", { pack_capacity_wh: Number(e.target.value) }));

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").textContent = theme === "sun" ? "Dark mode" : "Sun mode";
  try { localStorage.setItem("pit-theme", theme); } catch (e) { /* private mode */ }
  if (view.snap) { drawTrack(); drawGG(); }
}
$("theme-toggle").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "sun" ? "dark" : "sun");
});
try { setTheme(localStorage.getItem("pit-theme") || "dark"); } catch (e) { setTheme("dark"); }

window.addEventListener("resize", () => { if (view.snap) { drawTrack(); drawGG(); } });
setInterval(() => { tickClock(); renderLink(); }, 100);

connect();
