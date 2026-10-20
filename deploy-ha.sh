#!/bin/bash
#
# Build Ledger - Home Assistant Deployment Script
# Run this on your Home Assistant host/system (Linux)
# It installs server.py, configures it, and sets up a systemd service
#

set -euo pipefail

echo "=============================================="
echo "  Build Ledger - Home Assistant Deployment"
echo "=============================================="
echo ""

# -------------------------------------------------------
# 1. Detect environment
# -------------------------------------------------------
if [[ "$EUID" -eq 0 ]]; then
  RUN_AS_ROOT=true
else
  RUN_AS_ROOT=false
fi

# Check OS
if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  OS=$NAME
else
  OS="Unknown"
fi

echo "Detected OS: $OS"
echo "Running as root: $RUN_AS_ROOT"
echo ""

# -------------------------------------------------------
# 2. Prompt for configuration parameters
# -------------------------------------------------------
echo "--- Configuration ---"

read -p "Server listen port [8099]: " SERVER_PORT
SERVER_PORT=${SERVER_PORT:-8099}

read -p "Data directory [/var/build_ledger/data]: " DATA_DIR
DATA_DIR=${DATA_DIR:-/var/build_ledger/data}

read -p "Documents directory [/var/build_ledger/docs]: " DOCS_DIR
DOCS_DIR=${DOCS_DIR:-/var/build_ledger/docs}

read -p "Web directory (where index.html is served) [/var/www/build_ledger]: " WEB_DIR
WEB_DIR=${WEB_DIR:-/var/www/build_ledger}

read -p "Invite code (leave empty for open registration): " INVITE_CODE

read -p "User cap (0 = unlimited) [0]: " USER_CAP
USER_CAP=${USER_CAP:-0}

read -p "Create systemd service? (y/n) [y]: " CREATE_SERVICE
CREATE_SERVICE=${CREATE_SERVICE:-y}

echo ""

# -------------------------------------------------------
# 3. Check/install prerequisites
# -------------------------------------------------------
echo "--- Checking prerequisites ---"

if ! command -v python3 &> /dev/null; then
  echo "Python 3 not found. Installing..."
  if [[ "$RUN_AS_ROOT" == true ]]; then
    if command -v apt-get &> /dev/null; then
      apt-get update && apt-get install -y python3
    elif command -v yum &> /dev/null; then
      yum install -y python3
    elif command -v dnf &> /dev/null; then
      dnf install -y python3
    elif command -v apk &> /dev/null; then
      apk add python3
    else
      echo "ERROR: Cannot install Python 3 automatically. Please install it manually."
      exit 1
    fi
  else
    echo "ERROR: Python 3 is not installed and you are not root. Please install Python 3 manually."
    exit 1
  fi
else
  echo "Python 3 found: $(python3 --version)"
fi

if ! command -v curl &> /dev/null && ! command -v wget &> /dev/null; then
  echo "WARNING: Neither curl nor wget found. You will need to manually copy server.py to this machine."
fi

# -------------------------------------------------------
# 4. Create directories
# -------------------------------------------------------
echo "--- Creating directories ---"

if [[ "$RUN_AS_ROOT" == true ]]; then
  mkdir -p "$DATA_DIR" "$DOCS_DIR" "$WEB_DIR"
  chmod 755 "$DATA_DIR" "$DOCS_DIR" "$WEB_DIR"
else
  mkdir -p "$DATA_DIR" "$DOCS_DIR" "$WEB_DIR" 2>/dev/null || {
    echo "ERROR: Cannot create directories. Run with sudo or choose directories you own."
    exit 1
  }
fi

echo "  DATA_DIR=$DATA_DIR"
echo "  DOCS_DIR=$DOCS_DIR"
echo "  WEB_DIR=$WEB_DIR"

# -------------------------------------------------------
# 5. Download server.py
# -------------------------------------------------------
echo "--- Downloading server.py ---"

SERVER_PY_URL="https://raw.githubusercontent.com/mukeshrvyas/build-ledger/main/server.py"

if command -v curl &> /dev/null; then
  curl -fsSL "$SERVER_PY_URL" -o "$WEB_DIR/server.py"
elif command -v wget &> /dev/null; then
  wget -q "$SERVER_PY_URL" -O "$WEB_DIR/server.py"
else
  echo "ERROR: Cannot download server.py. Please manually copy it to $WEB_DIR/server.py"
  echo "  From: https://github.com/mukeshrvyas/build-ledger/raw/main/server.py"
  exit 1
fi

if [[ ! -f "$WEB_DIR/server.py" ]]; then
  echo "ERROR: Failed to download server.py"
  exit 1
fi

echo "  Downloaded to $WEB_DIR/server.py"

# Also copy index.html if it exists locally, otherwise download from GitHub
if [[ -f "$WEB_DIR/index.html" ]]; then
  echo "  index.html already exists in web directory"
elif command -v curl &> /dev/null; then
  curl -fsSL "https://raw.githubusercontent.com/mukeshrvyas/build-ledger/main/index.html" -o "$WEB_DIR/index.html" || \
    echo "WARNING: Could not download index.html. Copy it manually from the repo."
elif command -v wget &> /dev/null; then
  wget -q "https://raw.githubusercontent.com/mukeshrvyas/build-ledger/main/index.html" -O "$WEB_DIR/index.html" || \
    echo "WARNING: Could not download index.html. Copy it manually from the repo."
fi

# -------------------------------------------------------
# 6. Create environment file
# -------------------------------------------------------
echo "--- Creating environment configuration ---"

ENV_FILE="/etc/build-ledger.env"

cat > "$ENV_FILE" <<EOF
# Build Ledger Server Configuration
# Generated by deploy-ha.sh on $(date)
DATA_DIR=$DATA_DIR
DOCS_DIR=$DOCS_DIR
WEB_DIR=$WEB_DIR
PORT=$SERVER_PORT
INVITE_CODE=$INVITE_CODE
USER_CAP=$USER_CAP
EOF

if [[ "$RUN_AS_ROOT" == true ]]; then
  chmod 600 "$ENV_FILE"
  chown root:root "$ENV_FILE"
fi

echo "  Environment saved to $ENV_FILE"

# -------------------------------------------------------
# 7. Create systemd service (if requested)
# -------------------------------------------------------
if [[ "$CREATE_SERVICE" == "y" || "$CREATE_SERVICE" == "Y" ]]; then
  if [[ "$RUN_AS_ROOT" != true ]]; then
    echo "WARNING: Cannot create systemd service without root. Skipping service creation."
  else
    echo "--- Creating systemd service ---"

    SERVICE_FILE="/etc/systemd/system/build-ledger.service"

    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Build Ledger Server
After=network.target

[Service]
Type=simple
EnvironmentFile=/etc/build-ledger.env
WorkingDirectory=${WEB_DIR}
ExecStart=/usr/bin/python3 ${WEB_DIR}/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable build-ledger.service
    systemctl start build-ledger.service

    echo "  Service created and started"
    echo "  Check status: systemctl status build-ledger"
    echo "  View logs: journalctl -u build-ledger -f"
  fi
fi

# -------------------------------------------------------
# 8. Display summary
# -------------------------------------------------------
echo ""
echo "=============================================="
echo "  Deployment Complete!"
echo "=============================================="
echo ""
echo "Server configuration:"
echo "  Port:        $SERVER_PORT"
echo "  Data dir:    $DATA_DIR"
echo "  Docs dir:    $DOCS_DIR"
echo "  Web dir:     $WEB_DIR"
echo "  Invite code: ${INVITE_CODE:-"(none)"}"
echo "  User cap:    $USER_CAP"
echo ""

# Try to detect the server's IP address
SERVER_IP=""
if command -v ip &> /dev/null; then
  SERVER_IP=$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
elif command -v hostname &> /dev/null; then
  SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi

if [[ -n "$SERVER_IP" ]]; then
  echo "Access the app at:  http://$SERVER_IP:$SERVER_PORT"
  echo ""
  echo "IMPORTANT: For mobile sync and WebCrypto, use HTTPS, not HTTP."
  echo "           Set up a reverse proxy (nginx, Caddy, or HA proxy) with SSL."
  echo ""
  echo "Then in the Build Ledger app (https://mukeshrvyas.github.io/build-ledger/):"
  echo "  1. Go to More → Settings"
  echo "  2. Enter Server URL: https://your-domain-or-ip:$SERVER_PORT"
  echo "  3. Enable 'Sync to server'"
  echo "  4. Create account with same username on all devices"
  echo ""
else
  echo "Find your server's IP address and access: http://<your-ip>:$SERVER_PORT"
  echo ""
  echo "Then in the Build Ledger app:"
  echo "  1. Go to More → Settings → Server URL"
  echo "  2. Enter your server address"
fi

echo ""
echo "Service management (if systemd was used):"
echo "  status:   systemctl status build-ledger"
echo "  logs:     journalctl -u build-ledger -f"
echo "  restart:  systemctl restart build-ledger"
echo "  stop:     systemctl stop build-ledger"
echo ""
echo "To uninstall later:"
echo "  systemctl disable --now build-ledger.service"
echo "  rm /etc/systemd/system/build-ledger.service /etc/build-ledger.env"
echo "  rm -rf $DATA_DIR $DOCS_DIR"
echo ""
echo "=============================================="