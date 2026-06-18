# agent-network-platform

智能体网络平台（Agent Network Platform，ANP）。基于 Kafka 的**分布式黑板系统**：为某一类协同任务定义智能体之间的协作约定、信息交换标准与时空统一性，让智能体「只发布自己产生的、只订阅自己所需的」信息。

平台不是一个大而统的中枢大脑，而是一层轻量协作基础设施。每一个协同任务域（如「交通管控」）对应一个 **World Model**；平台可承载多个 World Model。

## 这个仓库在做什么

老仓库 `~/worldmodel` 的后端是按「大而统世界模型」思路写的，已弃用。本仓库**重构智能体网络平台的后端架构**，补上老仓库缺失的核心——系统级智能体 / World Status 状态层——并**复用老仓库的前端 UI**，跑通一条干净的端到端链路，作为后续工作的底座。

### 本期范围

- **做**：重构后端架构（分层黑板 + 三类智能体 + 系统级状态层）；恢复 UI；用虚拟交通智能体跑通端到端。
- **不做（本期明确排除）**：视频组功能迁移、在状态层接大语言模型整理、智能体开发工具接入。

详见 [docs/architecture.md](docs/architecture.md)。

## 目录

| 路径 | 说明 |
|---|---|
| `docs/` | 架构、命名、协议、World Status、网关 API 设计文档 |
| `backend/anp/` | 后端 Python 包：`contracts` / `system_agent` / `gateway` / `registry` / `adapters` |
| `backend/agents/` | 可运行的示例智能体（v1：虚拟交通智能体） |
| `frontend/` | 从老仓库迁移的前端 UI（React + Vite + Three.js） |
| `deploy/` | docker-compose（单节点 Kafka）与 topic 清单 |
| `schemas/` | JSON Schema（envelope 与各 payload） |

## 技术栈

- 后端：Python + FastAPI + Kafka（KRaft 单节点）+ Pydantic
- 前端：React + Vite + TypeScript + Three.js（沿用老仓库）

## 本地开发（P0–P4 已就绪）

后端环境用 **conda**（env 名 `anp`，Python 3.12）。本机需代理见全局配置。

```bash
# 1) 后端依赖
conda create -n anp python=3.12 -y
conda activate anp
pip install -e "backend[dev]"

# 2) 起单节点 Kafka（KRaft）并建 topic
docker compose -f deploy/docker-compose.yml up -d
bash deploy/create_topics.sh

# 3) 端到端冒烟：observation 往返 / World Status / 网关 / 命令闭环
python backend/scripts/smoke_roundtrip.py     # 期望 [smoke] PASS
python backend/scripts/smoke_world_status.py  # 感知→系统级→World Status
python backend/scripts/smoke_gateway.py       # 网关五接口（TestClient，无需 Kafka）
python backend/scripts/smoke_commands.py      # command→Safety Guard→ack 全分支

# 4) 跑活体一条链路：网关 + 系统级 + 虚拟体（三个终端）
python backend/scripts/run_gateway.py --port 8000
python backend/scripts/run_system_agent.py
python backend/scripts/run_virtual_agent.py --seed 7
#   → curl http://127.0.0.1:8000/api/agent-network/snapshot

# 5) 契约/网关回归测试 / 由模型重生成 schema
cd backend && pytest -q
python backend/scripts/gen_schemas.py

# 6) 前端（迁移自老仓库，指向新网关；node 22 + vite）
cd frontend && npm install
npm run dev        # 开发服务 18180；默认把 /api/* 反代到网关 8000（VITE_GATEWAY_PROXY 可覆盖）
#   浏览器开 http://127.0.0.1:18180/?source=gateway  → 状态条 source gateway、命令闭环看 ack
#   不带 ?source=gateway 则为纯 mock 简化壳
npm run build      # tsc -b + vite build
```

- 唯一契约源：`backend/anp/contracts/`（envelope / topics / 枚举 / payload / builder）。`schemas/*.json` 由 `gen_schemas.py` 派生，勿手改。
- 网关：`backend/anp/gateway/`（纯读模型 + 命令入口，契约对齐老前端）；registry：`backend/anp/registry/`。
- 前端：`frontend/`（简化壳 + `?source=gateway` 挂回命令闭环 Inspector），详见 [frontend/README.md](frontend/README.md)。
- Kafka 起停与 topic 清单见 [deploy/README.md](deploy/README.md)。

## 给智能体的入口

开始任何工作前先读 [AGENTS.md](AGENTS.md) 与 [memory.md](memory.md)。命令与架构速查见 [CLAUDE.md](CLAUDE.md)。
