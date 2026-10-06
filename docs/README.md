# 后端文档职责

本仓库只维护后端开发入口和实验原始记录，避免与前端仓库中的跨系统文档重复。

| 文档 | 职责 |
|---|---|
| [../README.md](../README.md) | 后端技术栈、运行与验证命令、部署边界、API 总览 |
| [../EXPERIMENT.md](../EXPERIMENT.md) | Phase 1 实验设计、原始结果和方法论备注 |
| [../AGENTS.md](../AGENTS.md) | 后端开发、文档同步和验证规则 |

跨前后端文档的唯一来源位于 [`YennieP/Fanfic-Assistant/docs`](https://github.com/YennieP/Fanfic-Assistant/tree/main/docs)：

- Design：最终产品与架构目标
- Implementation：两个默认分支上的真实实现及设计偏差
- ToDo：尚未完成的差额和风险
- Audit：追加式的文档与代码核查历史

后端行为变化如果影响跨系统事实，应在同一交付中同步对应文档；不要在本仓库再维护一份完整状态表。
