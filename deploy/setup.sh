#!/bin/bash
# Polytex VM Setup Script
# Run as root on a fresh Ubuntu/Debian server
set -e

APP_DIR="/root/polytex"

echo "=== Polytex Setup ==="

# System packages
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip nginx git

# App directory
mkdir -p "$APP_DIR"
cd "$APP_DIR"

# Virtual environment
python3 -m venv venv
source venv/bin/activate

# Dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Environment file
if [ ! -f .env ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > .env <<EOF
SECRET_KEY=$SECRET
ADMIN_PASSWORD=admin123
FLASK_DEBUG=0
EOF
    echo ">>> .env created — change ADMIN_PASSWORD!"
fi

# Create default directories
mkdir -p Unbearbeitet Bearbeitet

# Nginx
cp deploy/nginx.conf /etc/nginx/sites-available/polytex
ln -sf /etc/nginx/sites-available/polytex /etc/nginx/sites-enabled/polytex
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# Systemd service
cp deploy/polytex.service /etc/systemd/system/polytex.service
systemctl daemon-reload
systemctl enable polytex
systemctl restart polytex

echo ""
echo "=== Setup complete ==="
echo "App running at http://$(hostname -I | awk '{print $1}')"
echo "Check status: systemctl status polytex"
echo "View logs:    journalctl -u polytex -f"
