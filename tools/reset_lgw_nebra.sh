#!/bin/sh

# Nebra hm-pktfwd style reset: one shared script for all SX130x hotspots.
# The selected hotspot profile supplies CONCENTRATOR_RESET_PIN and, rarely,
# SX125x_RESET_PIN through the environment.

if [ -n "${CONCENTRATOR_RESET_PIN_OVERRIDE+x}" ]; then
    CONCENTRATOR_RESET_PIN=${CONCENTRATOR_RESET_PIN_OVERRIDE}
elif [ -n "${2:-}" ]; then
    CONCENTRATOR_RESET_PIN=$2
fi

if [ -z "${CONCENTRATOR_RESET_PIN:-}" ]; then
    echo "CONCENTRATOR_RESET_PIN is required" >&2
    exit 1
fi

if [ -n "${SX125x_RESET_PIN_OVERRIDE+x}" ]; then
    SX125x_RESET_PIN=${SX125x_RESET_PIN_OVERRIDE}
fi

WAIT_GPIO() {
    sleep 0.1
}

init() {
    echo "${CONCENTRATOR_RESET_PIN}" > /sys/class/gpio/export 2>/dev/null || true
    WAIT_GPIO
    echo "out" > "/sys/class/gpio/gpio${CONCENTRATOR_RESET_PIN}/direction"
    WAIT_GPIO

    if [ -n "${SX125x_RESET_PIN:-}" ]; then
        echo "${SX125x_RESET_PIN}" > /sys/class/gpio/export 2>/dev/null || true
        WAIT_GPIO
        echo "out" > "/sys/class/gpio/gpio${SX125x_RESET_PIN}/direction"
        WAIT_GPIO
    fi
}

reset() {
    if [ -d "/sys/class/gpio/gpio${CONCENTRATOR_RESET_PIN}" ]; then
        echo "1" > "/sys/class/gpio/gpio${CONCENTRATOR_RESET_PIN}/value"
        WAIT_GPIO
        echo "0" > "/sys/class/gpio/gpio${CONCENTRATOR_RESET_PIN}/value"
        WAIT_GPIO
    fi

    if [ -n "${SX125x_RESET_PIN:-}" ] && [ -d "/sys/class/gpio/gpio${SX125x_RESET_PIN}" ]; then
        echo "1" > "/sys/class/gpio/gpio${SX125x_RESET_PIN}/value"
        WAIT_GPIO
        echo "0" > "/sys/class/gpio/gpio${SX125x_RESET_PIN}/value"
        WAIT_GPIO
    fi
}

term() {
    if [ -d "/sys/class/gpio/gpio${CONCENTRATOR_RESET_PIN}" ]; then
        echo "${CONCENTRATOR_RESET_PIN}" > /sys/class/gpio/unexport
        WAIT_GPIO
    fi

    if [ -n "${SX125x_RESET_PIN:-}" ] && [ -d "/sys/class/gpio/gpio${SX125x_RESET_PIN}" ]; then
        echo "${SX125x_RESET_PIN}" > /sys/class/gpio/unexport
        WAIT_GPIO
    fi
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
        echo "Usage: $0 {start|stop} [concentrator-reset-pin]" >&2
        exit 1
        ;;
esac
