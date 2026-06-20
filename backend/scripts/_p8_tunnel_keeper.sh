#!/usr/bin/env bash
# P8 反向隧道自愈 keeper（sjx 上跑）：ssh 掉线就重连；kill 本进程会连带杀掉 ssh 子进程。
set -u
WX="${1:-wangxuan}"
child=0
cleanup(){ kill "$child" 2>/dev/null; exit 0; }
trap cleanup TERM INT
while true; do
  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      -R 9092:127.0.0.1:9092 "$WX" &
  child=$!
  wait "$child"
  echo "[tunnel-keeper] ssh 退出 @ $(date +%T)，3s 后重连"
  sleep 3
done
