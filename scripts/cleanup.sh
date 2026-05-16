#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="sx1302-meshcore-kiss"
SERVICE_NAME="sx1302-meshcore-kiss.service"
INSTALL_DIR="/opt/sx1302-meshcore-kiss"
CONFIG_DIR="/etc/sx1302-meshcore-kiss"
SERVICE_USER="sx1302kiss"
DRY_RUN=1
PURGE_CONFIG=0
REMOVE_USER=0
SYSTEMD=1

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Remove ${APP_NAME} from a Linux/SBC host.

Default behavior is a dry run. Pass --yes to actually remove files/services.

What cleanup removes with --yes:
  - stops/disables ${SERVICE_NAME}
  - removes /etc/systemd/system/${SERVICE_NAME}
  - removes ${INSTALL_DIR} because it is the installed application checkout + venv
  - leaves ${CONFIG_DIR}/config.yaml by default so board/radio settings are not lost

Optional cleanup:
  - --purge-config removes ${CONFIG_DIR} as well
  - --remove-user removes the ${SERVICE_USER} system user after files/services are gone

Usage:
  sudo ./scripts/cleanup.sh [options]

Options:
  --yes                 Actually perform cleanup; without this, only print actions
  --dry-run             Print actions only (default)
  --install-dir PATH    Install path to remove (default: ${INSTALL_DIR})
  --config-dir PATH     Config path to optionally purge (default: ${CONFIG_DIR})
  --service-user USER   Service user to optionally remove (default: ${SERVICE_USER})
  --purge-config        Also remove config directory
  --remove-user         Also remove service user
  --no-systemd          Skip systemd stop/disable/unit removal
  -h, --help            Show this help

Examples:
  sudo ./scripts/cleanup.sh                 # show what would be removed
  sudo ./scripts/cleanup.sh --yes           # remove service + install dir, keep config
  sudo ./scripts/cleanup.sh --yes --purge-config --remove-user
USAGE
}

ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) DRY_RUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --install-dir) INSTALL_DIR="${2:?missing path}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:?missing path}"; shift 2 ;;
    --service-user) SERVICE_USER="${2:?missing user}"; shift 2 ;;
    --purge-config) PURGE_CONFIG=1; shift ;;
    --remove-user) REMOVE_USER=1; shift ;;
    --no-systemd) SYSTEMD=0; shift ;;
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

validate_paths() {
  [[ -n "${INSTALL_DIR}" && "${INSTALL_DIR}" != "/" ]] || fatal "unsafe --install-dir: ${INSTALL_DIR}"
  [[ -n "${CONFIG_DIR}" && "${CONFIG_DIR}" != "/" ]] || fatal "unsafe --config-dir: ${CONFIG_DIR}"
  case "${INSTALL_DIR}" in
    /opt/*|/usr/local/*|/srv/*|/tmp/*) ;;
    *) warn "Install dir is outside the usual service paths: ${INSTALL_DIR}" ;;
  esac
}

run() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    printf 'DRY_RUN: %q' "$1"
    shift || true
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
  else
    "$@"
  fi
}

remove_systemd_service() {
  [[ ${SYSTEMD} -eq 1 ]] || { warn "Skipping systemd cleanup"; return; }
  command -v systemctl >/dev/null 2>&1 || { warn "systemctl not found; skipping systemd cleanup"; return; }

  log "Stopping and disabling ${SERVICE_NAME}"
  run systemctl stop "${SERVICE_NAME}"
  run systemctl disable "${SERVICE_NAME}"

  if [[ -e "/etc/systemd/system/${SERVICE_NAME}" ]]; then
    log "Removing systemd unit /etc/systemd/system/${SERVICE_NAME}"
    run rm -f "/etc/systemd/system/${SERVICE_NAME}"
  else
    warn "Systemd unit not present: /etc/systemd/system/${SERVICE_NAME}"
  fi

  run systemctl daemon-reload
  if [[ ${DRY_RUN} -eq 1 ]]; then
    run systemctl reset-failed "${SERVICE_NAME}"
  else
    systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
  fi
}

remove_install_dir() {
  if [[ -e "${INSTALL_DIR}" ]]; then
    log "Removing install directory ${INSTALL_DIR} because it contains the installed app checkout and venv"
    run rm -rf "${INSTALL_DIR}"
  else
    warn "Install directory not present: ${INSTALL_DIR}"
  fi
}

remove_config_dir() {
  if [[ ${PURGE_CONFIG} -ne 1 ]]; then
    warn "Keeping config directory ${CONFIG_DIR}; pass --purge-config to remove saved board/radio settings"
    return
  fi
  if [[ -e "${CONFIG_DIR}" ]]; then
    log "Removing config directory ${CONFIG_DIR} because --purge-config was requested"
    run rm -rf "${CONFIG_DIR}"
  else
    warn "Config directory not present: ${CONFIG_DIR}"
  fi
}

remove_service_user() {
  if [[ ${REMOVE_USER} -ne 1 ]]; then
    warn "Keeping service user ${SERVICE_USER}; pass --remove-user to delete it"
    return
  fi
  if id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "Removing service user ${SERVICE_USER} because --remove-user was requested"
    run userdel "${SERVICE_USER}"
  else
    warn "Service user not present: ${SERVICE_USER}"
  fi
}

print_plan() {
  cat <<PLAN
Cleanup plan for ${APP_NAME}:
  systemd service: /etc/systemd/system/${SERVICE_NAME}
  install dir:     ${INSTALL_DIR}
  config dir:      ${CONFIG_DIR} $([[ ${PURGE_CONFIG} -eq 1 ]] && echo '(will remove)' || echo '(will keep)')
  service user:    ${SERVICE_USER} $([[ ${REMOVE_USER} -eq 1 ]] && echo '(will remove)' || echo '(will keep)')
  mode:            $([[ ${DRY_RUN} -eq 1 ]] && echo 'dry run only; pass --yes to remove' || echo 'ACTIVE CLEANUP')
PLAN
}

validate_paths
print_plan
remove_systemd_service
remove_install_dir
remove_config_dir
remove_service_user

if [[ ${DRY_RUN} -eq 1 ]]; then
  warn "Dry run complete; no files were removed. Re-run with --yes to perform cleanup."
else
  log "Cleanup complete"
fi
