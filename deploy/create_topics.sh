#!/usr/bin/env bash
# 按 deploy/topics/topics.txt 在运行中的 Kafka 上幂等创建 topic。
# 前置：deploy/docker-compose.yml 的 kafka 已 up 且 healthy。
# 用法：bash deploy/create_topics.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
TOPICS_FILE="$SCRIPT_DIR/topics/topics.txt"
BOOTSTRAP="${ANP_BOOTSTRAP:-localhost:9092}"
KT="/opt/kafka/bin/kafka-topics.sh"

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

echo "[create_topics] 等待 broker 就绪 @ $BOOTSTRAP ..."
for i in $(seq 1 30); do
  if dc exec -T kafka "$KT" --bootstrap-server "$BOOTSTRAP" --list >/dev/null 2>&1; then
    break
  fi
  [ "$i" -eq 30 ] && { echo "[create_topics] broker 未就绪，放弃"; exit 1; }
  sleep 2
done

while IFS= read -r raw; do
  line="${raw%%#*}"                       # 去注释
  [ -z "${line//[[:space:]]/}" ] && continue   # 跳空行
  read -r topic parts repl _ <<<"$line"
  echo "[create_topics] $topic (partitions=$parts rf=$repl)"
  # </dev/null：避免 docker exec 吞掉 while-read 的 stdin（否则只会建第一个 topic）
  dc exec -T kafka "$KT" --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists \
    --topic "$topic" --partitions "$parts" --replication-factor "$repl" </dev/null
done <"$TOPICS_FILE"

echo "[create_topics] 当前 topic 列表："
dc exec -T kafka "$KT" --bootstrap-server "$BOOTSTRAP" --list
