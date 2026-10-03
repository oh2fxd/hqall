# hqall — Live Situational Awareness Map

Fullscreen live situational-awareness map for Finland. One Python process serves a JSON API and a static frontend; everything it shows is pulled from public, key-free upstream APIs where one exists.

Fully compatible with **macOS (OSX)** and **Linux**.

---

## 🚀 Quick Start & Installation

### Option 1: Automated Installation Script (Recommended)

Run the included cross-platform installation script to install dependencies, configure environment, open your browser, and prompt for bookmarking:

```bash
git clone https://github.com/your-username/hqall.git
cd hqall
./install.sh
```

The installer will:
1. Detect your Operating System (**Linux** or **macOS**).
2. Install dependencies from `requirements.txt`.
3. Create `.env` from `.env.example` if not present.
4. Setup and enable background service:
   - **Linux**: Enables systemd user service (`hqall.service`).
   - **macOS**: Enables launchd agent (`com.hqall.service.plist`).
5. Open your default web browser to `http://127.0.0.1:8077/?bookmark=1`.
6. Prompt you to bookmark the site using <kbd>Ctrl</kbd>+<kbd>D</kbd> (<kbd>Cmd</kbd>+<kbd>D</kbd> on macOS).

---

### Option 2: Manual Run

```bash
git clone https://github.com/your-username/hqall.git
cd hqall

pip install -r requirements.txt

# Run server directly
python3 app.py
```

Then open <http://127.0.0.1:8077/> in your browser.

---

## ⚙️ Service Management

### Linux (systemd)
```bash
# Check service status
systemctl --user status hqall.service

# Restart service
systemctl --user restart hqall.service

# View live logs
journalctl --user -u hqall.service -f
```

### macOS (launchd)
```bash
# Check loaded status
launchctl list | grep hqall

# Reload service
launchctl unload ~/Library/LaunchAgents/com.hqall.service.plist
launchctl load ~/Library/LaunchAgents/com.hqall.service.plist

# View logs
tail -f /tmp/hqall.log /tmp/hqall.err
```

### Uninstalling the Service
To remove background services and stop any running instance, run:
```bash
./uninstall.sh
```

---

## 🔑 API Keys Guide (Optional)

By default, HQALL runs **100% keyless** for all core layers (Aircraft ADS-B, RainViewer radar, NASA GIBS clouds, Tilannehuone incidents, Fintraffic alerts, Open-Meteo weather, and Country borders). No registration or API keys are required to use HQALL out of the box.

If you wish to enable optional integrations (such as live marine vessel tracking), here is where to get the keys and where to put them:

### Where to get keys:
- **AISHub Vessel Layer (`AIS_API_KEY`)**: Get a free account and username at [https://www.aishub.net/](https://www.aishub.net/) *(Your AISHub username acts as your key)*.
- **aprs.fi API (`APRS_API_KEY`)**: Get a free API key at [https://aprs.fi/page/api](https://aprs.fi/page/api).
- **APRS-IS Live Stream (`APRS_CALLSIGN` / `APRS_PASSCODE`)**: Registered callsign & passcode from [https://aprs.fi](https://aprs.fi).

### Where to put keys (2 Options):

1. **Option A: Web UI Settings Panel (Easiest)**
   - Open HQALL in your browser.
   - Click the **Settings (gear icon)** in the top bar.
   - Scroll down to **API Keys (Optional)**, paste your keys, and click away. They are stored safely in your browser (`localStorage`).

2. **Option B: Server `.env` File**
   - Edit the `.env` file in the project root directory:
     ```env
     AIS_API_KEY=your_aishub_username
     APRS_API_KEY=your_aprsfi_key
     ```
   - Restart the server.

---

## 📌 Environment Variables

The app runs without most credentials, but the following can be set in `.env`:

| Variable | Description | Where to get key / Default |
| --- | --- | --- |
| `HQALL_HOST` | Host address to bind server | `0.0.0.0` |
| `HQALL_PORT` | Port number to listen on | `8077` |
| `HQALL_REPO` | Owner/repo to enable GitHub update checks | `oh2fxd/hqall` |
| `AIS_API_KEY` | AISHub key to enable vessel layer | [aishub.net](https://www.aishub.net/) |
| `APRS_CALLSIGN` / `APRS_PASSCODE` | APRS-IS login information | [aprs.fi](https://aprs.fi) |
| `APRS_API_KEY` | aprs.fi JSON API fallback key | [aprs.fi/page/api](https://aprs.fi/page/api) |
| `HQALL_CONTACT` | Contact string in User-Agent header | `local` |

---

## 🌐 What is on the map

| Layer | Source | Key needed |
| --- | --- | --- |
| Aircraft | [opendata.adsb.fi](https://opendata.adsb.fi/) (fallback api.adsb.lol) | no |
| Radar / precipitation | [RainViewer](https://www.rainviewer.com/api.html) tiles | no |
| Cloud cover | NASA GIBS MODIS cloud-fraction rasters (`adapters/cloudcover.py`) | no |
| APRS stations (moving + RF-heard only) | live APRS-IS stream (`adapters/aprs_is.py`) | registered callsign |
| Vessels | AISHub `ws.php` | `AIS_API_KEY` |
| Incidents | tilannehuone.fi `halytysmap.php` | no |
| Traffic notices | liikennetilanne.fintraffic.fi GeoRSS | no |
| Country borders | bundled Natural Earth 110m outlines | no |
| Weather | Open-Meteo current conditions | no |
| Base map | OpenStreetMap / Humanitarian OSM / OSM DE / Esri World Imagery | no |

---

## 💡 Key Features & Behaviour

- **Bookmark Integration**: Built-in topbar bookmark button and setup modal providing single-click instructions for browser bookmark panel integration (<kbd>Ctrl</kbd>+<kbd>D</kbd> / <kbd>Cmd</kbd>+<kbd>D</kbd>).
- **Current Incidents Only**: Filtered by active time window (default 45 min) with live opacity fading.
- **Bilingual Interface**: Toggle between English and Finnish (`EN`/`FI`) instantly.
- **Aircraft Flight Cards**: Detail views with registration, altitude, speed, ICAO hex, and direct links to Flightradar24 and JetPhotos.
- **APRS Filtering**: Smart filtering for moving stations, RF-heard paths, and callsign SSIDs (`-9` vehicles, `-7` foot).

---

## 🧪 Testing

Run pytest to verify all parser and core system tests:

```bash
pytest
```

---

## 📁 Repository Structure

```
hqall/
├── app.py                   # FastAPI server & route handlers
├── cache.py                 # In-memory TTL cache
├── lang.py                  # Finnish -> English translation tables
├── install.sh               # Cross-platform installer & service setup
├── .env.example             # Template environment configuration
├── adapters/                # Upstream data integration modules
│   ├── adsb.py              # Aircraft feed
│   ├── aprs_is.py           # APRS-IS stream listener
│   ├── ais.py               # AIS vessel feed
│   ├── cloudcover.py        # NASA GIBS cloud deck
│   ├── fintraffic.py        # Fintraffic RSS alerts
│   ├── fmi.py               # FMI & MET Norway warnings
│   ├── openmeteo.py         # Weather geocoding & metrics
│   ├── rainviewer.py        # Precipitation radar
│   └── tilannehuone.py      # Emergency incidents
├── static/                  # Web UI frontend assets (HTML, CSS, JS)
└── tests/                   # Pytest suite
```
