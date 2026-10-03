#!/usr/bin/env bash
set -e

# HQALL Uninstaller & Service Remover for Linux and macOS (OSX)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD='\033[1m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}${BOLD}=== HQALL Service Uninstaller ===${NC}\n"

OS="$(uname -s)"
case "$OS" in
  Linux*)   PLATFORM="linux" ;;
  Darwin*)  PLATFORM="macos" ;;
  *)        PLATFORM="unknown" ;;
esac

# 1. Remove Linux systemd user service
if [ "$PLATFORM" = "linux" ]; then
    SERVICE_FILE="$HOME/.config/systemd/user/hqall.service"
    if [ -f "$SERVICE_FILE" ] || systemctl --user is-enabled hqall.service &>/dev/null; then
        echo -e "Stopping and disabling Linux systemd user service 'hqall.service'..."
        systemctl --user stop hqall.service 2>/dev/null || true
        systemctl --user disable hqall.service 2>/dev/null || true
        if [ -f "$SERVICE_FILE" ]; then
            rm -f "$SERVICE_FILE"
            echo -e "Removed service file: ${CYAN}$SERVICE_FILE${NC}"
        fi
        systemctl --user daemon-reload
        echo -e "${GREEN}✓ Systemd user service uninstalled successfully.${NC}"
    else
        echo -e "No active systemd service found for HQALL."
    fi

# 2. Remove macOS launchd agent
elif [ "$PLATFORM" = "macos" ]; then
    PLIST_FILE="$HOME/Library/LaunchAgents/com.hqall.service.plist"
    if [ -f "$PLIST_FILE" ]; then
        echo -e "Unloading and removing macOS launchd agent 'com.hqall.service'..."
        launchctl unload "$PLIST_FILE" 2>/dev/null || true
        rm -f "$PLIST_FILE"
        echo -e "${GREEN}✓ Launchd agent uninstalled successfully.${NC}"
    else
        echo -e "No active launchd plist found for HQALL."
    fi
fi

# 3. Stop running standalone python app.py instance if running
PORT=8077
if [ -f "$SCRIPT_DIR/.env" ]; then
    ENV_PORT=$(grep -E "^HQALL_PORT=" "$SCRIPT_DIR/.env" | cut -d'=' -f2 | tr -d ' "')
    if [ -n "$ENV_PORT" ]; then
        PORT="$ENV_PORT"
    fi
fi

RUNNING_PIDS=$(lsof -t -i :"$PORT" 2>/dev/null || true)
if [ -n "$RUNNING_PIDS" ]; then
    echo -e "Stopping running HQALL process on port $PORT (PID: $RUNNING_PIDS)..."
    kill $RUNNING_PIDS 2>/dev/null || true
    echo -e "${GREEN}✓ Stopped running HQALL process.${NC}"
fi

echo -e "\n${GREEN}${BOLD}✓ HQALL Service Uninstallation Complete!${NC}\n"
