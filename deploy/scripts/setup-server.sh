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

detect_web_stack() {
  SITES_AVAILABLE="/etc/nginx/sites-available"
  SITES_ENABLED="/etc/nginx/sites-enabled"
  WEB_TEST_CMD="nginx -t"
  WEB_RELOAD_CMD="systemctl reload nginx"

  if ss -ltn 2>/dev/null | grep -q ':80 '; then
    if ss -ltnp 2>/dev/null | grep ':80 ' | grep -qi openresty; then
      WEB_TEST_CMD="openresty -t"
      WEB_RELOAD_CMD="systemctl reload openresty"
      echo "Detected OpenResty listening on port 80."
      return
    fi
  fi

  if systemctl is-active --quiet openresty 2>/dev/null || command -v openresty >/dev/null 2>&1; then
    WEB_TEST_CMD="openresty -t"
    WEB_RELOAD_CMD="systemctl reload openresty"
    echo "Detected OpenResty as the active web server."
    return
  fi

  if systemctl is-active --quiet nginx 2>/dev/null || command -v nginx >/dev/null 2>&1; then
    echo "Detected Nginx as the active web server."
    return
  fi

  echo "No active web server detected; nginx will be installed and started."
}

wait_for_apt() {
  local attempts=0
  while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
    || sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [[ "${attempts}" -gt 60 ]]; then
      echo "Timed out waiting for apt lock." >&2
      exit 1
    fi
    echo "Waiting for apt lock (${attempts}/60)..."
    sleep 5
  done
}

install_web_packages() {
  local packages=(git python3 python3-venv python3-pip certbot curl ca-certificates)
  if ! ss -ltnp 2>/dev/null | grep ':80 ' | grep -qi openresty \
    && ! systemctl is-active --quiet openresty 2>/dev/null; then
    packages+=(nginx python3-certbot-nginx)
  fi
  wait_for_apt
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"
}

reload_web() {
  eval "sudo ${WEB_TEST_CMD}"
  if systemctl is-active --quiet nginx 2>/dev/null; then
    sudo systemctl reload nginx
    return 0
  fi
  if systemctl is-active --quiet openresty 2>/dev/null; then
    sudo systemctl reload openresty
    return 0
  fi

  if ss -ltnp 2>/dev/null | grep -q ':80 '; then
    echo "Port 80 is already in use:"
    ss -ltnp 2>/dev/null | grep ':80 ' || true
    docker_container="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep '0.0.0.0:80->' | awk '{print $1}' | head -1 || true)"
    if [[ -n "${docker_container}" ]]; then
      echo "Stopping docker container on port 80: ${docker_container}"
      docker stop "${docker_container}" || true
    fi
  fi

  if systemctl list-unit-files 2>/dev/null | grep -q '^nginx\.service'; then
    if sudo systemctl enable nginx && sudo systemctl restart nginx; then
      echo "Started nginx."
      return 0
    fi
  fi

  echo "WARNING: Could not activate nginx/openresty. Web UI remains on http://127.0.0.1:8080"
  return 0
}

issue_certificate() {
  if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    echo "Certificate already exists, skipping certbot."
    return
  fi

  if command -v certbot >/dev/null 2>&1 && certbot plugins 2>/dev/null | grep -q nginx; then
    sudo certbot --nginx \
      -d "${DOMAIN}" \
      --non-interactive \
      --agree-tos \
      -m "admin@${DOMAIN#*.}" \
      --redirect || true
  fi

  if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
    sudo certbot certonly --webroot \
      -w /var/www/certbot \
      -d "${DOMAIN}" \
      --non-interactive \
      --agree-tos \
      -m "admin@${DOMAIN#*.}"
  fi
}

detect_web_stack
echo "==> Installing system packages"
wait_for_apt
install_web_packages

echo "==> Preparing app directory: ${APP_DIR}"
sudo mkdir -p "${APP_DIR}"
sudo chown "${DEPLOY_USER}:${DEPLOY_USER}" "${APP_DIR}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin main
  git -C "${APP_DIR}" reset --hard origin/main
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
sudo mkdir -p /var/www/certbot "${SITES_AVAILABLE}" "${SITES_ENABLED}"
if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  sudo cp "deploy/nginx/${DOMAIN}.conf" "${SITES_AVAILABLE}/${DOMAIN}.conf"
else
  sudo cp "deploy/nginx/${DOMAIN}.init.conf" "${SITES_AVAILABLE}/${DOMAIN}.conf"
fi
sudo ln -sf "${SITES_AVAILABLE}/${DOMAIN}.conf" "${SITES_ENABLED}/${DOMAIN}.conf"
sudo rm -f "${SITES_ENABLED}/default"
reload_web

echo "==> Requesting TLS certificate"
issue_certificate
if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  sudo cp "deploy/nginx/${DOMAIN}.conf" "${SITES_AVAILABLE}/${DOMAIN}.conf"
  reload_web
fi

sudo systemctl enable certbot.timer || true
sudo systemctl start certbot.timer || true

echo "==> Done"
echo "Service: sudo systemctl status ${SERVICE_NAME}"
echo "URL:     https://${DOMAIN}/"
echo "If the domain uses Cloudflare proxy, set SSL mode to Full (strict)."
