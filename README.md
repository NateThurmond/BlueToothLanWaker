# WoL Waker

**Bluetooth-triggered Wake-on-LAN for your Raspberry Pi.**  
Power on any PC on your network the moment a paired controller wakes up — no extra dongles, no per-device hardware. One Pi handles everything.

> **How it works:** The Pi passively scans for Bluetooth advertisements (BLE). When a PS5 DualSense, Xbox Series controller, or any other BLE device you've configured becomes active, the Pi immediately sends a Wake-on-LAN magic packet to every PC mapped to that controller.

---

## Screenshot

> _Screenshot placeholder — run the app and add one here._  
> `docs/screenshot.png`

---

## Features

- **Automatic BT detection** — BLE scanning via [bleak](https://github.com/hbldh/bleak); no pairing to the Pi required for BLE devices (PS5, Xbox Series X/S)
- **Classic Bluetooth support** — optional `hcitool scan` for older controllers (PS4, Xbox One)
- **Multi-PC / multi-controller** — any controller can wake any number of PCs; any PC can be woken by any number of controllers
- **Active / inactive device view** — see live devices with a countdown timer; full history of every device ever seen
- **Custom device names** — rename "Unknown Device" to "Blue PS5 Controller"
- **Network discovery** — scan your LAN to auto-fill PC IP and MAC addresses
- **Manual WoL test** — send a test magic packet from the UI at any time
- **Real-time UI** — updates via WebSocket (Socket.IO); no page refresh needed
- **Mobile-first PWA** — dark theme, bottom nav, pinnable to your phone's homescreen
- **SQLite storage** — zero external database; everything lives in `wol_waker.db`
- **systemd service** — stays running after reboot

---

## Requirements

### Hardware
| Item | Notes |
|------|-------|
| Raspberry Pi 3, 4, or 5 | Pi 3B+ recommended (has built-in BT) |
| Raspberry Pi 2 | Works with a USB Bluetooth 4.0+ dongle |
| Target PC(s) | Must have WoL enabled in BIOS and be on the same LAN |

> **Pi 2 note:** Any USB BT 4.0 dongle (e.g. TP-Link UB400) will work. Plug it in before starting the service.

### Software (Pi)
- Raspberry Pi OS Lite (Bookworm / Bullseye) or any Debian-based distro
- Python 3.9+
- BlueZ (`bluetooth` + `bluez`)

### Target PC — enabling Wake-on-LAN
1. Enter BIOS/UEFI → find **Wake on LAN** (often under Power Management) → **Enable**
2. Note your PC's **MAC address** (shown in your router's DHCP table, or run `ip link` / `getmac` on the PC)

---

## Installation (Raspberry Pi)

```bash
# 1. Install system dependencies
sudo apt-get update
sudo apt-get install -y bluetooth bluez git python3 python3-pip python3-venv

# 2. (Optional but recommended) Allow arp-scan without sudo for better network discovery
sudo apt-get install -y arp-scan
sudo chmod +s /usr/sbin/arp-scan

# 3. Clone the repo
cd ~
git clone https://github.com/YOUR_USERNAME/BlueToothLanWaker.git
cd BlueToothLanWaker

# 4. Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Add the pi user to the bluetooth group (allows bleak to access the BT adapter)
sudo usermod -a -G bluetooth pi

# 6. Test run (Ctrl+C to stop)
python app.py
```

The web UI will be available at `http://<pi-ip>:8080` from any device on your network.

---

## Running as a systemd Service

```bash
# Copy the service file
sudo cp wol-waker.service /etc/systemd/system/

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable wol-waker
sudo systemctl start wol-waker

# Check status
sudo systemctl status wol-waker

# View live logs
sudo journalctl -u wol-waker -f
```

The service will automatically restart on failure and start on boot.

---

## Local Development (macOS / Linux)

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/BlueToothLanWaker.git
cd BlueToothLanWaker

# Install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python app.py
```

Open `http://localhost:8080` in your browser.

> **macOS note:** On macOS 12+, bleak returns CoreBluetooth UUIDs instead of real MAC addresses. The UI and all features work normally for testing, but BT devices will show UUID strings rather than `AA:BB:CC:DD:EE:FF` addresses. On the Pi, real MAC addresses are used.

> **macOS Bluetooth permission:** The first time you run, macOS will prompt to allow Bluetooth access. Accept the prompt, or grant access via System Settings → Privacy & Security → Bluetooth.

---

## Configuration

Environment variables (set in your shell or in the systemd service's `[Service]` block):

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Web UI port |
| `INACTIVE_TIMEOUT` | `60` | Seconds after last detection before a device is marked inactive and WoL can re-trigger |
| `SCAN_INTERVAL` | `10` | Seconds between BLE scan cycles |
| `DB_PATH` | `wol_waker.db` (next to `app.py`) | Path to the SQLite database file |
| `SECRET_KEY` | `wol-waker-dev-key` | Flask secret key (change for production) |

**Example systemd override:**
```ini
# /etc/systemd/system/wol-waker.service.d/override.conf
[Service]
Environment="INACTIVE_TIMEOUT=90"
Environment="PORT=8080"
```

---

## Pairing Controllers to the Pi (Required for Reliable Detection)

When a controller is paired to a console (PS5, Xbox), pressing its button sends a *directed* advertisement aimed only at that console — a passive BLE scanner can't see it. To guarantee the Pi catches the signal:

**How it works after pairing:**
1. You press the PS button
2. Controller connects to the Pi (Pi is in its paired list)
3. Pi detects the connection, fires WoL to your PC → **immediately disconnects**
4. Controller retries its next paired host (PS5/console), which is now waking up
5. By the time the console is on (~10–20 s), the controller reconnects normally

**One-time pairing setup per controller:**

```bash
sudo bluetoothctl
[bluetooth]# power on
[bluetooth]# agent on
[bluetooth]# default-agent
[bluetooth]# scan on
# Put controller in pairing mode (hold Create + PS until rapid double-flash)
# Wait for it to appear, e.g. [NEW] Device AA:BB:CC:DD:EE:FF DualSense Wireless Controller
[bluetooth]# pair AA:BB:CC:DD:EE:FF
[bluetooth]# trust AA:BB:CC:DD:EE:FF
[bluetooth]# scan off
[bluetooth]# quit
```

Then install the D-Bus Python binding so WoL Waker can monitor connections:

```bash
sudo apt install -y libdbus-1-dev python3-dev
source venv/bin/activate
pip install dbus-python
sudo systemctl restart wol-waker
```

> **Note:** `trust` is what matters — it tells BlueZ to auto-accept reconnects without a prompt. You do NOT need to manually connect; the Pi will accept the controller's incoming connection automatically.

---



### 1 — Add your PC(s)
1. Open the app → **Computers** tab → **Add PC**
2. Enter a name (e.g. "Gaming PC")
3. Enter the PC's **MAC address** (required for WoL)
4. Optionally enter the IP address, or tap **Scan network** to discover devices automatically
5. Tap **Save**

### 2 — Register your controller(s)
Turn on your controller. The Pi will detect it within one scan cycle (~10 s) and it will appear in the **Bluetooth Devices** tab.  

- PS5 DualSense: press the PS button
- Xbox Series X/S: press the Xbox button
- Older controllers: put them in pairing mode (hold the pairing button)

Once seen, the device appears in **Active** with a countdown timer.

### 3 — Assign controller → PC
1. In the **Computers** tab, tap **Assign** on a PC card
2. Tap the controller(s) you want to wake this PC — a checkmark confirms each assignment
3. Tap **Done**

The same controller can be assigned to multiple PCs, and a PC can have multiple controllers.

### 4 — Test it
Tap **Test WoL** on any PC card to send a manual magic packet without waiting for a controller.

### 5 — Pin to homescreen (iOS/Android)
1. Open `http://<pi-ip>:8080` in Safari (iOS) or Chrome (Android)
2. iOS: Share → **Add to Home Screen**
3. Android: Menu → **Add to Home screen** / **Install app**

---

## Troubleshooting

**Controller not detected**  
- Confirm the Pi's Bluetooth adapter is up: `hciconfig`
- Check the app logs: `sudo journalctl -u wol-waker -f`
- On Pi 3, ensure BT is not disabled in `raspi-config`

**WoL packet sent but PC doesn't wake**  
- Confirm WoL is enabled in the PC's BIOS
- On Windows, check Device Manager → Network Adapter → Power Management → "Allow this device to wake the computer"
- The PC must be on the same subnet as the Pi, or you need to configure directed broadcast
- Some PCs only respond to WoL from a completely powered-off state (S5), not sleep (S3) — check BIOS settings

**"bleak not installed" warning**  
Run `pip install bleak` inside the virtual environment.

**arp-scan permission error**  
Run `sudo chmod +s /usr/sbin/arp-scan` or just use manual entry — arp-scan is optional.

**Port 8080 in use**  
Set `PORT=9090` (or any free port) before running.  
To check what's using a port on macOS: `lsof -i -P -n | grep LISTEN`  
On Linux: `ss -tlnp`

---

## Adding PWA Icons

For full homescreen icon support, add two PNG files:

```
static/icons/icon-192.png   (192×192)
static/icons/icon-512.png   (512×512)
```

You can generate them from any image using [Favicon.io](https://favicon.io) or [RealFaviconGenerator](https://realfavicongenerator.net).

---

## License

MIT
