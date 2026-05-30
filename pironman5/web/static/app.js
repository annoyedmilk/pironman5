"use strict";

const $ = (id) => document.getElementById(id);
const GB = 1024 ** 3;
let CONFIG = null;   // last known config, kept in sync with the server
let HISTORY = [];    // last loaded history samples, for chart hover

// History series shown on the chart and in the hover tooltip.
const SERIES = [
  { key: "cpu_percent", max: 100, color: "var(--accent)", label: "CPU", suffix: "%" },
  { key: "cpu_temperature", max: 90, color: "var(--orange)", label: "Temp", suffix: "°C" },
  { key: "memory_percent", max: 100, color: "var(--purple)", label: "Memory", suffix: "%" },
];

// ---- theme ----------------------------------------------------------------

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  $("theme-toggle").textContent = theme === "dark" ? "☀" : "☾";
  localStorage.setItem("pironman5-theme", theme);
}

function initTheme() {
  const saved = localStorage.getItem("pironman5-theme");
  const prefers = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  applyTheme(saved || prefers);
  $("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
  });
}

// ---- helpers --------------------------------------------------------------

function fmtRate(n) {
  n = n || 0;
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtShort(n) {
  n = n || 0;
  const units = ["B", "K", "M", "G"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)}${units[i]}`;
}

function fmtUptime(s) {
  s = Math.floor(s);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function setRing(id, percent, text) {
  const el = $(id);
  el.style.setProperty("--p", Math.max(0, Math.min(100, percent || 0)));
  el.textContent = text;
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}

// Debounced config PATCH. Also mirrors the change into the local CONFIG.
const patchTimers = {};
function patchConfig(section, values, immediate = false) {
  if (CONFIG && CONFIG[section]) Object.assign(CONFIG[section], values);
  const key = section + ":" + Object.keys(values).join(",");
  clearTimeout(patchTimers[key]);
  const send = () =>
    api("PATCH", "/api/v1/config", { [section]: values }).catch((e) => toast("error: " + e.message));
  if (immediate) send();
  else patchTimers[key] = setTimeout(send, 200);
}

// ---- live frame -----------------------------------------------------------

function renderFrame(f) {
  // CPU
  setRing("cpu-ring", f.cpu_percent, `${Math.round(f.cpu_percent || 0)}%`);
  $("cpu-freq").textContent = f.cpu_freq ? (f.cpu_freq / 1000).toFixed(1) : "0";
  $("cpu-cores").textContent = f.cpu_count ?? 0;
  const load1 = f.load_avg ? f.load_avg[0].toFixed(2) : "—";
  const volt = f.cpu_voltage != null ? `  ${f.cpu_voltage.toFixed(2)}V` : "";
  $("cpu-sub").textContent = `load ${load1}${volt}`;
  renderCores(f.cpu_per_core || []);

  // Temperature
  const t = f.cpu_temperature;
  setRing("temp-ring", t ? (t / 90) * 100 : 0, t != null ? `${Math.round(t)}°` : "—");
  $("cpu-temp").textContent = t != null ? t.toFixed(1) : "—";
  $("temp-sub").textContent = f.cpu_fan_rpm != null ? `tower fan ${f.cpu_fan_rpm} rpm` : "tower fan —";
  $("gpu-sub").textContent = f.gpu_temperature != null ? `GPU ${Math.round(f.gpu_temperature)}°C` : "GPU —";

  // Memory
  setRing("mem-ring", f.memory_percent, `${Math.round(f.memory_percent || 0)}%`);
  $("mem-used").textContent = ((f.memory_used || 0) / GB).toFixed(1);
  $("mem-total").textContent = ((f.memory_total || 0) / GB).toFixed(1);
  $("swap-sub").textContent = f.swap_total
    ? `swap ${(f.swap_used / GB).toFixed(1)} / ${(f.swap_total / GB).toFixed(1)} GB`
    : "swap —";

  // Storage
  setRing("disk-ring", f.disk_percent, `${Math.round(f.disk_percent || 0)}%`);
  $("disk-used").textContent = Math.round((f.disk_used || 0) / GB);
  $("disk-total").textContent = Math.round((f.disk_total || 0) / GB);
  $("nvme-sub").textContent = f.nvme_temperature != null ? `NVMe ${f.nvme_temperature}°C` : "NVMe —";
  $("diskio-sub").textContent = f.disk_read != null
    ? `R ${fmtShort(f.disk_read)} W ${fmtShort(f.disk_write)}`
    : "IO —";

  // Power & health
  const w = f.power_watts;
  setRing("power-ring", w != null ? (w / 15) * 100 : 0, w != null ? `${w.toFixed(1)}W` : "—");
  $("input-v").textContent = f.input_voltage != null ? f.input_voltage.toFixed(2) : "—";
  $("batt-v").textContent = f.battery_voltage != null ? f.battery_voltage.toFixed(2) : "—";
  renderHealth(f.throttle);

  // Network
  const ips = f.ips || {};
  $("net-ip").textContent = Object.values(ips)[0] || "—";
  $("net-mac").textContent = Object.values(f.macs || {})[0] || "—";
  $("net-down").textContent = fmtRate(f.net_download);
  $("net-up").textContent = fmtRate(f.net_upload);
  $("net-link").textContent = f.link_speed != null ? `${f.link_speed} Mbps` : "—";

  // System
  $("sys-model").textContent = f.model || "—";
  $("sys-kernel").textContent = f.kernel || "—";
  $("sys-uptime").textContent = f.uptime != null ? fmtUptime(f.uptime) : "—";
  $("sys-procs").textContent = f.processes ?? "—";
  $("sys-load").textContent = f.load_avg ? f.load_avg.join("  ") : "—";

  // Case fans are on/off (no tach), so reflect the commanded state.
  const running = !!f.case_fan_on;
  const badge = $("fan-badge");
  badge.textContent = running ? "ON" : "OFF";
  badge.classList.toggle("on", running);

  if (f.mock) $("mock-tag").hidden = false;
  renderLeds(f.rgb_leds || []);
  renderOled(f.oled_preview || {});
  if (f.config) syncControls(f.config);
}

function renderCores(cores) {
  const box = $("cores");
  if (box.children.length !== cores.length) {
    box.innerHTML = "";
    cores.forEach((_, i) => {
      const row = document.createElement("div");
      row.className = "core";
      row.innerHTML =
        `<span class="lbl">${i}</span><div class="core-track"><div class="core-fill"></div></div><span class="pct"></span>`;
      box.appendChild(row);
    });
  }
  cores.forEach((v, i) => {
    const row = box.children[i];
    row.querySelector(".core-fill").style.width = `${Math.round(v)}%`;
    row.querySelector(".pct").textContent = `${Math.round(v)}%`;
  });
}

function renderHealth(throttle) {
  document.querySelectorAll("#health .hchip").forEach((chip) => {
    chip.classList.remove("ok", "now", "past");
    if (throttle && throttle[chip.dataset.k]) chip.classList.add(throttle[chip.dataset.k]);
  });
}

function renderLeds(leds) {
  const box = $("leds");
  box.innerHTML = "";
  leds.forEach((c) => {
    const rgb = `rgb(${c[0]},${c[1]},${c[2]})`;
    const d = document.createElement("div");
    d.className = "led";
    d.style.background = rgb;
    d.style.boxShadow = `0 0 12px ${rgb}`;
    box.appendChild(d);
  });
}

function renderOled(preview) {
  const box = $("oled-preview");
  const asleep = preview.awake === false;
  box.classList.toggle("asleep", asleep);
  box.innerHTML = "";
  const lines = asleep ? ["", "   asleep"] : preview.lines || [];
  lines.forEach((line) => {
    const d = document.createElement("div");
    d.className = "line";
    d.textContent = line || " ";
    box.appendChild(d);
  });
}

// ---- websocket ------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/v1/stream`);
  ws.onopen = () => { $("conn-dot").classList.add("live"); $("conn-text").textContent = "service active"; };
  ws.onmessage = (ev) => renderFrame(JSON.parse(ev.data));
  ws.onclose = () => {
    $("conn-dot").classList.remove("live");
    $("conn-text").textContent = "reconnecting";
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
}

// ---- history chart --------------------------------------------------------

function chartY(v, max) {
  // Matches the px mapping used by the hover markers (viewBox is 0..130).
  return 130 - Math.max(0, Math.min(1, v / max)) * 124 - 3;
}

function polyline(samples, key, max, color) {
  const pts = samples
    .map((s, i) => {
      const v = s[key];
      if (v == null) return null;
      const x = (i / Math.max(1, samples.length - 1)) * 600;
      return `${x.toFixed(1)},${chartY(v, max).toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  return `<polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/>`;
}

async function loadHistory() {
  try {
    const data = await api("GET", `/api/v1/history?range=${$("range").value}`);
    HISTORY = data.samples;
    $("chart").innerHTML = HISTORY.length
      ? SERIES.map((s) => polyline(HISTORY, s.key, s.max, s.color)).join("")
      : "";
  } catch (e) { /* history disabled or empty */ }
}

// Hover: vertical guide, per-series dots, and a value tooltip.
const dots = [];
function setupChartHover() {
  const wrap = $("chart-wrap"), cursor = $("chart-cursor"), tip = $("chart-tip");
  SERIES.forEach((s) => {
    const d = document.createElement("div");
    d.className = "chart-dot";
    d.style.background = s.color;
    d.hidden = true;
    wrap.appendChild(d);
    dots.push(d);
  });

  function hide() {
    cursor.hidden = tip.hidden = true;
    dots.forEach((d) => (d.hidden = true));
  }

  wrap.addEventListener("mousemove", (e) => {
    if (!HISTORY.length) return;
    const rect = wrap.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const idx = Math.round(ratio * (HISTORY.length - 1));
    const s = HISTORY[idx];
    const px = (idx / Math.max(1, HISTORY.length - 1)) * rect.width;

    cursor.style.left = `${px}px`;
    cursor.hidden = false;

    const rows = SERIES.map((ser, i) => {
      const v = s[ser.key];
      const dot = dots[i];
      if (v == null) { dot.hidden = true; return ""; }
      dot.hidden = false;
      dot.style.left = `${px}px`;
      dot.style.top = `${(chartY(v, ser.max) / 130) * rect.height}px`;
      const shown = ser.suffix === "%" ? Math.round(v) : v.toFixed(1);
      return `<div class="r"><span><i style="background:${ser.color}"></i>${ser.label}</span><span>${shown}${ser.suffix}</span></div>`;
    }).join("");

    const time = new Date(s.time * 1000).toLocaleTimeString();
    tip.innerHTML = `<div class="t">${time}</div>${rows}`;
    tip.style.left = `${Math.max(54, Math.min(rect.width - 54, px))}px`;
    tip.hidden = false;
  });
  wrap.addEventListener("mouseleave", hide);
}

// ---- config controls ------------------------------------------------------

function bindRange(id, section, key) {
  const el = $(id), val = $(id + "-val");
  el.addEventListener("input", () => {
    if (val) val.textContent = el.value;
    patchConfig(section, { [key]: Number(el.value) });
  });
}

function bindToggle(id, section, key, after) {
  $(id).addEventListener("change", (e) => {
    patchConfig(section, { [key]: e.target.checked }, true);
    if (after) after(e.target.checked);
  });
}

function bindSelect(id, section, key, numeric) {
  $(id).addEventListener("change", (e) =>
    patchConfig(section, { [key]: numeric ? Number(e.target.value) : e.target.value }, true));
}

// Reflect the live config into the controls without disturbing the one the
// user is actively editing, so the dashboard always represents the hardware.
function setIfIdle(el, value) {
  if (!el || document.activeElement === el) return;
  if (el.type === "checkbox") {
    if (el.checked !== value) el.checked = value;
  } else if (String(el.value) !== String(value)) {
    el.value = value;
  }
}

function syncRange(id, value) {
  const el = $(id);
  if (document.activeElement === el) return;
  if (String(el.value) !== String(value)) el.value = value;
  const v = $(id + "-val");
  if (v) v.textContent = value;
}

function syncControls(cfg) {
  CONFIG = cfg;
  setIfIdle($("rgb-enable"), cfg.rgb.enable);
  setIfIdle($("rgb-sync"), cfg.rgb.sync);
  setIfIdle($("rgb-color"), cfg.rgb.color);
  setIfIdle($("rgb-style"), cfg.rgb.style);
  syncRange("rgb-speed", cfg.rgb.speed);
  syncRange("rgb-brightness", cfg.rgb.brightness);
  updateRgbMode(cfg.rgb.sync);
  syncLedPickers(cfg.rgb.colors);
  setIfIdle($("fan-mode"), cfg.fan.mode);
  syncRange("fan-on-temp", cfg.fan.on_temp);
  setIfIdle($("oled-enable"), cfg.oled.enable);
  setIfIdle($("oled-rotation"), String(cfg.oled.rotation));
  syncRange("oled-sleep", cfg.oled.sleep_timeout);
}

function syncLedPickers(colors) {
  const inputs = $("led-pickers").querySelectorAll("input[type=color]");
  if (inputs.length !== colors.length) { buildLedPickers(colors); return; }
  inputs.forEach((inp, i) => {
    if (document.activeElement !== inp && inp.value !== colors[i]) inp.value = colors[i];
  });
}

function updateRgbMode(sync) {
  $("rgb-synced").hidden = !sync;
  $("rgb-perled").hidden = sync;
}

function buildLedPickers(colors) {
  const box = $("led-pickers");
  box.innerHTML = "";
  colors.forEach((hex, i) => {
    const wrap = document.createElement("div");
    wrap.className = "led-pick";
    const input = document.createElement("input");
    input.type = "color";
    input.value = hex;
    input.addEventListener("input", () => {
      const next = [...CONFIG.rgb.colors];
      next[i] = input.value;
      patchConfig("rgb", { colors: next, sync: false });
    });
    const label = document.createElement("span");
    label.textContent = i + 1;
    wrap.append(input, label);
    box.appendChild(wrap);
  });
}

function wireControls() {
  bindToggle("rgb-enable", "rgb", "enable");
  bindToggle("rgb-sync", "rgb", "sync", (sync) => updateRgbMode(sync));
  $("rgb-color").addEventListener("input", (e) => patchConfig("rgb", { color: e.target.value }));
  bindSelect("rgb-style", "rgb", "style");
  bindRange("rgb-speed", "rgb", "speed");
  bindRange("rgb-brightness", "rgb", "brightness");

  bindSelect("fan-mode", "fan", "mode");
  bindRange("fan-on-temp", "fan", "on_temp");

  bindToggle("oled-enable", "oled", "enable");
  bindSelect("oled-rotation", "oled", "rotation", true);
  bindRange("oled-sleep", "oled", "sleep_timeout");

  $("range").addEventListener("change", loadHistory);
}

// ---- boot -----------------------------------------------------------------

async function main() {
  initTheme();
  wireControls();
  setupChartHover();
  try {
    syncControls(await api("GET", "/api/v1/config"));
  } catch (e) {
    toast("failed to load config: " + e.message);
  }
  connect();
  loadHistory();
  setInterval(loadHistory, 15000);
}

main();
