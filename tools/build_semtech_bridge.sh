#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEMTECH_HAL_REPO="${SEMTECH_HAL_REPO:-https://github.com/Lora-net/sx1302_hal.git}"
SEMTECH_HAL_REF="${SEMTECH_HAL_REF:-master}"
CACHE_DIR="${SEMTECH_HAL_CACHE:-$ROOT/build/semtech_sx1302_hal}"
HAL_DIR="${SEMTECH_HAL_DIR:-$CACHE_DIR}"
OUT_DIR="${1:-$ROOT/build/c_hal}"
mkdir -p "$OUT_DIR"

if [ ! -f "$HAL_DIR/libloragw/inc/loragw_hal.h" ]; then
  if [ -n "${SEMTECH_HAL_DIR:-}" ]; then
    echo "Semtech sx1302_hal not found at $HAL_DIR" >&2
    echo "Check SEMTECH_HAL_DIR=/path/to/sx1302_hal" >&2
    exit 2
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required to clone Semtech sx1302_hal, or set SEMTECH_HAL_DIR=/path/to/sx1302_hal" >&2
    exit 2
  fi
  echo "Cloning Semtech sx1302_hal from $SEMTECH_HAL_REPO ($SEMTECH_HAL_REF) to $CACHE_DIR" >&2
  rm -rf "$CACHE_DIR"
  git clone --depth 1 --branch "$SEMTECH_HAL_REF" "$SEMTECH_HAL_REPO" "$CACHE_DIR"
fi

if [ ! -f "$HAL_DIR/libloragw/inc/loragw_hal.h" ]; then
  echo "Semtech sx1302_hal not found at $HAL_DIR" >&2
  echo "Set SEMTECH_HAL_DIR=/path/to/sx1302_hal" >&2
  exit 2
fi

# Build a shared object that Python ctypes can load. We compile the Semtech
# sources with -fPIC here instead of linking the upstream static archive, because
# distro linkers often reject non-PIC .a objects inside a .so.
PATCHED_HAL_DIR="$OUT_DIR/hal_patched"
rm -rf "$PATCHED_HAL_DIR"
cp -a "$HAL_DIR" "$PATCHED_HAL_DIR"
HAL_DIR="$PATCHED_HAL_DIR"

python3 - <<'PY' "$HAL_DIR"
from pathlib import Path
import sys

hal = Path(sys.argv[1])

def patch(path: str, replacements: list[tuple[str, str]]) -> None:
    p = hal / path
    text = p.read_text()
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"pattern not found in {p}: {old!r}")
        text = text.replace(old, new)
    p.write_text(text)

patch("libloragw/inc/loragw_hal.h", [
    ("#define IS_LORA_BW(bw)          ((bw == BW_125KHZ) || (bw == BW_250KHZ) || (bw == BW_500KHZ))",
     "#define IS_LORA_BW(bw)          ((bw == BW_62K5) || (bw == BW_125KHZ) || (bw == BW_250KHZ) || (bw == BW_500KHZ))"),
    ("#define BW_UNDEFINED    0\n#define BW_500KHZ       0x06",
     "#define BW_UNDEFINED    0\n#define BW_62K5         0x03\n#define BW_500KHZ       0x06"),
])

patch("libloragw/src/loragw_hal.c", [
    ("case BW_125KHZ: return 125000;\n        default: return -1;",
     "case BW_125KHZ: return 125000;\n        case BW_62K5: return 62500;\n        default: return -1;"),
])

patch("libloragw/src/loragw_aux.c", [
    ("uint8_t bw_pow;", "double bw_pow;"),
    ("switch (bw) {\n        case BW_125KHZ:",
     "switch (bw) {\n        case BW_62K5:\n            bw_pow = 0.5;\n            break;\n        case BW_125KHZ:"),
    ("t_symbol_us = (1 << sf) * 8 / bw_pow;",
     "t_symbol_us = (uint16_t)(((double)(1 << sf) * 8.0) / bw_pow);"),
])

patch("libloragw/src/loragw_sx1302.c", [
    ("if (bandwidth == BW_125KHZ) {\n                radio_bw_delay = 19;",
     "if (bandwidth == BW_62K5) {\n                radio_bw_delay = 38;\n            } else if (bandwidth == BW_125KHZ) {\n                radio_bw_delay = 19;"),
    ("if (bandwidth == BW_125KHZ) {\n                radio_bw_delay += 0;",
     "if (bandwidth == BW_62K5) {\n                radio_bw_delay += 0;\n            } else if (bandwidth == BW_125KHZ) {\n                radio_bw_delay += 0;"),
])
PY

make -C "$HAL_DIR/libloragw" inc/config.h >/dev/null

OBJ_DIR="$OUT_DIR/obj"
rm -rf "$OBJ_DIR"
mkdir -p "$OBJ_DIR"
COMMON_FLAGS=(-O2 -Wall -Wextra -std=c99 -fPIC -I"$HAL_DIR/libloragw/inc" -I"$HAL_DIR/libloragw" -I"$HAL_DIR/libtools/inc")

for src in \
  "$HAL_DIR/libtools/src/tinymt32.c" \
  "$HAL_DIR/libloragw/src/loragw_spi.c" \
  "$HAL_DIR/libloragw/src/loragw_usb.c" \
  "$HAL_DIR/libloragw/src/loragw_com.c" \
  "$HAL_DIR/libloragw/src/loragw_mcu.c" \
  "$HAL_DIR/libloragw/src/loragw_i2c.c" \
  "$HAL_DIR/libloragw/src/sx125x_spi.c" \
  "$HAL_DIR/libloragw/src/sx125x_com.c" \
  "$HAL_DIR/libloragw/src/sx1250_spi.c" \
  "$HAL_DIR/libloragw/src/sx1250_usb.c" \
  "$HAL_DIR/libloragw/src/sx1250_com.c" \
  "$HAL_DIR/libloragw/src/sx1261_spi.c" \
  "$HAL_DIR/libloragw/src/sx1261_usb.c" \
  "$HAL_DIR/libloragw/src/sx1261_com.c" \
  "$HAL_DIR/libloragw/src/loragw_aux.c" \
  "$HAL_DIR/libloragw/src/loragw_reg.c" \
  "$HAL_DIR/libloragw/src/loragw_sx1250.c" \
  "$HAL_DIR/libloragw/src/loragw_sx1261.c" \
  "$HAL_DIR/libloragw/src/loragw_sx125x.c" \
  "$HAL_DIR/libloragw/src/loragw_sx1302.c" \
  "$HAL_DIR/libloragw/src/loragw_cal.c" \
  "$HAL_DIR/libloragw/src/loragw_debug.c" \
  "$HAL_DIR/libloragw/src/loragw_hal.c" \
  "$HAL_DIR/libloragw/src/loragw_lbt.c" \
  "$HAL_DIR/libloragw/src/loragw_stts751.c" \
  "$HAL_DIR/libloragw/src/loragw_gps.c" \
  "$HAL_DIR/libloragw/src/loragw_sx1302_timestamp.c" \
  "$HAL_DIR/libloragw/src/loragw_sx1302_rx.c" \
  "$HAL_DIR/libloragw/src/loragw_ad5338r.c" \
  "$ROOT/c_hal/meshcore_lgw_bridge.c"; do
  obj="$OBJ_DIR/$(basename "$src" .c).o"
  gcc "${COMMON_FLAGS[@]}" -c "$src" -o "$obj"
done

gcc -shared -o "$OUT_DIR/libmeshcore_lgw.so" "$OBJ_DIR"/*.o -lm -lrt -lpthread
printf '%s\n' "$OUT_DIR/libmeshcore_lgw.so"
