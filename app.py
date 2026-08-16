import threading
import time
import logging
import os

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

import database as db
from bluetooth_scanner import scan_bluetooth
from wol import send_wol
from network_scanner import scan_network

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "wol-waker-dev-key")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# How long (seconds) a device stays "active" after last detection
INACTIVE_TIMEOUT = int(os.environ.get("INACTIVE_TIMEOUT", "60"))
# How often (seconds) to run a BLE scan cycle
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "10"))

# In-memory active device state  { mac: {"last_seen": float, "name": str} }
_active_devices: dict = {}
# BT MACs that already triggered WoL this session (reset when device goes inactive)
_woken_this_session: set = set()
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _device_display_name(discovered: str | None, custom: str | None, mac: str) -> str:
    return custom or discovered or mac


def _get_device_status() -> list:
    """Return all known BT devices, enriched with live active-state info."""
    all_devices = db.get_all_bt_devices()
    now = time.time()
    result = []
    with _state_lock:
        active_snap = dict(_active_devices)
    for d in all_devices:
        mac = d["mac_address"]
        info = active_snap.get(mac)
        is_active = info is not None
        time_until_inactive = (
            max(0.0, INACTIVE_TIMEOUT - (now - info["last_seen"])) if is_active else 0.0
        )
        result.append(
            {
                **d,
                "is_active": is_active,
                "time_until_inactive": round(time_until_inactive),
                "inactive_timeout": INACTIVE_TIMEOUT,
            }
        )
    return result


def _trigger_wol_for_device(bt_mac: str) -> None:
    """Fire WoL magic packets for every PC mapped to this BT device."""
    pcs = db.get_pcs_for_bt_device(bt_mac)
    for pc in pcs:
        try:
            send_wol(pc["mac_address"])
            logger.info("WoL → %s (%s) triggered by %s", pc["name"], pc["mac_address"], bt_mac)
            socketio.emit(
                "wol_sent",
                {
                    "pc_name": pc["name"],
                    "pc_id": pc["id"],
                    "bt_mac": bt_mac,
                    "timestamp": time.time(),
                },
            )
        except Exception as exc:
            logger.error("WoL failed for %s: %s", pc["name"], exc)


# ---------------------------------------------------------------------------
# Background scan loop
# ---------------------------------------------------------------------------

def _bluetooth_scan_loop() -> None:
    logger.info("Bluetooth scan loop started (interval=%ds, timeout=%ds)", SCAN_INTERVAL, INACTIVE_TIMEOUT)
    while True:
        try:
            devices = scan_bluetooth()
            now = time.time()

            newly_active: list[str] = []
            newly_inactive: list[str] = []

            with _state_lock:
                for device in devices:
                    mac = device["mac"]
                    name = device.get("name") or ""
                    device_type = device.get("device_type") or None
                    db.upsert_bt_device(mac, name, device_type)

                    if mac not in _active_devices:
                        newly_active.append(mac)
                        if mac not in _woken_this_session:
                            _woken_this_session.add(mac)
                            # Fire WoL outside lock
                            threading.Thread(
                                target=_trigger_wol_for_device,
                                args=(mac,),
                                daemon=True,
                            ).start()

                    _active_devices[mac] = {"last_seen": now, "name": name}

                for mac, info in list(_active_devices.items()):
                    if now - info["last_seen"] > INACTIVE_TIMEOUT:
                        newly_inactive.append(mac)
                        del _active_devices[mac]
                        _woken_this_session.discard(mac)

            # Emit state events
            for mac in newly_active:
                socketio.emit("device_active", {"mac": mac})
            for mac in newly_inactive:
                socketio.emit("device_inactive", {"mac": mac})

            socketio.emit("devices_update", _get_device_status())

        except Exception as exc:
            logger.error("Scan loop error: %s", exc)

        time.sleep(SCAN_INTERVAL)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# --- PCs ---

@app.route("/api/pcs", methods=["GET"])
def api_get_pcs():
    pcs = db.get_all_pcs()
    for pc in pcs:
        pc["bt_devices"] = db.get_mappings_for_pc(pc["id"])
    return jsonify(pcs)


@app.route("/api/pcs", methods=["POST"])
def api_add_pc():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    mac = (data.get("mac_address") or "").strip()
    ip = (data.get("ip_address") or "").strip() or None
    if not name or not mac:
        return jsonify({"error": "name and mac_address are required"}), 400
    pc_id = db.add_pc(name, mac, ip)
    return jsonify({"id": pc_id}), 201


@app.route("/api/pcs/<int:pc_id>", methods=["PUT"])
def api_update_pc(pc_id):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    mac = (data.get("mac_address") or "").strip()
    ip = (data.get("ip_address") or "").strip() or None
    if not name or not mac:
        return jsonify({"error": "name and mac_address are required"}), 400
    db.update_pc(pc_id, name, mac, ip)
    return jsonify({"ok": True})


@app.route("/api/pcs/<int:pc_id>", methods=["DELETE"])
def api_delete_pc(pc_id):
    db.delete_pc(pc_id)
    return jsonify({"ok": True})


@app.route("/api/pcs/<int:pc_id>/wol", methods=["POST"])
def api_test_wol(pc_id):
    pcs = db.get_all_pcs()
    pc = next((p for p in pcs if p["id"] == pc_id), None)
    if not pc:
        return jsonify({"error": "PC not found"}), 404
    try:
        send_wol(pc["mac_address"])
        socketio.emit(
            "wol_sent",
            {"pc_name": pc["name"], "pc_id": pc["id"], "bt_mac": "manual", "timestamp": time.time()},
        )
        return jsonify({"ok": True, "message": f"WoL sent to {pc['name']}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# --- BT Devices ---

@app.route("/api/bt-devices", methods=["GET"])
def api_get_bt_devices():
    return jsonify(_get_device_status())


@app.route("/api/bt-devices", methods=["POST"])
def api_add_bt_device():
    data = request.get_json(silent=True) or {}
    mac  = (data.get("mac_address") or "").strip()
    name = (data.get("discovered_name") or "").strip() or None
    device_type = (data.get("device_type") or "").strip() or None
    if not mac:
        return jsonify({"error": "mac_address is required"}), 400
    device_id = db.upsert_bt_device(mac, name, device_type)
    socketio.emit("devices_update", _get_device_status())
    return jsonify({"id": device_id}), 201


@app.route("/api/bt-devices/<int:device_id>", methods=["PUT"])
def api_update_bt_device(device_id):
    data = request.get_json(silent=True) or {}
    custom_name = (data.get("custom_name") or "").strip() or None
    db.update_bt_device_custom_name(device_id, custom_name)
    return jsonify({"ok": True})


@app.route("/api/bt-devices/<int:device_id>", methods=["DELETE"])
def api_delete_bt_device(device_id):
    db.delete_bt_device(device_id)
    socketio.emit("devices_update", _get_device_status())
    return jsonify({"ok": True})


# --- Mappings ---

@app.route("/api/mappings", methods=["POST"])
def api_add_mapping():
    data = request.get_json(silent=True) or {}
    pc_id = data.get("pc_id")
    bt_id = data.get("bt_device_id")
    if not pc_id or not bt_id:
        return jsonify({"error": "pc_id and bt_device_id required"}), 400
    db.add_mapping(pc_id, bt_id)
    return jsonify({"ok": True}), 201


@app.route("/api/mappings", methods=["DELETE"])
def api_remove_mapping():
    data = request.get_json(silent=True) or {}
    pc_id = data.get("pc_id")
    bt_id = data.get("bt_device_id")
    if not pc_id or not bt_id:
        return jsonify({"error": "pc_id and bt_device_id required"}), 400
    db.remove_mapping(pc_id, bt_id)
    return jsonify({"ok": True})


# --- Network scan ---

@app.route("/api/network-scan", methods=["POST"])
def api_network_scan():
    hosts = scan_network()
    return jsonify(hosts)


# --- Status ---

@app.route("/api/status", methods=["GET"])
def api_status():
    with _state_lock:
        active_count = len(_active_devices)
    return jsonify({"active_bt_devices": active_count, "inactive_timeout": INACTIVE_TIMEOUT})


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    emit("devices_update", _get_device_status())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init_db()
    scan_thread = threading.Thread(target=_bluetooth_scan_loop, daemon=True)
    scan_thread.start()
    port = int(os.environ.get("PORT", "8081"))
    logger.info("Starting WoL Waker on http://0.0.0.0:%d", port)
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
