#!/bin/sh

# Semtech CoreCell/SX1302 reset sequence, matching the upstream reset_lgw.sh
# style used by Helium hotspot containers. GPIO mappings must match the board.

SX1302_RESET_PIN=${SX1302_RESET_PIN:-23}
SX1302_POWER_EN_PIN=${SX1302_POWER_EN_PIN:-18}
SX1261_RESET_PIN=${SX1261_RESET_PIN:-22}
AD5338R_RESET_PIN=${AD5338R_RESET_PIN:-13}

WAIT_GPIO() {
    sleep 0.1
}

gpio_export() {
    if [ ! -d "/sys/class/gpio/gpio$1" ]; then
        echo "$1" > /sys/class/gpio/export
        WAIT_GPIO
    fi
}

gpio_unexport() {
    if [ -d "/sys/class/gpio/gpio$1" ]; then
        echo "$1" > /sys/class/gpio/unexport
        WAIT_GPIO
    fi
}

gpio_out() {
    echo "out" > "/sys/class/gpio/gpio$1/direction"
    WAIT_GPIO
}

gpio_write() {
    echo "$2" > "/sys/class/gpio/gpio$1/value"
    WAIT_GPIO
}

init() {
    gpio_export "$SX1302_RESET_PIN"
    gpio_export "$SX1261_RESET_PIN"
    gpio_export "$SX1302_POWER_EN_PIN"
    gpio_export "$AD5338R_RESET_PIN"
    gpio_out "$SX1302_RESET_PIN"
    gpio_out "$SX1261_RESET_PIN"
    gpio_out "$SX1302_POWER_EN_PIN"
    gpio_out "$AD5338R_RESET_PIN"
}

reset() {
    gpio_write "$SX1302_POWER_EN_PIN" 1
    gpio_write "$SX1302_RESET_PIN" 1
    gpio_write "$SX1302_RESET_PIN" 0
    gpio_write "$SX1261_RESET_PIN" 0
    gpio_write "$SX1261_RESET_PIN" 1
    gpio_write "$AD5338R_RESET_PIN" 0
    gpio_write "$AD5338R_RESET_PIN" 1
}

term() {
    gpio_unexport "$SX1302_RESET_PIN"
    gpio_unexport "$SX1261_RESET_PIN"
    gpio_unexport "$SX1302_POWER_EN_PIN"
    gpio_unexport "$AD5338R_RESET_PIN"
}

case "${1:-start}" in
    start)
        term
        init
        reset
        ;;
    stop)
        reset
        term
        ;;
    *)
        echo "Usage: $0 {start|stop}" >&2
        exit 1
        ;;
esac
