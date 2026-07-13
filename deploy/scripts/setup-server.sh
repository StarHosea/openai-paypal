#!/usr/bin/env bash
set -euo pipefail

# One-time server bootstrap for pix.shuangdeng.space
# Run on the server as a sudo-capable user:
#   curl -fsSL https://raw.githubusercontent.com/StarHosea/openai-paypal/main/deploy/scripts/setup-server.sh | bash

APP_DIR="${APP_DIR:-/opt/openai-paypal}"
REPO_URL="${REPO_URL:-https://github.com/StarHosea/openai-paypal.git}"
DOMAIN="${DOMAIN:-pix.shuangdeng.space}"
SERVICE_NAME="${SERVICE_NAME:-openai-paypal-web.service}"
DEPLOY_USER="${DEPLOY_USER:-$(whoami)}"

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as a normal user with sudo, not as root." >&2
  exit 1
fi

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  git \
  python3 \
  python3-venv \
  python3-pip \
  nginx \
  certbot \
  python3-certbot-nginx \
  curl \
  ca-certificates

echo "==> Preparing app directory: ${APP_DIR}"
sudo mkdir -p "${APP_DIR}"
sudo chown "${DEPLOY_USER}:${DEPLOY_USER}" "${APP_DIR}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull --ff-only
fi

cd "${APP_DIR}"

if [[ ! -f .env ]]; then
  cp .env.example .env
  cat >> .env <<'EOF'

# Production overrides (review before going live)
PAYPAL_WEB_PRODUCTION=1
PAYPAL_WEB_COOKIE_SECURE=1
PAYPAL_WEB_ALLOW_DEBUG_LOGS=0
PAYPAL_WEB_MAX_ACTIVE_JOBS=4
PAYPAL_WEB_MAX_ACTIVE_JOBS_PER_DEVICE=2
EOF
  echo "Created ${APP_DIR}/.env from .env.example — edit secrets before production use."
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-headless.txt
python -m playwright install chromium
python -m playwright install-deps chromium

chmod +x deploy/scripts/deploy.sh

echo "==> Installing systemd service"
sudo cp "deploy/systemd/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
sudo sed -i "s|^User=.*|User=${DEPLOY_USER}|" "/etc/systemd/system/${SERVICE_NAME}"
sudo sed -i "s|^Group=.*|Group=${DEPLOY_USER}|" "/etc/systemd/system/${SERVICE_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "==> Installing nginx site"
sudo mkdir -p /var/www/certbot
if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  sudo cp "deploy/nginx/${DOMAIN}.conf" "/etc/nginx/sites-available/${DOMAIN}.conf"
else
  sudo cp "deploy/nginx/${DOMAIN}.init.conf" "/etc/nginx/sites-available/${DOMAIN}.conf"
fi
sudo ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "==> Requesting TLS certificate"
if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  sudo certbot --nginx \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m "admin@${DOMAIN#*.}" \
    --redirect
  sudo cp "deploy/nginx/${DOMAIN}.conf" "/etc/nginx/sites-available/${DOMAIN}.conf"
  sudo nginx -t
  sudo systemctl reload nginx
else
  echo "Certificate already exists, skipping certbot."
fi

sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

echo "==> Done"
echo "Service: sudo systemctl status ${SERVICE_NAME}"
echo "URL:     https://${DOMAIN}/"
echo "If the domain uses Cloudflare proxy, set SSL mode to Full (strict)."
