#!/usr/bin/env bash
# P8 step2 跨机闭环：一键 up/down/status/test/logs（自愈隧道+桥+ingest+wangxuan sidecar）。
# 不动视频组任何系统级配置；删 .anp_p8_backup/RESTORE.sh 即复原其侧。
set -uo pipefail
ANP="/home/sjx/agent-network-platform"
PY="/home/sjx/miniconda3/envs/anp/bin/python"
SC="$ANP/backend/scripts"
WX="wangxuan"
WXREPO="/nvme2/VLM/agents_for_vision_hub"
RUN="/tmp/anp_p8_run"
BROKER="anp-kafka"
mkdir -p "$RUN"
log(){ printf '[p8] %s\n' "$*"; }
_alive(){ [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

ensure_broker(){
  docker ps --format '{{.Names}}' | grep -qx "$BROKER" || { log "ERROR: broker $BROKER 未运行，先 docker compose up"; exit 1; }
}

# 按需起 PKU VPN（连 wangxuan 用；服务不开机自启，仅本项目跨机链路 up 时拉起）。
# 已可达则跳过；否则 systemctl start pku-vpn-split（scoped 免密 sudoers）并等连上。
VPN_SERVICE="pku-vpn-split.service"
ensure_vpn(){
  if ssh -o BatchMode=yes -o ConnectTimeout=5 "$WX" true 2>/dev/null; then
    log "wangxuan 已可达（VPN 在线或无需），跳过起 VPN"; return 0
  fi
  log "wangxuan 不可达，按需拉起 VPN 服务 $VPN_SERVICE ..."
  if ! sudo -n systemctl start "$VPN_SERVICE" 2>/dev/null; then
    log "ERROR: 无法启动 $VPN_SERVICE（需 scoped 免密 sudoers，或手动 'sudo systemctl start $VPN_SERVICE'）"; return 1
  fi
  for _ in $(seq 1 20); do            # VPN 连接 + IAAA 认证 ~10-20s
    "$PY" -c "import time;time.sleep(2)"
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "$WX" true 2>/dev/null; then
      log "VPN 就绪，wangxuan 可达 ✓"; return 0
    fi
  done
  log "ERROR: VPN 已启动但 ~40s 内 wangxuan 仍不可达（查 /home/sjx/vpn_split.log）"; return 1
}

up(){
  ensure_broker
  ensure_vpn || { log "VPN/wangxuan 不就绪，终止 up"; exit 1; }
  if _alive "$RUN/tunnel.pid"; then log "隧道已在跑 (pid $(cat $RUN/tunnel.pid))"; else
    nohup bash "$SC/_p8_tunnel_keeper.sh" "$WX" >"$RUN/tunnel.log" 2>&1 </dev/null &
    echo $! >"$RUN/tunnel.pid"; log "隧道 keeper 启动 (pid $(cat $RUN/tunnel.pid))"
  fi
  "$PY" -c "import time;time.sleep(3)"
  if _alive "$RUN/bridge.pid"; then log "双向桥已在跑"; else
    # exec 让子 shell 替身为 python，故 $! 即 python 真身 PID（否则记到 AND-list 子壳 PID，down 杀不掉 python 致残留双发）
    ( cd "$ANP" && exec nohup "$PY" "$SC/run_visionhub_bridge.py" ) >"$RUN/bridge.log" 2>&1 </dev/null &
    echo $! >"$RUN/bridge.pid"
    log "双向桥启动 (pid $(cat $RUN/bridge.pid))"
  fi
  if _alive "$RUN/ingest.pid"; then log "ingest 已在跑"; else
    ( cd "$ANP" && exec nohup "$PY" "$SC/run_video_ingest.py" ) >"$RUN/ingest.log" 2>&1 </dev/null &
    echo $! >"$RUN/ingest.pid"
    log "ingest 启动 (pid $(cat $RUN/ingest.pid))"
  fi
  if ssh "$WX" 'p=$(cat /tmp/vh-glue-keeper.pid 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null'; then
    log "wangxuan sidecar keeper 已在跑 (pid $(ssh $WX cat /tmp/vh-glue-keeper.pid))，跳过（防重复消费组实例）"
  else
    # 起前先清掉可能残留的 keeper/glue（防多实例抢同消费组致 latest 竞态丢命令）
    ssh "$WX" 'for p in $(ps -eo pid,args | awk "/_p8_glue_keeper|run_video_inference_glue/ && !/awk/{print \$1}"); do kill "$p" 2>/dev/null; done; rm -f /tmp/vh-glue-keeper.pid' 2>/dev/null
    scp -q "$SC/_p8_glue_keeper.sh" "$WX:$WXREPO/scripts/_p8_glue_keeper.sh"
    ssh "$WX" "cd $WXREPO && (nohup bash scripts/_p8_glue_keeper.sh >/tmp/vh-glue-keeper.log 2>&1 </dev/null & echo \$! >/tmp/vh-glue-keeper.pid)"
    log "wangxuan sidecar keeper 启动 (pid $(ssh $WX cat /tmp/vh-glue-keeper.pid))"
  fi
  log "等消费者组 assignment..."; "$PY" -c "import time;time.sleep(8)"
  status
}

down(){
  ssh "$WX" 'kill $(cat /tmp/vh-glue-keeper.pid) 2>/dev/null; rm -f /tmp/vh-glue-keeper.pid' 2>/dev/null && log "wangxuan sidecar 已停" || log "wangxuan sidecar 无在跑"
  for n in bridge ingest tunnel; do
    if _alive "$RUN/$n.pid"; then kill "$(cat "$RUN/$n.pid")" 2>/dev/null; log "$n 已停 (pid $(cat $RUN/$n.pid))"; fi
    rm -f "$RUN/$n.pid"
  done
}

status(){
  echo "---- sjx 进程 ----"
  for n in tunnel bridge ingest; do
    if _alive "$RUN/$n.pid"; then echo "  $n: UP (pid $(cat $RUN/$n.pid))"; else echo "  $n: DOWN"; fi
  done
  echo "  wangxuan sidecar: $(ssh $WX 'p=$(cat /tmp/vh-glue-keeper.pid 2>/dev/null); if [ -n "$p" ] && kill -0 $p 2>/dev/null; then echo "UP (keeper $p)"; else echo DOWN; fi' 2>/dev/null)"
  echo "---- 跨机传输 ----"
  ssh "$WX" 'ss -ltn 2>/dev/null | grep -q ":9092" && echo "  wangxuan:9092 隧道在 (LISTEN)" || echo "  wangxuan:9092 隧道断!"' 2>/dev/null
  echo "---- 消费者组 ----"
  docker exec "$BROKER" /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list 2>/dev/null \
    | grep -E "visionhub-video-inference-glue|anp-visionhub-command-bridge|anp-visionhub-result-bridge|anp-video-ingest" | sed 's/^/  /'
}

test_loop(){
  ensure_broker
  local road="${1:-人民路}" cam="${2:-cam-renmin-001}"
  local out; out="$(cd "$ANP" && "$PY" "$SC/run_video_command.py" --road-name "$road" --camera-id "$cam" --prompt "${road}最近有没有交通事故或拥堵？请基于监控视频分析。" 2>&1)"
  echo "$out"
  local cid; cid="$(echo "$out" | grep -oE 'command_id=[0-9a-f-]+' | head -1 | cut -d= -f2)"
  log "等待回流入库（关联键 command_id=$cid）..."
  cd "$ANP" && "$PY" - "$cid" <<'PYEOF'
import sqlite3, json, sys, time
cid=sys.argv[1]
for i in range(60):
    time.sleep(3)
    con=sqlite3.connect("backend/.data/video_text.db"); con.row_factory=sqlite3.Row
    for r in con.execute("SELECT event_id,road_name,category,text,envelope FROM video_text_events WHERE source_agent_id='video-perception-visionhub-001' ORDER BY rowid DESC LIMIT 5"):
        env=json.loads(r["envelope"]); ptid=(env.get("trace") or {}).get("parent_trace_id")
        if ptid==cid:
            print("PASS ✅ event=%s road=%s cat=%s" % (r["event_id"][:8], r["road_name"], r["category"]))
            print("  parent_trace_id==command_id:", ptid==cid)
            print("  text[:120]:", (r["text"] or "")[:120])
            con.close(); raise SystemExit(0)
    con.close()
print("FAIL ❌ 120s 内未见 command_id=%s 的回流事件" % cid); raise SystemExit(1)
PYEOF
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  test) shift; test_loop "$@" ;;
  logs) tail -n "${3:-20}" "$RUN/${2:-bridge}.log" ;;
  *) echo "用法: $0 {up|down|status|test [road] [cam]|logs [tunnel|bridge|ingest] [N]}"; exit 1 ;;
esac
