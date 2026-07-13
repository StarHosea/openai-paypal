#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/openai-paypal}"
VENV_DIR="${APP_DIR}/.venv"
SERVICE_NAME="${SERVICE_NAME:-openai-paypal-web.service}"

cd "${APP_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Virtualenv not found at ${VENV_DIR}. Run setup-server.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-headless.txt
python -m playwright install chromium
python -m playwright install-deps chromium || true

if command -v nginx >/dev/null 2>&1; then
  sudo nginx -t
  sudo systemctl reload nginx
fi

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true

curl -fsS "http://127.0.0.1:8080/api/health" >/dev/null
echo "Deploy finished. Health check passed."
