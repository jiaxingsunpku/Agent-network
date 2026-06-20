#!/usr/bin/env bash
# P8 vision hub sidecar 自愈 keeper（wangxuan 宿主机上跑）：sidecar 退出就重启。
set -u
cd "$(dirname "$0")/.." || exit 1
child=0
cleanup(){ kill "$child" 2>/dev/null; exit 0; }
trap cleanup TERM INT
while true; do
  python3 scripts/run_video_inference_glue.py &
  child=$!
  wait "$child"
  echo "[glue-keeper] sidecar 退出 @ $(date +%T)，3s 后重启"
  sleep 3
done
