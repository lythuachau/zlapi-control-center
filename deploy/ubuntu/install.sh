#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$(cd -- "${script_dir}/../.." && pwd)"
app_user="${APP_USER:-${SUDO_USER:-ubuntu}}"
app_group="${APP_GROUP:-$(id -gn "${app_user}")}" 
env_file="${ENV_FILE:-/etc/zlapi-control-center.env}"

if ! id "${app_user}" >/dev/null 2>&1; then
  echo "Application user does not exist: ${app_user}" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl ffmpeg git nodejs npm python3 python3-pip python3-venv

if [[ ! -f "${env_file}" ]]; then
  install -m 0600 "${script_dir}/zlapi.env.example" "${env_file}"
  echo "Created ${env_file}. Replace every REPLACE_ME value, then rerun this installer." >&2
  exit 2
fi

if grep -q 'REPLACE_ME' "${env_file}"; then
  echo "Configure every REPLACE_ME value in ${env_file}, then rerun this installer." >&2
  exit 2
fi

install -d -o "${app_user}" -g "${app_group}" /var/lib/zlapi-control-center
python3 -m venv "${app_dir}/.venv"
"${app_dir}/.venv/bin/python" -m pip install --upgrade pip
"${app_dir}/.venv/bin/python" -m pip install "${app_dir}"

if [[ -f "${app_dir}/package-lock.json" ]]; then
  sudo -u "${app_user}" npm --prefix "${app_dir}" ci
  sudo -u "${app_user}" npm --prefix "${app_dir}" run build
fi

for service_name in zlapi-web zlapi-worker zlapi-listener; do
  sed \
    -e "s|@APP_DIR@|${app_dir}|g" \
    -e "s|@APP_USER@|${app_user}|g" \
    -e "s|@APP_GROUP@|${app_group}|g" \
    -e "s|@ENV_FILE@|${env_file}|g" \
    "${script_dir}/${service_name}.service.in" \
    > "/etc/systemd/system/${service_name}.service"
done

systemctl daemon-reload
systemctl enable --now zlapi-web.service zlapi-worker.service zlapi-listener.service
systemctl restart zlapi-web.service zlapi-worker.service zlapi-listener.service

for _ in {1..30}; do
  if curl --fail --silent --output /dev/null http://127.0.0.1:8000/login; then
    echo "ZLAPI Control Center is running at http://127.0.0.1:8000"
    exit 0
  fi
  sleep 1
done

systemctl --no-pager --full status zlapi-web.service zlapi-worker.service zlapi-listener.service || true
exit 1
