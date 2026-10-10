#!/bin/bash
# Áp lại cửa sổ TCP, BBR, fq và cổng 80 → hub sau mỗi lần boot.
set -u
modprobe tcp_bbr 2>/dev/null || true
sysctl -p /etc/sysctl.d/99-fb-poller-upload.conf >/dev/null 2>&1 || true
IFACE="$(ip -o route show to default | awk '{print $5; exit}')"
if [ -n "${IFACE}" ]; then
  tc qdisc replace dev "${IFACE}" root fq 2>/dev/null || true
fi
if [ -w /sys/block/vda/queue/scheduler ]; then
  echo none > /sys/block/vda/queue/scheduler 2>/dev/null || true
fi
PORT="${CONTROL_PORT:-8088}"
if command -v iptables >/dev/null 2>&1; then
  iptables -t nat -C PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports "${PORT}" 2>/dev/null || \
    iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-ports "${PORT}"
fi
