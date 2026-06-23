# frontend —— 智能体网络平台前端（迁移自老仓库）

迁移自老仓库 `~/worldmodel/agent_network_frontend`（React + Vite + TypeScript + Three.js）。本期（P4）迁移其**简化壳 simplified-shell**（世界模型工作台 + 大交通地图），主要改 **API 接入层指向新网关**，并按需挂回命令闭环界面跑通端到端非 mock。设计契约见 [../docs/gateway-api.md](../docs/gateway-api.md)。

## 两种运行模式

- **mock 默认壳**：直接打开页面（不带 query），纯前端 mock 演示，与迁移前视觉一致，不连后端。
- **网关模式（非 mock）**：打开 `?source=gateway`，前端经网关读 P2 产出的真实 World Status；右侧 `InspectorPanel` 提供单智能体命令面板与**命令闭环**（命令下发 → 执行端 Safety Guard → ack，projection 3s 轮询拾取）。网关不可用或无有效节点时自动回落 mock（沿用老前端 normalizer + 回落约定）。

## 命令

```bash
npm install            # 依赖与老仓库锁文件一致，可复用其 node_modules
npm run dev            # 开发服务 18180
npm run build          # tsc -b + vite build → dist/
npm run preview        # 18181，预览 dist
npm run test:visual    # 默认 mock 壳视觉/交互冒烟
npm run test:ui        # 网关模式模块按钮、模型边界、响应式布局 QA
```

## 接入网关（二选一）

1. **同源反代（默认，推荐开发用）**：不设 `VITE_AGENT_NETWORK_API_BASE`，由 vite dev/preview 把 `/api/*` 反代到 `VITE_GATEWAY_PROXY`（默认 `http://127.0.0.1:8000`，即 `backend/scripts/run_gateway.py`）。
2. **客户端直连绝对地址**（生产/一体化，或前端与网关不同源）：设 `VITE_AGENT_NETWORK_API_BASE=http://host:port`，则浏览器直接请求该 base + `/api/agent-network/*`，不走 dev proxy。

环境变量示例见 [.env.example](.env.example)（`.env*` 已忽略，勿入库真实值）。

## 端到端非 mock 跑法

```bash
# 后端（anp conda 环境）：Kafka + 网关 + 系统级 + 虚拟体
docker compose -f ../deploy/docker-compose.yml up -d && bash ../deploy/create_topics.sh
python ../backend/scripts/run_gateway.py --port 8000
python ../backend/scripts/run_system_agent.py
python ../backend/scripts/run_virtual_agent.py --seed 7
# 前端
npm run dev
# 浏览器
open http://127.0.0.1:18180/?source=gateway
#   预期：状态条 source gateway；选中执行体 traffic-virtual-001 点「信号计划」下发，
#         命令闭环「最近命令/ack」数秒内出现 completed（通过本地 Safety Guard）。
```

## 与新网关的字段对齐（P4）

- 命令选项以 snapshot agent 节点的 `metrics.commandTypes` 为权威来源派生（契约驱动，兼容新执行体 id `traffic-virtual-001`），保留旧 id/tag 兜底。
- projection `event_list` 渲染补出 ack 的 `status` 与 `reason`。
- Inspector projection 由「选中取一次」改为 3s 轮询，命令下发后能看到 ack。

## 本期不做 / 已知边界

- 不迁视频组功能；不直连 Kafka（一切经网关）。
- 网关模式中部 stage 仍是 mock runtime 驱动的交通地图（视觉底图）；真实数据走状态条 / 节点选择器 / 右侧 Inspector。
- 老接口 `/api/world-model/runtime` 新网关无此路由 → 404 回落 `makeFallbackRuntime`（演示运行态，非阻塞）。
- `scripts/{visual-check,serve-with-gateway}.mjs` 为老仓库脚本，按需使用。无头视觉校验依赖系统 Chrome。
