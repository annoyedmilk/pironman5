"use strict";

const $ = (id) => document.getElementById(id);
const NA = "n/a";
const SVG = "http://www.w3.org/2000/svg";

// Seconds of each history range, and the series of each plot.
const RANGES = { "10m": 600, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000 };
const POINTS = 360;
const PLOTS = [
  { id: "plot-load", series: [["cpu_percent", "cpu", "CPU"], ["memory_percent", "mem", "Memory"]] },
  { id: "plot-temp", series: [["cpu_temperature", "temp", "Temperature"]], area: true },
];
const HEALTH = { ok: ["", "OK"], past: ["earlier", "Occurred since boot"], now: ["now", "Active now"] };

let cfg = null; // config of the last frame
const trend = []; // recent values for the sparklines
const TREND_SIZE = 120;

// ---- format ----------------------------------------------------------------

const tempUnit = () => (cfg ? cfg.system.temperature_unit : "C");
const toUnit = (c) => (tempUnit() === "F" ? (c * 9) / 5 + 32 : c);
const fmtTemp = (c, digits = 0) => (c == null ? NA : `${toUnit(c).toFixed(digits)} °${tempUnit()}`);
const fmtPct = (v) => (v == null ? NA : `${Math.round(v)} %`);
const fmtVolt = (v) => (v == null ? NA : `${v.toFixed(2)} V`);
const gb = (n) => (n / 2 ** 30).toFixed(1);

function fmtRate(n) {
  const units = ["B/s", "kB/s", "MB/s", "GB/s"];
  let i = 0;
  for (n = n || 0; n >= 1024 && i < units.length - 1; i++) n /= 1024;
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

function set(id, value) {
  const el = $(id), text = String(value);
  if (el.textContent !== text) el.textContent = text;
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3000);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// ---- live frame ------------------------------------------------------------

function render(f) {
  cfg = f.config;
  document.title = f.hostname ? `${f.hostname} · Pironman 5` : "Pironman 5";
  set("hostname", f.hostname || "");
  $("mock-tag").hidden = !f.mock;

  set("cpu", Math.round(f.cpu_percent));
  const freq = f.cpu_freq ? `${(f.cpu_freq / 1000).toFixed(1)} GHz` : null;
  const load = f.load_avg ? `load ${f.load_avg[0].toFixed(2)}` : null;
  set("cpu-sub", [`${f.cpu_count || 0} cores`, freq, load].filter(Boolean).join(" · "));
  renderCores(f.cpu_per_core);

  const t = f.cpu_temperature;
  set("temp", t == null ? NA : toUnit(t).toFixed(1));
  set("temp-unit", t == null ? "" : `°${tempUnit()}`);
  const rpm = f.cpu_fan_rpm == null ? NA : `${f.cpu_fan_rpm} rpm`;
  set("temp-sub", `GPU ${fmtTemp(f.gpu_temperature)} · NVMe ${fmtTemp(f.nvme_temperature)} · fan ${rpm}`);

  set("lg-cpu", fmtPct(f.cpu_percent));
  set("lg-mem", fmtPct(f.memory_percent));
  set("lg-temp", fmtTemp(t, 1));

  set("mem", Math.round(f.memory_percent));
  meter("mem-meter", f.memory_percent);
  const swap = f.swap_total ? `swap ${gb(f.swap_used)} of ${gb(f.swap_total)} GB` : "no swap";
  set("mem-sub", `${gb(f.memory_used)} of ${gb(f.memory_total)} GB · ${swap}`);

  set("disk", Math.round(f.disk_percent));
  meter("disk-meter", f.disk_percent);
  const io = f.disk_read == null ? "IO n/a" : `R ${fmtRate(f.disk_read)} · W ${fmtRate(f.disk_write)}`;
  set("disk-sub", `${Math.round(f.disk_used / 2 ** 30)} of ${Math.round(f.disk_total / 2 ** 30)} GB · ${io}`);

  set("power", f.power_watts == null ? NA : f.power_watts.toFixed(1));
  set("power-unit", f.power_watts == null ? "" : "W");
  set("input-v", fmtVolt(f.input_voltage));
  set("core-v", fmtVolt(f.cpu_voltage));
  set("batt-v", fmtVolt(f.battery_voltage));
  renderHealth(f.throttle || {});

  const [iface, ip] = Object.entries(f.ips)[0] || [NA, NA];
  set("net-if", iface);
  set("net-ip", ip);
  set("net-mac", f.macs[iface] || NA);
  set("net-down", fmtRate(f.net_download));
  set("net-up", fmtRate(f.net_upload));
  set("net-link", f.link_speed == null ? NA : `${f.link_speed} Mbps`);

  set("sys-model", f.model || NA);
  set("sys-kernel", f.kernel || NA);
  set("sys-uptime", fmtUptime(f.uptime));
  set("sys-procs", f.processes);
  set("sys-load", f.load_avg ? f.load_avg.map((v) => v.toFixed(2)).join("  ") : NA);

  set("case-fan", f.case_fan_on ? "On" : "Off");
  set("fan-state", `Fans are ${f.case_fan_on ? "on" : "off"} at ${fmtTemp(t, 1)}`);
  renderOled(f.oled_preview);

  trend.push({ cpu: f.cpu_percent, temp: f.cpu_temperature });
  if (trend.length > TREND_SIZE) trend.shift();
  spark("cpu-spark", trend.map((s) => s.cpu), 0, 100);
  const temps = trend.map((s) => s.temp);
  const known = temps.filter((v) => v != null);
  spark("temp-spark", temps, Math.min(...known) - 3, Math.max(...known) + 3);

  syncControls(false);
}

// Fill the sparklines from the history, so they are not empty after a page load.
async function seedTrend() {
  try {
    const { samples } = await api("GET", "/api/v1/history?range=10m");
    trend.push(...samples.slice(-TREND_SIZE).map((s) => ({ cpu: s.cpu_percent, temp: s.cpu_temperature })));
  } catch (e) { /* the stream fills them */ }
}

function meter(id, percent) {
  const el = $(id);
  el.style.setProperty("--v", `${Math.min(100, percent)}%`);
  el.classList.toggle("warn", percent >= 85 && percent < 95);
  el.classList.toggle("crit", percent >= 95);
}

function renderCores(cores) {
  const box = $("cores");
  while (box.children.length < cores.length) box.appendChild(document.createElement("i"));
  while (box.children.length > cores.length) box.lastChild.remove();
  cores.forEach((v, i) => {
    box.children[i].style.setProperty("--v", `${v}%`);
    box.children[i].title = `Core ${i}: ${Math.round(v)} %`;
  });
}

function renderHealth(throttle) {
  for (const li of $("health").children) {
    const state = throttle[li.dataset.k];
    const [short, long] = HEALTH[state] || ["", "No data"];
    li.className = state || "";
    li.title = long;
    li.querySelector("use").setAttribute("href", `#i-${state || "none"}`);
    li.querySelector("em").textContent = short;
  }
}

function spark(id, values, lo, hi) {
  const x = (i) => 120 - (values.length - 1 - i) * (120 / (TREND_SIZE - 1));
  const pts = values
    .map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${(31 - ((v - lo) / (hi - lo)) * 30).toFixed(1)}`))
    .filter(Boolean);
  if (pts.length < 2) return;
  const first = pts[0].split(",")[0];
  $(id).innerHTML =
    `<path class="area" d="M${pts.join("L")}L120,32L${first},32Z"/><path class="line" d="M${pts.join("L")}"/>`;
}

// ---- case preview ----------------------------------------------------------

let oledShown = null;
function renderOled(preview) {
  // A dim panel is still readable, so the copy never goes below 30 % opacity.
  const opacity = preview.awake ? 0.3 + (0.7 * cfg.oled.brightness) / 100 : 0.15;
  const key = preview.fb + opacity;
  if (key === oledShown) return;
  oledShown = key;
  const pages = atob(preview.fb);
  for (const canvas of document.querySelectorAll(".oled-canvas")) {
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(128, 64);
    for (let i = 0; i < pages.length * 8; i++) {
      const lit = (pages.charCodeAt(i >> 3) >> (i & 7)) & 1;
      // Byte i>>3 is page p, column x. Bit i&7 is the row in the page.
      const p = ((i >> 3) >> 7) * 8 + (i & 7), x = (i >> 3) & 127;
      img.data.set(lit ? [214, 236, 255, 255] : [0, 0, 0, 255], (p * 128 + x) * 4);
    }
    ctx.putImageData(img, 0, 0);
    canvas.style.opacity = opacity;
  }
}

// The LED effects of hardware/rgb.py, so the preview is smooth without a fast stream.
function ledColors(c, t) {
  const n = c.led_count, k = c.brightness / 100;
  const hex = (h) => [0, 2, 4].map((i) => parseInt(h.replace("#", "").slice(i, i + 2), 16) * k);
  const hue = (deg) => {
    const h = (((deg % 360) + 360) % 360) / 60, f = h % 1;
    return [[1, f, 0], [1 - f, 1, 0], [0, 1, f], [0, 1 - f, 1], [f, 0, 1], [1, 0, 1 - f]][Math.floor(h)].map((v) => v * 255 * k);
  };
  const speed = (slow, fast) => slow + ((fast - slow) * c.speed) / 100;
  const degrees = (t * 2) / speed(0.1, 0.005);
  const all = (color) => Array(n).fill(color);
  const one = (i) => all([0, 0, 0]).map((off, j) => (j === i ? hex(c.color) : off));
  if (!c.enable) return all([0, 0, 0]);
  if (!c.sync) return c.colors.map(hex);
  const rainbow = () => all(0).map((_, i) => hue((i * 360) / n + degrees));
  const lit = Math.floor(t / speed(0.5, 0.1)) % n;
  return {
    solid: () => all(hex(c.color)),
    breathing: () => all(hex(c.color).map((v) => v * (1 - Math.abs(2 * ((t / speed(10, 1)) % 1) - 1)))),
    flow: () => one(lit),
    flow_reverse: () => one(n - 1 - lit),
    rainbow,
    rainbow_reverse: () => rainbow().reverse(),
    hue_cycle: () => all(hue(degrees)),
  }[c.style]();
}

function animateLeds(ms) {
  requestAnimationFrame(animateLeds);
  if (!cfg) return;
  const colors = ledColors(cfg.rgb, ms / 1000).map((c) => `rgb(${c.map(Math.round).join(",")})`);
  for (const box of document.querySelectorAll(".leds")) {
    while (box.children.length < colors.length) box.appendChild(document.createElement("i"));
    while (box.children.length > colors.length) box.lastChild.remove();
    colors.forEach((color, i) => {
      const led = box.children[i];
      if (led.dataset.c === color) return;
      led.dataset.c = color;
      led.style.background = color;
      led.style.boxShadow = `0 0 10px ${color}`;
    });
  }
}

// ---- stream ----------------------------------------------------------------

const RETRY = [1, 2, 4, 8, 15];
let socket = null, retries = 0;

function connect() {
  if (socket || document.hidden) return;
  const ws = (socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/stream`));
  ws.onmessage = (ev) => {
    retries = 0;
    setLive(true);
    render(JSON.parse(ev.data));
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    setLive(false);
    setTimeout(connect, RETRY[Math.min(retries++, RETRY.length - 1)] * 1000);
  };
}

function setLive(live) {
  document.body.classList.toggle("stale", !live);
  $("conn").classList.toggle("live", live);
  set("conn-text", live ? "Live" : "Reconnecting");
}

// A hidden tab closes the stream, so the service does no work for it.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return connect(), loadHistory();
  const ws = socket;
  socket = null;
  if (ws) ws.close();
});

// ---- history chart ---------------------------------------------------------

const chart = { range: "1h", samples: [], index: null, anchor: 0, scales: [] };

async function loadHistory() {
  if (document.hidden) return;
  $("chart").classList.add("loading");
  try {
    chart.samples = (await api("GET", `/api/v1/history?range=${chart.range}`)).samples;
  } catch (e) { /* keep the last data, the header shows the connection state */ }
  $("chart").classList.remove("loading");
  drawChart();
}

function scheduleHistory() {
  clearInterval(scheduleHistory.timer);
  const seconds = Math.max(5, Math.min(300, RANGES[chart.range] / POINTS));
  scheduleHistory.timer = setInterval(loadHistory, seconds * 1000);
  loadHistory();
}

const value = (sample, key) => (sample[key] == null ? null : key === "cpu_temperature" ? toUnit(sample[key]) : sample[key]);

function timeTicks(t0, t1, count) {
  const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 432000, 604800];
  const step = steps.find((s) => (t1 - t0) / s <= count) || steps[steps.length - 1];
  const zone = new Date(t1 * 1000).getTimezoneOffset() * 60;
  const ticks = [];
  for (let t = Math.ceil((t0 - zone) / step) * step + zone; t <= t1; t += step) ticks.push(t);
  const fmt = step >= 86400
    ? { month: "short", day: "numeric" }
    : t1 - t0 > 86400 ? { weekday: "short", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" };
  return ticks.map((t) => [t, new Date(t * 1000).toLocaleString([], fmt)]);
}

function drawPlot(plot, t0, t1) {
  const svg = $(plot.id), W = svg.clientWidth, H = svg.clientHeight;
  const m = { l: 44, r: 10, t: 8, b: 24 };
  const values = chart.samples.flatMap((s) => plot.series.map(([key]) => value(s, key))).filter((v) => v != null);

  // The load plot is 0 to 100. The temperature plot fits its data.
  let lo = 0, hi = 100, step = 50, suffix = " %";
  if (plot.area) {
    step = values.length && Math.max(...values) - Math.min(...values) > 30 ? 10 : 5;
    lo = Math.floor(((values.length ? Math.min(...values) : 40) - 1) / step) * step;
    hi = Math.max(lo + 2 * step, Math.ceil(((values.length ? Math.max(...values) : 50) + 1) / step) * step);
    suffix = "°";
  }
  const x = (t) => m.l + ((t - t0) / (t1 - t0)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);

  let out = "";
  for (let v = lo; v <= hi; v += step) {
    out += `<line class="grid-line" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
    out += `<text class="tick" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}${suffix}</text>`;
  }
  for (const [t, label] of timeTicks(t0, t1, Math.max(2, Math.floor(W / 110)))) {
    out += `<text class="tick" x="${x(t)}" y="${H - 6}" text-anchor="middle">${label}</text>`;
  }
  // A line breaks where samples are missing.
  const gap = Math.max(150, (3 * (t1 - t0)) / POINTS);
  for (const [key, cls] of plot.series) {
    let d = "", last = null, runStart = null;
    for (const s of chart.samples) {
      const v = value(s, key);
      if (v == null) continue;
      const fresh = last == null || s.time - last > gap;
      if (fresh && runStart != null && plot.area) out += areaPath(d, runStart, x(last), y(lo), cls);
      if (fresh) runStart = d.length;
      d += `${fresh ? "M" : "L"}${x(s.time).toFixed(1)},${y(v).toFixed(1)}`;
      last = s.time;
    }
    if (plot.area && runStart != null) out += areaPath(d, runStart, x(last), y(lo), cls);
    out += `<path class="line ${cls}" d="${d}"/>`;
  }
  if (!values.length) out += `<text class="empty" x="${(W + m.l) / 2}" y="${H / 2}" text-anchor="middle">No data in this range</text>`;
  out += `<line class="cross" y1="${m.t}" y2="${H - m.b}" hidden/>`;
  out += plot.series.map(([, cls]) => `<circle class="dot ${cls}" r="4.5" hidden/>`).join("");

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = out;
  return { svg, x, y, plot };
}

function areaPath(d, start, xEnd, yBase, cls) {
  const run = d.slice(start), x0 = run.slice(1, run.indexOf(","));
  return `<path class="area ${cls}" d="${run}L${xEnd.toFixed(1)},${yBase}L${x0},${yBase}Z"/>`;
}

function drawChart() {
  if ($("overview").hidden) return;
  const t1 = Date.now() / 1000, t0 = t1 - RANGES[chart.range];
  chart.scales = PLOTS.map((plot) => drawPlot(plot, t0, t1));
  hover(chart.index);
}

function fmtTime(seconds) {
  const long = RANGES[chart.range] > 86400;
  return new Date(seconds * 1000).toLocaleString([], long
    ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Show the crosshair, the dots and the tooltip for one sample. null hides them.
// The tooltip stays next to the plot that the pointer is in.
function hover(index, anchor = chart.anchor) {
  const sample = chart.samples[index];
  chart.index = sample ? index : null;
  chart.anchor = anchor;
  const tip = $("tip");
  tip.hidden = !sample;
  tip.replaceChildren();
  if (sample) tip.appendChild(document.createElement("time")).textContent = fmtTime(sample.time);
  for (const { svg, x, y, plot } of chart.scales) {
    const cross = svg.querySelector(".cross"), dots = svg.querySelectorAll(".dot");
    cross.toggleAttribute("hidden", !sample);
    plot.series.forEach(([key, cls, name], i) => {
      const v = sample ? value(sample, key) : null;
      dots[i].toggleAttribute("hidden", v == null);
      if (v == null) return;
      dots[i].setAttribute("cx", x(sample.time));
      dots[i].setAttribute("cy", y(v));
      const row = tip.appendChild(document.createElement("div"));
      row.appendChild(document.createElement("i")).className = `key ${cls}`;
      row.appendChild(document.createElement("b")).textContent = plot.area ? fmtTemp(sample[key], 1) : fmtPct(v);
      row.appendChild(document.createElement("span")).textContent = name;
    });
    if (!sample) continue;
    cross.setAttribute("x1", x(sample.time));
    cross.setAttribute("x2", x(sample.time));
  }
  if (!sample) return;
  const { svg, x } = chart.scales[anchor], box = $("chart");
  const left = svg.getBoundingClientRect().left - box.getBoundingClientRect().left + x(sample.time) + 12;
  tip.style.left = `${Math.max(8, Math.min(box.clientWidth - tip.offsetWidth - 8, left))}px`;
}

// Hover the sample nearest to the pointer, measured in the plot under the pointer.
function point(e) {
  const rects = chart.scales.map(({ svg }) => svg.getBoundingClientRect());
  const anchor = Math.max(0, rects.findIndex((r) => e.clientX <= r.right && e.clientY <= r.bottom));
  const px = e.clientX - rects[anchor].left, { x } = chart.scales[anchor];
  let best = null, dist = Infinity;
  chart.samples.forEach((s, i) => {
    const d = Math.abs(x(s.time) - px);
    if (d < dist) [best, dist] = [i, d];
  });
  hover(best, anchor);
}

function wireChart() {
  const box = $("chart");
  box.addEventListener("pointermove", point);
  box.addEventListener("pointerdown", point);
  box.addEventListener("pointerleave", () => hover(null));
  box.addEventListener("blur", () => hover(null));
  box.addEventListener("keydown", (e) => {
    const last = chart.samples.length - 1, at = chart.index == null ? last : chart.index;
    const next = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: last, Escape: null }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    hover(next == null ? null : Math.max(0, Math.min(last, next)));
  });
  $("range").addEventListener("change", (e) => {
    chart.range = e.target.value;
    chart.index = null;
    scheduleHistory();
  });
  new ResizeObserver(() => requestAnimationFrame(drawChart)).observe(box);
}

// ---- settings --------------------------------------------------------------

// Each control names its config key in data-cfg="section.key".
const controls = [...document.querySelectorAll("[data-cfg]")];
const pending = {}; // timers of changes that the server did not confirm yet

function readControl(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.dataset.type === "bool") return el.value === "true";
  return el.type === "range" || el.type === "number" || el.dataset.type === "int" ? Number(el.value) : el.value;
}

function writeControl(el, v) {
  if (el.type === "checkbox") el.checked = v;
  else if (el.type === "radio") el.checked = el.value === String(v);
  else if (el.value !== String(v)) el.value = v;
}

function showOutput(el) {
  const out = el.parentElement.querySelector("output");
  if (out) out.textContent = out.dataset.fmt === "temp" ? fmtTemp(Number(el.value)) : `${el.value}${out.dataset.suffix || ""}`;
}

function patch(path, v, delay) {
  const [section, key] = path.split(".");
  clearTimeout(pending[path]);
  const id = (pending[path] = setTimeout(async () => {
    let rejected = false;
    try {
      cfg = (await api("PATCH", "/api/v1/config", { [section]: { [key]: v } })).config;
    } catch (e) {
      toast(e.message);
      rejected = true;
    }
    if (pending[path] === id) delete pending[path];
    syncControls(rejected);
  }, delay));
}

// Show the server config in the controls. Without force, a control in use keeps its value.
function syncControls(force) {
  if (!cfg) return;
  for (const el of controls) {
    const [section, key] = el.dataset.cfg.split(".");
    if (force || (el !== document.activeElement && !pending[el.dataset.cfg])) writeControl(el, cfg[section][key]);
    showOutput(el);
  }
  $("rgb-synced").hidden = !cfg.rgb.sync;
  $("rgb-perled").hidden = cfg.rgb.sync;
  const box = $("led-pickers");
  while (box.children.length < cfg.rgb.colors.length) box.appendChild(ledPicker());
  while (box.children.length > cfg.rgb.colors.length) box.lastChild.remove();
  [...box.children].forEach((el, i) => {
    if (force || (el !== document.activeElement && !pending["rgb.colors"])) el.value = cfg.rgb.colors[i];
  });
}

function ledPicker() {
  const el = document.createElement("input");
  el.type = "color";
  el.setAttribute("aria-label", "LED color");
  el.addEventListener("input", () => patch("rgb.colors", [...$("led-pickers").children].map((c) => c.value), 200));
  return el;
}

function wireControls() {
  for (const el of controls) {
    const slow = el.type === "range" || el.type === "color";
    el.addEventListener(slow ? "input" : "change", () => {
      showOutput(el);
      if (el.type === "number" && !el.checkValidity()) return toast(el.validationMessage);
      patch(el.dataset.cfg, readControl(el), slow ? 200 : 0);
    });
  }
}

// ---- views and theme -------------------------------------------------------

function showView() {
  const name = location.hash === "#settings" ? "settings" : "overview";
  for (const view of document.querySelectorAll(".view")) view.hidden = view.id !== name;
  for (const link of document.querySelectorAll("[data-view]")) {
    link.toggleAttribute("aria-current", link.dataset.view === name);
  }
  drawChart();
}

function toggleTheme() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try {
    localStorage.setItem("pironman5-theme", root.dataset.theme);
  } catch (e) { /* private mode: the choice lasts for this page only */ }
}

addEventListener("hashchange", showView);
$("theme-toggle").addEventListener("click", toggleTheme);
wireControls();
wireChart();
showView();
seedTrend().then(connect);
scheduleHistory();
requestAnimationFrame(animateLeds);
