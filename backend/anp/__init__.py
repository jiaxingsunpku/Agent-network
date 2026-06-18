"""Agent Network Platform (ANP) 后端包。

基于 Kafka 的分布式黑板系统。子包职责见 docs/architecture.md §4：

- ``anp.contracts``    唯一契约源：envelope / topic / 角色 / 命令-ack / payload。
- ``anp.system_agent`` 系统级智能体：窗口聚合产出 World Status（P2）。
- ``anp.gateway``      纯读模型 + 命令入口（P3）。
- ``anp.registry``     注册 / 心跳 / 白名单（P3）。
- ``anp.adapters``     感知/执行智能体接入适配（P5）。
"""

__version__ = "0.1.0"
