# deploy —— 本地单节点 Kafka

本目录提供本地开发与端到端冒烟用的单节点 Kafka（KRaft，无 ZooKeeper）。生产级部署不在本期范围（见 [AGENTS.md](../AGENTS.md) §1）。

## 组成

- `docker-compose.yml`：单节点 `apache/kafka:3.7.0`，KRaft 组合节点，对宿主暴露 `localhost:9092`。
- `topics/topics.txt`：交通域 v1 topic 清单（topic / partitions / replication）。命名规范见 [docs/naming.md](../docs/naming.md)。
- `create_topics.sh`：读取 `topics.txt`，幂等创建 topic。

## 快速开始

```bash
# 1) 起 Kafka（首次会拉镜像，本机如需代理自行设置 HTTPS_PROXY）
docker compose -f deploy/docker-compose.yml up -d

# 2) 等 healthy 后建 topic
bash deploy/create_topics.sh

# 3) 冒烟：发一条 observation 并取回校验往返（需 conda 环境 anp，见 ../README.md）
conda activate anp && python backend/scripts/smoke_roundtrip.py

# 停止（保留数据卷）
docker compose -f deploy/docker-compose.yml down
# 连数据一起清掉
docker compose -f deploy/docker-compose.yml down -v
```

## 约定

- 后端/前端客户端一律连 `localhost:9092`（可用环境变量 `ANP_BOOTSTRAP` 覆盖）。
- 关闭了自动建 topic（`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false`），topic 必须显式创建，以尽早暴露契约偏差。
- 单节点副本因子固定为 1；partition 数按实体 id 的并行度预留（见 `topics/topics.txt`）。
