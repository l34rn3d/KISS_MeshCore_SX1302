#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="sx1302-meshcore-kiss"
SERVICE_NAME="sx1302-meshcore-kiss.service"
REPO_URL="https://github.com/l34rn3d/KISS_MeshCore_SX1302.git"
BRANCH="pymc-tcp-dev"
INSTALL_DIR="/opt/sx1302-meshcore-kiss"
CONFIG_DIR="/etc/sx1302-meshcore-kiss"
SERVICE_USER="sx1302kiss"
SOURCE_DIR=""
DO_APT=1
INSTALL_DEV=0
START_SERVICE=0
ENABLE_SERVICE=1
FORCE=0
SYSTEMD=1

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Install ${APP_NAME} onto a Linux/SBC host.

Default behavior:
  - installs apt prerequisites unless --skip-apt is used
  - copies this checkout, or clones ${BRANCH} from GitHub if not run in a checkout
  - creates ${SERVICE_USER}
  - creates a Python venv and installs the package
  - builds the Semtech C HAL bridge library used by cricket
  - creates ${CONFIG_DIR}/config.yaml if missing
  - installs/enables the systemd service, but does NOT start it unless --start is used

Usage:
  sudo ./scripts/install.sh [options]

Options:
  --source-dir PATH       Use an existing source checkout instead of auto-detect/clone
  --install-dir PATH      Install path (default: ${INSTALL_DIR})
  --config-dir PATH       Config path (default: ${CONFIG_DIR})
  --service-user USER     Service user (default: ${SERVICE_USER})
  --repo-url URL          Git clone URL (default: ${REPO_URL})
  --branch NAME           Git branch to clone (default: ${BRANCH})
  --skip-apt              Do not run apt-get update/install
  --dev                   Install .[dev] extras too
  --start                 Start/restart the service after install
  --no-enable             Do not enable the systemd service
  --no-systemd            Do not install/enable/start systemd service
  --force                 Allow replacing an existing install directory
  -h, --help              Show this help

Examples:
  sudo ./scripts/install.sh
  sudo ./scripts/install.sh --start
  sudo ./scripts/install.sh --source-dir /tmp/sx1302-meshcore-kiss --install-dir /opt/sx1302-meshcore-kiss
USAGE
}

ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SOURCE_DIR="${2:?missing path}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:?missing path}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:?missing path}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?missing user}"; shift 2 ;;
    --repo-url) REPO_URL="${2:?missing url}"; shift 2 ;;
    --branch) BRANCH="${2:?missing branch}"; shift 2 ;;
    --skip-apt) DO_APT=0; shift ;;
    --dev) INSTALL_DEV=1; shift ;;
    --start) START_SERVICE=1; shift ;;
    --no-enable) ENABLE_SERVICE=0; shift ;;
    --no-systemd) SYSTEMD=0; ENABLE_SERVICE=0; START_SERVICE=0; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "unknown option: $1" ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    log "Re-running with sudo"
    exec sudo -E bash "$0" "${ORIG_ARGS[@]}"
  fi
  fatal "run as root, or install sudo"
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fatal "missing required command: $1"
}

validate_paths() {
  [[ -n "${INSTALL_DIR}" && "${INSTALL_DIR}" != "/" ]] || fatal "unsafe --install-dir: ${INSTALL_DIR}"
  [[ -n "${CONFIG_DIR}" && "${CONFIG_DIR}" != "/" ]] || fatal "unsafe --config-dir: ${CONFIG_DIR}"
  case "${INSTALL_DIR}" in
    /opt/*|/usr/local/*|/srv/*|/tmp/*) ;;
    *) warn "Install dir is outside the usual service paths: ${INSTALL_DIR}" ;;
  esac
}

abs_path() {
  python3 - <<'PY' "$1"
import os, sys
print(os.path.abspath(sys.argv[1]))
PY
}

find_source_dir() {
  if [[ -n "${SOURCE_DIR}" ]]; then
    SOURCE_DIR="$(abs_path "${SOURCE_DIR}")"
    [[ -f "${SOURCE_DIR}/pyproject.toml" ]] || fatal "--source-dir does not look like a checkout: ${SOURCE_DIR}"
    return
  fi

  local script_dir candidate
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  candidate="$(cd -- "${script_dir}/.." && pwd)"
  if [[ -f "${candidate}/pyproject.toml" ]] && grep -q 'name = "sx1302-meshcore-kiss"' "${candidate}/pyproject.toml"; then
    SOURCE_DIR="${candidate}"
    return
  fi

  SOURCE_DIR=""
}

install_apt_prereqs() {
  [[ ${DO_APT} -eq 1 ]] || { warn "Skipping apt prerequisite install"; return; }
  require_cmd apt-get
  log "Installing system prerequisites"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3 python3-venv python3-dev build-essential gpiod ca-certificates
}

ensure_service_user() {
  log "Ensuring service user ${SERVICE_USER} exists"
  if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --home "${INSTALL_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
  fi

  local groups_to_add=()
  getent group spi >/dev/null 2>&1 && groups_to_add+=(spi)
  getent group gpio >/dev/null 2>&1 && groups_to_add+=(gpio)
  getent group i2c >/dev/null 2>&1 && groups_to_add+=(i2c)
  getent group dialout >/dev/null 2>&1 && groups_to_add+=(dialout)
  if [[ ${#groups_to_add[@]} -gt 0 ]]; then
    usermod -aG "$(IFS=,; echo "${groups_to_add[*]}")" "${SERVICE_USER}"
  else
    warn "No spi/gpio/i2c/dialout groups found; check device permissions manually"
  fi
}

copy_from_checkout() {
  local src="$1"
  require_cmd git
  log "Copying tracked source from ${src} to ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"
  (
    cd "${src}"
    git ls-files -z | tar --null -T - -cf -
  ) | tar -C "${INSTALL_DIR}" -xf -
}

clone_source() {
  require_cmd git
  log "Cloning ${REPO_URL} branch ${BRANCH} to ${INSTALL_DIR}"
  git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${INSTALL_DIR}"
}

install_source_tree() {
  find_source_dir

  if [[ -e "${INSTALL_DIR}" ]]; then
    if [[ ${FORCE} -ne 1 ]]; then
      if [[ -f "${INSTALL_DIR}/pyproject.toml" ]] && grep -q 'name = "sx1302-meshcore-kiss"' "${INSTALL_DIR}/pyproject.toml"; then
        log "Existing ${APP_NAME} install found at ${INSTALL_DIR}; updating files in place"
      else
        fatal "${INSTALL_DIR} already exists and does not look like ${APP_NAME}; use --install-dir or --force"
      fi
    else
      warn "--force set: replacing ${INSTALL_DIR}"
      rm -rf "${INSTALL_DIR}"
    fi
  fi

  if [[ -n "${SOURCE_DIR}" ]]; then
    copy_from_checkout "${SOURCE_DIR}"
  else
    clone_source
  fi
}

create_venv_and_install() {
  log "Creating Python virtualenv and installing package"
  python3 -m venv "${INSTALL_DIR}/.venv"
  "${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade pip wheel setuptools
  local install_arg="${INSTALL_DIR}"
  [[ ${INSTALL_DEV} -eq 1 ]] && install_arg="${INSTALL_DIR}[dev]"
  "${INSTALL_DIR}/.venv/bin/python" -m pip install -e "${install_arg}"
}

build_semtech_bridge() {
  if [[ -x "${INSTALL_DIR}/tools/build_semtech_bridge.sh" ]]; then
    log "Building Semtech C HAL bridge"
    "${INSTALL_DIR}/tools/build_semtech_bridge.sh" "${INSTALL_DIR}/build/c_hal"
  else
    warn "Semtech C HAL build script not found; semtech_c_hal backend will not start until libmeshcore_lgw.so exists"
  fi
}

install_config() {
  log "Ensuring config exists at ${CONFIG_DIR}/config.yaml"
  install -d -m 0755 "${CONFIG_DIR}"
  if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
    install -m 0640 -o root -g "${SERVICE_USER}" "${INSTALL_DIR}/config.example.yaml" "${CONFIG_DIR}/config.yaml"
    warn "Created example config. Edit board-specific SPI/GPIO values only if this is not a SenseCAP/WM1302 cricket-style install."
  else
    warn "Keeping existing config: ${CONFIG_DIR}/config.yaml"
  fi
}

install_systemd_service() {
  [[ ${SYSTEMD} -eq 1 ]] || { warn "Skipping systemd install"; return; }
  require_cmd systemctl
  log "Installing systemd service ${SERVICE_NAME}"
  install -m 0644 "${INSTALL_DIR}/packaging/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
  systemctl daemon-reload
  if [[ ${ENABLE_SERVICE} -eq 1 ]]; then
    systemctl enable "${SERVICE_NAME}"
  fi
  if [[ ${START_SERVICE} -eq 1 ]]; then
    systemctl restart "${SERVICE_NAME}"
    systemctl --no-pager --full status "${SERVICE_NAME}" || true
  else
    warn "Service installed but not started. Use: sudo systemctl start ${SERVICE_NAME}"
  fi
}

verify_install() {
  log "Running install verification"
  "${INSTALL_DIR}/.venv/bin/python" -m py_compile \
    "${INSTALL_DIR}/src/sx1302_meshcore_kiss/main.py" \
    "${INSTALL_DIR}/src/sx1302_meshcore_kiss/config.py"
  "${INSTALL_DIR}/.venv/bin/python" -m sx1302_meshcore_kiss --help >/dev/null
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
  chmod 750 "${INSTALL_DIR}"

  log "Installed successfully"
  cat <<SUMMARY

Install path:  ${INSTALL_DIR}
Config path:   ${CONFIG_DIR}/config.yaml
Service user:  ${SERVICE_USER}
Service:       ${SERVICE_NAME}
pyMC TCP:      0.0.0.0:5055 by default
Dashboard:     http://<device-ip>:8080/

Next steps:
  1. Edit board-specific config:
     sudo editor ${CONFIG_DIR}/config.yaml
  2. Start the daemon:
     sudo systemctl start ${SERVICE_NAME}
  3. Watch logs:
     journalctl -u ${SERVICE_NAME} -n 100 --no-pager
  4. Point pyMC_Repeater at native pymc_tcp on 127.0.0.1:5055.

SUMMARY
}

validate_paths
install_apt_prereqs
ensure_service_user
install_source_tree
create_venv_and_install
build_semtech_bridge
install_config
install_systemd_service
verify_install
