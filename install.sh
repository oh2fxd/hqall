#!/usr/bin/env bash
set -e

# HQALL Installer for Linux and macOS (OSX)
# Configures environment and dependencies. Service setup is OPTIONAL (--service flag or prompt).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

INSTALL_SERVICE=false

# Check for command line flags
for arg in "$@"; do
  case $arg in
    --service|-s)
      INSTALL_SERVICE=true
      shift
      ;;
  esac
done

echo -e "${CYAN}${BOLD}=== HQALL Live Situational Awareness Map Setup ===${NC}\n"

# 1. Detect Operating System
OS="$(uname -s)"
case "$OS" in
  Linux*)   PLATFORM="linux" ;;
  Darwin*)  PLATFORM="macos" ;;
  *)        echo -e "${RED}Unsupported OS: $OS. HQALL installer supports Linux and macOS.${NC}" && exit 1 ;;
esac

echo -e "Detected Operating System: ${BOLD}$OS${NC} ($PLATFORM)"

# 2. Check for Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Error: python3 is not installed. Please install Python 3.9+ and try again.${NC}"
    exit 1
fi

PYTHON_BIN=$(command -v python3)
echo -e "Using Python: ${GREEN}$PYTHON_BIN${NC}"

# 3. Install dependencies
echo -e "Installing dependencies..."
"$PYTHON_BIN" -m pip install -r requirements.txt --quiet || "$PYTHON_BIN" -m pip install --user -r requirements.txt --quiet
echo -e "${GREEN}✓ Dependencies installed successfully.${NC}"

# 4. Configure Environment File
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    if [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo -e "${GREEN}✓ Created .env file from .env.example.${NC}"
    fi
fi

# Determine Port from .env or default 8077
PORT=8077
if [ -f "$SCRIPT_DIR/.env" ]; then
    ENV_PORT=$(grep -E "^HQALL_PORT=" "$SCRIPT_DIR/.env" | cut -d'=' -f2 | tr -d ' "')
    if [ -n "$ENV_PORT" ]; then
        PORT="$ENV_PORT"
    fi
fi

# 5. Optional System Service Setup
if [ "$INSTALL_SERVICE" = false ] && [ -t 0 ]; then
    echo -e ""
    read -p "Do you want to install HQALL as an autostart background service (systemd/launchd)? [y/N]: " -n 1 -r REPLY
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        INSTALL_SERVICE=true
    fi
fi

if [ "$INSTALL_SERVICE" = true ]; then
    echo -e "\n${BOLD}Configuring background system service...${NC}"

    if [ "$PLATFORM" = "linux" ]; then
        SYSTEMD_DIR="$HOME/.config/systemd/user"
        SERVICE_FILE="$SYSTEMD_DIR/hqall.service"
        mkdir -p "$SYSTEMD_DIR"

        cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=HQALL Live Situational Awareness Map
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN $SCRIPT_DIR/app.py
Restart=always
RestartSec=5
EnvironmentFile=-$SCRIPT_DIR/.env

[Install]
WantedBy=default.target
EOF

        echo -e "Systemd user service written to ${CYAN}$SERVICE_FILE${NC}"
        systemctl --user daemon-reload
        systemctl --user enable hqall.service
        systemctl --user restart hqall.service
        echo -e "${GREEN}✓ Linux systemd user service 'hqall.service' enabled and started.${NC}"

    elif [ "$PLATFORM" = "macos" ]; then
        LAUNCHD_DIR="$HOME/Library/LaunchAgents"
        PLIST_FILE="$LAUNCHD_DIR/com.hqall.service.plist"
        mkdir -p "$LAUNCHD_DIR"

        cat <<EOF > "$PLIST_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hqall.service</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$SCRIPT_DIR/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/hqall.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/hqall.err</string>
</dict>
</plist>
EOF

        echo -e "Launchd agent written to ${CYAN}$PLIST_FILE${NC}"
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        launchctl load "$PLIST_FILE"
        echo -e "${GREEN}✓ macOS launchd LaunchAgent 'com.hqall.service' enabled and loaded.${NC}"
    fi
else
    echo -e "${YELLOW}Skipping system background service installation.${NC}"
fi

# 6. Verify Server Readiness (or start app in background if not running)
APP_URL="http://127.0.0.1:$PORT/"
BOOKMARK_URL="http://127.0.0.1:$PORT/?bookmark=1"

IS_RUNNING=false
if "$PYTHON_BIN" -c "import urllib.request; urllib.request.urlopen('$APP_URL/api/health')" &>/dev/null; then
    IS_RUNNING=true
fi

if [ "$IS_RUNNING" = false ]; then
    echo -e "Starting HQALL server directly..."
    "$PYTHON_BIN" app.py &
    sleep 2
fi

# 7. Open Browser
echo -e "\nOpening HQALL in your default browser..."
if [ "$PLATFORM" = "macos" ]; then
    open "$BOOKMARK_URL" &>/dev/null || true
else
    if command -v xdg-open &>/dev/null; then
        xdg-open "$BOOKMARK_URL" &>/dev/null || true
    else
        "$PYTHON_BIN" -m webbrowser "$BOOKMARK_URL" &>/dev/null || true
    fi
fi

# 8. Terminal Bookmark Instructions
SHORTCUT="Ctrl + D"
if [ "$PLATFORM" = "macos" ]; then
    SHORTCUT="Cmd + D"
fi

echo -e "\n${GREEN}========================================================================${NC}"
echo -e "${GREEN}${BOLD}🎉 HQALL Setup Complete!${NC}"
echo -e "${GREEN}========================================================================${NC}"
echo -e "📍 Web Interface: ${CYAN}${BOLD}$APP_URL${NC}"
if [ "$INSTALL_SERVICE" = true ]; then
    echo -e "⚙️  Service Installed: YES ($( [ "$PLATFORM" = "linux" ] && echo "hqall.service" || echo "com.hqall.service" ))"
else
    echo -e "⚙️  Service Installed: NO (Running standalone)"
    echo -e "ℹ️  To start manually anytime: ${CYAN}python3 app.py${NC}"
fi
echo -e ""
echo -e "${YELLOW}${BOLD}📌 BOOKMARK INSTRUCTIONS:${NC}"
echo -e "   Please add HQALL to your browser's bookmark panel for quick access:"
echo -e "   👉 Press ${BOLD}$SHORTCUT${NC} in your browser window to add $APP_URL"
echo -e "   👉 Or drag the bookmark link from the on-screen prompt to your bookmarks bar."
echo -e "${GREEN}========================================================================${NC}\n"
