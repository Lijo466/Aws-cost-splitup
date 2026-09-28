#!/usr/bin/env bash
# ONE-TIME setup on Lightsail:  sudo bash server-setup.sh
set -euo pipefail

TARGET_USER="${SUDO_USER:-$USER}"
APP_DIR=/opt/aws-dashboard

if ! command -v curl >/dev/null; then
  if command -v apt-get >/dev/null; then apt-get update -y && apt-get install -y curl
  else yum install -y curl; fi
fi

echo ">> Installing Docker Engine + Compose plugin"
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
usermod -aG docker "$TARGET_USER" || true

echo ">> Creating app directory"
mkdir -p "$APP_DIR/scripts"

if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" <<'EOF'
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=CHANGE_ME_STRONG_PASSWORD
FLASK_SECRET_KEY=CHANGE_ME_RANDOM_SECRET
AWS_ACCESS_KEY_ID=YOUR_REAL_ACCESS_KEY
AWS_SECRET_ACCESS_KEY=YOUR_REAL_SECRET_KEY
AWS_DEFAULT_REGION=us-east-1
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=false
GUNICORN_WORKERS=2
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=120
EOF
  chmod 600 "$APP_DIR/.env"
fi

chown -R "$TARGET_USER":"$TARGET_USER" "$APP_DIR" 2>/dev/null || true
echo ">> Done. Edit $APP_DIR/.env with real values, then push to main."
