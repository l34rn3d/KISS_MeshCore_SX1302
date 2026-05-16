#!/bin/bash
# WM1302/SX1302 reset sequence copied from the working pyMC wrapper baseline.
# Targeted at Raspberry Pi/SenseCAP installs where pinctrl is available.
# Pins: 18=POWER_EN, 17=SX1302_RESET, 5=SX1261_RESET, 13=ADC_RESET.

set -euo pipefail

if ! command -v pinctrl >/dev/null 2>&1; then
    echo "pinctrl not found" >&2
    exit 1
fi

pinctrl set 18 op dh
sleep 0.01
pinctrl set 17 op dh
sleep 0.01
pinctrl set 17 dl
sleep 0.01
pinctrl set 5 op dl
sleep 0.01
pinctrl set 5 dh
sleep 0.01
pinctrl set 13 op dl
sleep 0.01
pinctrl set 13 dh
sleep 0.5
