/* global io */
"use strict";

// ═══════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════
const state = {
  pcs: [],
  btDevices: [],
  wolLog: [],
  activeTab: "pcs",
  // client-side countdown  { mac: remainingSeconds }
  timers: {},
  // inactive_timeout from server (fallback 60s)
  inactiveTimeout: 60,
};

// ═══════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  setupModals();
  setupSocket();
  loadPCs();
  loadBTDevices();
  startCountdown();
});

// ═══════════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════════
function setupNavigation() {
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
  document.getElementById(`tab-${tab}`).classList.add("active");
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
}

// ═══════════════════════════════════════════════════════
//  SOCKET.IO
// ═══════════════════════════════════════════════════════
function setupSocket() {
  const socket = io({ transports: ["websocket", "polling"] });

  socket.on("connect", () => updateScanStatus(true));
  socket.on("disconnect", () => updateScanStatus(false));

  socket.on("devices_update", (devices) => {
    state.btDevices = devices;
    state.timers = {};
    for (const d of devices) {
      if (d.is_active) {
        state.timers[d.mac_address] = d.time_until_inactive;
        if (d.inactive_timeout) state.inactiveTimeout = d.inactive_timeout;
      }
    }
    // If current filter no longer exists in device list, reset it
    if (_deviceFilter && !devices.some(d => d.device_type === _deviceFilter)) {
      _deviceFilter = null;
    }
    renderBTDevices();
  });

  socket.on("wol_sent", (data) => {
    addLogEntry({
      type: "wol",
      primary: `WoL → ${data.pc_name}`,
      secondary: data.bt_mac === "manual"
        ? "Triggered manually"
        : `Via device ${data.bt_mac}`,
      ts: data.timestamp,
    });
    showToast(`⚡ WoL sent to ${data.pc_name}`, "wol");
  });
}

function updateScanStatus(online) {
  const el = document.getElementById("scan-status");
  const label = el.querySelector(".status-label");
  el.className = `scan-status ${online ? "online" : "offline"}`;
  label.textContent = online ? "Scanning" : "Offline";
}

// ═══════════════════════════════════════════════════════
//  PCs
// ═══════════════════════════════════════════════════════
async function loadPCs() {
  try {
    const res = await fetch("/api/pcs");
    state.pcs = await res.json();
    renderPCs();
  } catch (e) {
    console.error("loadPCs error:", e);
  }
}

function renderPCs() {
  const el = document.getElementById("pcs-list");
  if (!state.pcs.length) {
    el.innerHTML = `<div class="empty-state">
      <i class="bi bi-pc-display"></i>
      <p>No PCs yet.<br>Tap <strong>Add PC</strong> to get started.</p>
    </div>`;
    return;
  }
  el.innerHTML = state.pcs.map(pcCardHTML).join("");
}

function pcCardHTML(pc) {
  const devices = pc.bt_devices || [];
  const badges = devices.length
    ? devices.map((d) => `
      <span class="device-badge" data-pc="${pc.id}" data-btid="${d.bt_id}">
        <i class="bi bi-controller"></i>
        ${esc(d.custom_name || d.discovered_name || d.mac_address)}
        <button class="badge-remove" title="Remove"
          onclick="removeMapping(${pc.id},${d.bt_id})">×</button>
      </span>`).join("")
    : `<span class="no-devices-hint">No controllers assigned</span>`;

  return `
  <div class="pc-card" id="pc-card-${pc.id}">
    <div class="pc-card-header">
      <div class="pc-icon"><i class="bi bi-pc-display"></i></div>
      <div class="pc-info">
        <div class="pc-name">${esc(pc.name)}</div>
        <div class="pc-detail">${esc(pc.ip_address || "")}</div>
        <div class="pc-mac">${esc(pc.mac_address)}</div>
      </div>
      <div class="pc-card-actions">
        <button class="btn-icon" title="Edit" onclick="openEditPC(${pc.id})">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn-icon danger" title="Delete" onclick="deletePC(${pc.id}, '${esc(pc.name)}')">
          <i class="bi bi-trash3"></i>
        </button>
      </div>
    </div>
    <div class="pc-devices">${badges}</div>
    <div class="pc-footer">
      <button class="btn-sm primary" onclick="openAssignDevice(${pc.id})">
        <i class="bi bi-plus-circle"></i> Assign
      </button>
      <button class="btn-sm wol" onclick="testWol(${pc.id}, '${esc(pc.name)}')">
        <i class="bi bi-lightning-charge"></i> Test WoL
      </button>
    </div>
  </div>`;
}

// ── Add / Edit PC modal ──────────────────────────────────

document.getElementById("btn-add-pc").addEventListener("click", openAddPC);

function openAddPC() {
  document.getElementById("modal-pc-title").textContent = "Add Computer";
  document.getElementById("pc-id").value = "";
  document.getElementById("pc-name").value = "";
  document.getElementById("pc-mac").value = "";
  document.getElementById("pc-ip").value = "";
  hideScanResults();
  openModal("modal-pc");
}

function openEditPC(pcId) {
  const pc = state.pcs.find((p) => p.id === pcId);
  if (!pc) return;
  document.getElementById("modal-pc-title").textContent = "Edit Computer";
  document.getElementById("pc-id").value = pc.id;
  document.getElementById("pc-name").value = pc.name;
  document.getElementById("pc-mac").value = pc.mac_address;
  document.getElementById("pc-ip").value = pc.ip_address || "";
  hideScanResults();
  openModal("modal-pc");
}

document.getElementById("form-pc").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id   = document.getElementById("pc-id").value;
  const name = document.getElementById("pc-name").value.trim();
  const mac  = document.getElementById("pc-mac").value.trim();
  const ip   = document.getElementById("pc-ip").value.trim() || null;

  if (!name || !mac) { showToast("Name and MAC address are required", "error"); return; }

  const url    = id ? `/api/pcs/${id}` : "/api/pcs";
  const method = id ? "PUT" : "POST";

  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, mac_address: mac, ip_address: ip }),
    });
    if (!res.ok) { const d = await res.json(); showToast(d.error || "Error saving PC", "error"); return; }
    closeModal("modal-pc");
    await loadPCs();
    showToast(id ? "PC updated" : "PC added", "success");
  } catch (e) {
    showToast("Network error", "error");
  }
});

async function deletePC(pcId, name) {
  if (!confirm(`Delete "${name}"? This will remove all controller assignments.`)) return;
  await fetch(`/api/pcs/${pcId}`, { method: "DELETE" });
  await loadPCs();
  showToast(`${name} removed`, "info");
}

async function testWol(pcId, pcName) {
  const res = await fetch(`/api/pcs/${pcId}/wol`, { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    showToast(`⚡ ${data.message}`, "wol");
  } else {
    showToast(data.error || "WoL failed", "error");
  }
}

// ── Network scan ─────────────────────────────────────────

document.getElementById("btn-scan-network").addEventListener("click", async () => {
  const btn = document.getElementById("btn-scan-network");
  btn.classList.add("scanning");
  btn.innerHTML = `<span class="spinner"></span> Scanning…`;

  try {
    const res   = await fetch("/api/network-scan", { method: "POST" });
    const hosts = await res.json();
    renderScanResults(hosts);
  } catch (e) {
    showToast("Network scan failed", "error");
  } finally {
    btn.classList.remove("scanning");
    btn.innerHTML = `<i class="bi bi-radar"></i> Scan network to discover PCs`;
  }
});

function renderScanResults(hosts) {
  const wrap = document.getElementById("scan-results");
  const list = document.getElementById("scan-results-list");
  if (!hosts.length) {
    list.innerHTML = `<div style="padding:12px 14px;font-size:0.85rem;color:var(--text-muted)">No hosts found. Try manually.</div>`;
    wrap.style.display = "block";
    return;
  }
  list.innerHTML = hosts.map((h) => `
    <div class="scan-result-item"
         onclick="fillPCFromScan('${esc(h.ip)}','${esc(h.mac)}','${esc(h.name)}')">
      <i class="bi bi-hdd-network scan-result-icon"></i>
      <div>
        <div class="scan-result-name">${esc(h.name)}</div>
        <div class="scan-result-detail">${esc(h.ip)} · ${esc(h.mac)}</div>
      </div>
    </div>`).join("");
  wrap.style.display = "block";
}

function fillPCFromScan(ip, mac, name) {
  document.getElementById("pc-ip").value  = ip;
  document.getElementById("pc-mac").value = mac;
  if (!document.getElementById("pc-name").value) {
    document.getElementById("pc-name").value = name;
  }
  hideScanResults();
}

function hideScanResults() {
  document.getElementById("scan-results").style.display = "none";
  document.getElementById("scan-results-list").innerHTML = "";
}

// ── Assign device modal ──────────────────────────────────

let _assigningPcId = null;

function openAssignDevice(pcId) {
  _assigningPcId = pcId;
  const pc = state.pcs.find((p) => p.id === pcId);
  document.getElementById("modal-assign-subtitle").textContent =
    pc ? `Assigning to: ${pc.name}` : "";

  const assignedIds = new Set((pc?.bt_devices || []).map((d) => d.bt_id));
  const list = document.getElementById("assign-device-list");

  if (!state.btDevices.length) {
    list.innerHTML = `<div class="assign-empty">No Bluetooth devices seen yet.<br>
      Controllers need to be powered on so the Pi can detect them first.</div>`;
  } else {
    // Sort: active first, then by last_seen descending
    const sorted = [...state.btDevices].sort((a, b) => {
      if (a.is_active !== b.is_active) return a.is_active ? -1 : 1;
      const ta = a.last_seen || "";
      const tb = b.last_seen || "";
      return tb.localeCompare(ta);
    });
    list.innerHTML = sorted.map((d) => {
      const assigned = assignedIds.has(d.id);
      const displayName = d.custom_name || d.discovered_name || null;
      const nameHtml = displayName
        ? `<div class="assign-item-name">${esc(displayName)}</div>`
        : `<div class="assign-item-name unnamed">Unknown device</div>`;
      const typeLabel = d.device_type
        ? `<div class="assign-item-type">${esc(d.device_type)}</div>`
        : "";
      const seenLabel = d.last_seen
        ? `<div class="assign-item-seen">${timeAgo(d.last_seen)}</div>`
        : "";
      return `
      <div class="assign-item ${assigned ? "assigned" : ""}"
           onclick="toggleAssign(${pcId},${d.id},this)">
        <div class="assign-check"><i class="bi bi-check-lg"></i></div>
        <div class="assign-item-info">
          ${nameHtml}
          ${typeLabel}
          <div class="assign-item-mac">${esc(d.mac_address)}</div>
          ${seenLabel}
        </div>
        ${d.is_active ? `<span class="assign-item-status active">● Active</span>` : ""}
      </div>`;
    }).join("");
  }

  openModal("modal-assign");
}

async function toggleAssign(pcId, btDeviceId, el) {
  const wasAssigned = el.classList.contains("assigned");
  el.classList.toggle("assigned");

  const method = wasAssigned ? "DELETE" : "POST";
  try {
    await fetch("/api/mappings", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pc_id: pcId, bt_device_id: btDeviceId }),
    });
    await loadPCs();
  } catch (e) {
    el.classList.toggle("assigned"); // revert on error
    showToast("Error updating assignment", "error");
  }
}

async function removeMapping(pcId, btDeviceId) {
  await fetch("/api/mappings", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pc_id: pcId, bt_device_id: btDeviceId }),
  });
  await loadPCs();
}

// ═══════════════════════════════════════════════════════
//  BT DEVICES
// ═══════════════════════════════════════════════════════
let _deviceFilter = null; // null = show all

async function loadBTDevices() {
  try {
    const res = await fetch("/api/bt-devices");
    const devices = await res.json();
    state.btDevices = devices;
    state.timers = {};
    for (const d of devices) {
      if (d.is_active) state.timers[d.mac_address] = d.time_until_inactive;
      if (d.inactive_timeout) state.inactiveTimeout = d.inactive_timeout;
    }
    renderBTDevices();
  } catch (e) {
    console.error("loadBTDevices error:", e);
  }
}

function renderDeviceFilters() {
  const container = document.getElementById("device-filters");
  // Collect unique device types (non-null)
  const types = [...new Set(
    state.btDevices.map(d => d.device_type).filter(Boolean)
  )].sort();

  if (!types.length) { container.innerHTML = ""; return; }

  const allBtn = `<button class="filter-pill ${_deviceFilter === null ? "active" : ""}"
    onclick="setDeviceFilter(null)">All</button>`;

  const typeBtns = types.map(t =>
    `<button class="filter-pill ${_deviceFilter === t ? "active" : ""}"
      onclick="setDeviceFilter(${JSON.stringify(t)})">${esc(t)}</button>`
  ).join("");

  container.innerHTML = allBtn + typeBtns;
}

function setDeviceFilter(type) {
  _deviceFilter = type;
  renderBTDevices();
}

function renderBTDevices() {
  renderDeviceFilters();

  const filtered = _deviceFilter
    ? state.btDevices.filter(d => d.device_type === _deviceFilter)
    : state.btDevices;

  const active   = filtered.filter((d) => d.is_active);
  const inactive = filtered.filter((d) => !d.is_active);

  // Active section
  const activeLabel = document.getElementById("active-label");
  activeLabel.textContent = `Active (${active.length})`;

  const activeEl = document.getElementById("active-devices-list");
  activeEl.innerHTML = active.length
    ? active.map(btCardHTML).join("")
    : `<div class="empty-state small">No active devices detected.</div>`;

  // Inactive section
  const inactiveLabel = document.getElementById("inactive-label");
  inactiveLabel.textContent = `Inactive (${inactive.length})`;

  const inactiveEl = document.getElementById("inactive-devices-list");
  inactiveEl.innerHTML = inactive.length
    ? inactive.map((d) => btCardHTML(d, false)).join("")
    : `<div class="empty-state small">No devices in history.</div>`;
}

function btCardHTML(device, active = device.is_active) {
  const name = device.custom_name || device.discovered_name || device.mac_address;
  const remaining = state.timers[device.mac_address] ?? 0;
  const pct = active ? Math.round((remaining / state.inactiveTimeout) * 100) : 0;
  const fillClass = pct > 40 ? "" : pct > 15 ? "warning" : "danger";

  // Device type badge — dim style for broad fallback labels
  const broadLabels = new Set(["Sony Device", "Microsoft Device", "Apple Device", "Game Controller"]);
  const typeBadge = device.device_type
    ? `<span class="device-type-badge ${broadLabels.has(device.device_type) ? "broad" : ""}">${esc(device.device_type)}</span>`
    : "";

  // Icon based on device type
  const iconClass = deviceIcon(device.device_type);

  const timerBar = active ? `
    <div class="timer-wrap">
      <div class="timer-bar-bg">
        <div class="timer-bar-fill ${fillClass}"
             id="timer-fill-${device.mac_address.replace(/:/g, "-")}"
             style="width:${pct}%"></div>
      </div>
      <div class="timer-label" id="timer-label-${device.mac_address.replace(/:/g, "-")}">
        ${remaining}s
      </div>
    </div>` : "";

  const lastSeen = device.last_seen
    ? `<div class="bt-seen">Last seen ${timeAgo(device.last_seen)}</div>`
    : "";

  return `
  <div class="bt-card ${active ? "active-device" : "inactive-device"}">
    <div class="bt-card-top">
      <div class="bt-icon"><i class="bi ${iconClass}"></i></div>
      <div class="bt-info">
        <div class="bt-name">${esc(name)}</div>
        ${typeBadge}
        <div class="bt-mac">${esc(device.mac_address)}</div>
        ${lastSeen}
      </div>
      <div class="bt-card-actions">
        <button class="btn-icon" title="Rename" onclick="openRenameDevice(${device.id},'${esc(name)}')">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn-icon danger" title="Remove from history"
                onclick="deleteDevice(${device.id},'${esc(name)}')">
          <i class="bi bi-trash3"></i>
        </button>
      </div>
    </div>
    ${timerBar}
  </div>`;
}

function deviceIcon(deviceType) {
  if (!deviceType) return "bi-controller";
  const t = deviceType.toLowerCase();
  if (t.includes("ps5") || t.includes("dualsense"))     return "bi-controller";
  if (t.includes("ps4") || t.includes("dualshock"))     return "bi-controller";
  if (t.includes("playstation"))                         return "bi-controller";
  if (t.includes("xbox"))                                return "bi-joystick";
  if (t.includes("nintendo") || t.includes("joy-con"))  return "bi-nintendo-switch";
  return "bi-controller";
}

// ── Countdown ─────────────────────────────────────────────

function startCountdown() {
  setInterval(() => {
    let changed = false;
    for (const mac in state.timers) {
      const old = state.timers[mac];
      state.timers[mac] = Math.max(0, old - 1);
      if (old !== state.timers[mac]) changed = true;
    }
    if (changed) updateTimerDisplays();
  }, 1000);
}

function updateTimerDisplays() {
  for (const [mac, remaining] of Object.entries(state.timers)) {
    const safeMac = mac.replace(/:/g, "-");
    const fillEl  = document.getElementById(`timer-fill-${safeMac}`);
    const labelEl = document.getElementById(`timer-label-${safeMac}`);
    if (!fillEl || !labelEl) continue;

    const pct = Math.round((remaining / state.inactiveTimeout) * 100);
    fillEl.style.width = pct + "%";
    labelEl.textContent = remaining + "s";

    fillEl.className = "timer-bar-fill";
    if (pct <= 15) fillEl.classList.add("danger");
    else if (pct <= 40) fillEl.classList.add("warning");
  }
}

// ── Rename device ─────────────────────────────────────────

function openRenameDevice(deviceId, currentName) {
  document.getElementById("rename-device-id").value = deviceId;
  const device = state.btDevices.find((d) => d.id === deviceId);
  document.getElementById("rename-custom-name").value = device?.custom_name || "";
  document.getElementById("modal-rename-title").textContent = `Rename: ${currentName}`;
  openModal("modal-rename");
}

document.getElementById("form-rename").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id   = parseInt(document.getElementById("rename-device-id").value, 10);
  const name = document.getElementById("rename-custom-name").value.trim() || null;

  try {
    await fetch(`/api/bt-devices/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ custom_name: name }),
    });
    closeModal("modal-rename");
    await loadBTDevices();
    await loadPCs(); // update badges on PC cards
    showToast("Device renamed", "success");
  } catch (e) {
    showToast("Error renaming device", "error");
  }
});

async function deleteDevice(deviceId, name) {
  if (!confirm(`Remove "${name}" from history?`)) return;
  await fetch(`/api/bt-devices/${deviceId}`, { method: "DELETE" });
  await loadBTDevices();
  await loadPCs();
  showToast(`${name} removed`, "info");
}

// ── Inactive toggle ───────────────────────────────────────

document.getElementById("btn-toggle-inactive").addEventListener("click", () => {
  const btn = document.getElementById("btn-toggle-inactive");
  const panel = document.getElementById("inactive-devices-list");
  const open = btn.getAttribute("aria-expanded") === "true";
  btn.setAttribute("aria-expanded", String(!open));
  panel.style.display = open ? "none" : "flex";
  if (!open) panel.style.flexDirection = "column";
});

// ═══════════════════════════════════════════════════════
//  LOG
// ═══════════════════════════════════════════════════════
function addLogEntry({ type, primary, secondary, ts }) {
  const entry = { type, primary, secondary, ts: ts || Date.now() / 1000 };
  state.wolLog.unshift(entry);
  if (state.wolLog.length > 100) state.wolLog.pop();
  renderLog();
}

function renderLog() {
  const el = document.getElementById("log-list");
  if (!state.wolLog.length) {
    el.innerHTML = `<div class="empty-state">
      <i class="bi bi-journal-text"></i>
      <p>WoL events will appear here.</p>
    </div>`;
    return;
  }
  el.innerHTML = state.wolLog.map(logEntryHTML).join("");
}

function logEntryHTML(entry) {
  const d = new Date(entry.ts * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `
  <div class="log-entry">
    <div class="log-icon wol"><i class="bi bi-lightning-charge-fill"></i></div>
    <div class="log-body">
      <div class="log-primary">${esc(entry.primary)}</div>
      <div class="log-secondary">${esc(entry.secondary || "")}</div>
    </div>
    <div class="log-time">${time}</div>
  </div>`;
}

document.getElementById("btn-clear-log").addEventListener("click", () => {
  state.wolLog = [];
  renderLog();
});

// ═══════════════════════════════════════════════════════
//  MODALS
// ═══════════════════════════════════════════════════════
function setupModals() {
  document.getElementById("btn-pc-cancel").addEventListener("click",     () => closeModal("modal-pc"));
  document.getElementById("btn-assign-cancel").addEventListener("click", () => closeModal("modal-assign"));
  document.getElementById("btn-rename-cancel").addEventListener("click", () => closeModal("modal-rename"));

  // Close on overlay click
  ["modal-pc", "modal-assign", "modal-rename"].forEach((id) => {
    document.getElementById(id).addEventListener("click", (e) => {
      if (e.target.id === id) closeModal(id);
    });
  });
}

function openModal(id) {
  document.getElementById(id).classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeModal(id) {
  document.getElementById(id).classList.remove("open");
  document.body.style.overflow = "";
}

// ═══════════════════════════════════════════════════════
//  TOAST
// ═══════════════════════════════════════════════════════
function showToast(msg, type = "info", duration = 3000) {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast-item ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s";
    setTimeout(() => el.remove(), 300);
  }, duration);
}

// ═══════════════════════════════════════════════════════
//  UTILS
// ═══════════════════════════════════════════════════════
function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;")
    .replace(/'/g,  "&#039;");
}

function timeAgo(isoString) {
  const d = new Date(isoString.replace(" ", "T") + "Z");
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (isNaN(sec) || sec < 0)   return "just now";
  if (sec < 60)                return `${sec}s ago`;
  if (sec < 3600)              return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400)             return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}
