# Claude Code Agent Teams Parity (WIP)

目标：对标并超越 Claude Code 的 Agent Teams（团队模式），以“可运维的无人值守多 agent”为基线。

> 说明：本项目是单机文件系统上的编排器（task-board + message bus + worktrees）。它的优势是可审计、可离线、可控；短板主要在“事件驱动投递”和“持久会话上下文”。

## Parity Matrix

Legend:
- ✅ = 已实现（可用且可运维）
- 🟡 = 部分实现（可用但有边界/需要人工兜底）
- ❌ = 未实现（计划项）

### 1) 架构与隔离
- ✅ Lead + 多角色 worker（router + per-role daemon）
- ✅ worktree 分角色隔离写入面（避免并行改同一工作区）
- 🟡 “持久会话上下文”（Claude teammate 是连续会话；本项目是 message->exec，无状态；通过 role memory tail 近似）

### 2) 任务状态机（核心）
- ✅ task board：`pending / in_progress / completed / failed`
- ✅ 依赖：`depends_on` 满足才可 claim/dispatch
- ✅ claim 互斥：`state/tasks/tasks.lockdir`（目录锁）
- 🟡 自领取（self-claim）：role inbox 为空时的 self-dispatch fallback（lead 掉线也能推进）
- 🟡 任务重投递：dispatch message 丢失时的 TTL redelivery（避免卡死）

### 3) 通信与投递
- ✅ inbox/outbox 文件队列（原子写入 + 幂等 done）
- ✅ Router 回执转发（outbox -> inbox），支持 `::bus-send{...}` 自动接力
- ✅ 多目标投递：`to="r1,r2"` 与广播 `to="all"`（排除发送者）
- 🟡 事件驱动投递（mac：kqueue 监听目录变更以降低延迟；仍保留扫描逻辑与 poll fallback）

### 4) 竞争与无冲突
- ✅ 单消息目录锁：`state/processing/<id>.<role>.lockdir`
- ✅ 幂等哨兵：`state/done/<id>.<role>.ok`
- ✅ 可选全局串行锁：`AUTOPILOT_GLOBAL_LOCK=1` / `--serial`
- 🟡 细粒度冲突控制（按“写入文件集合/模块”加锁；可作为超越项）

### 5) 运维与可观测
- ✅ 每个 daemon 有独立 log（router/lead/builder/reviewer/tester）
- ✅ 心跳（30s）：包含 session/role/pid/队列计数/最近任务
- ✅ 退出可解释：SIGNAL/EXIT/FATAL + 上下文（pid/ppid/pgid/sid/ps）
- ✅ 一页诊断：`./scripts/diag.sh <sid>`
- 🟡 常驻守护（launchd 集成）：提供 `./scripts/launchd.sh` 一键 install/uninstall（用户级 LaunchAgent）

### 6) 权限与安全（Claude 的“可控边界”）
- 🟡 软边界（prompt 约束）：角色职责/可写文件范围
- 🟡 REPL `/sh` allowlist（默认安全命令）
- ❌ 硬边界（强制执行）：按角色强制校验“禁止写共享/禁止改代码”等（超越项）

## 当前短板（阻塞“完全对标”的点）

1. **事件驱动**：router/worker 都是轮询目录；要完全对标 Claude 的“自动送达”，需要 mac 优先的 FS 事件（kqueue/FSEvents）+ 统一调度。
2. **持久 teammate 上下文**：目前靠 memory tail 与任务文件复述；需要 task/thread 级上下文拼装（Top-K 相关 outbox/inbox/verify/decision 摘要注入）。
3. **强权限边界**：目前靠 prompt，遇到模型越界时缺少“强制阻断/回滚”的控制面。

## 超越路线（建议按阶段）

Phase 1 (稳定性/运维先于智能):
- kqueue 事件驱动（mac）+ fallback poll（其他平台）
- launchd 一键安装/卸载（开机自启、崩溃自愈）
- 任务重投递与限流（每 scan 最大 dispatch 数、队列 backpressure）

Phase 2 (更强的“团队控制面”):
- per-role 写入边界硬校验（执行后 `git diff` 审计，违规自动 fail + 回滚）
- per-intent/command allowlist（无人值守安全）
- task/thread 上下文注入（不依赖“连续会话”也能稳定多步推进）

Phase 3 (产品级体验):
- team REPL：默认目标 `/to <role>`、@mention、/claim /reassign /retry
- web/TUI 面板：任务板、队列、吞吐与成本可视化
- 多模型策略（便宜模型规划/路由，强模型实现/评审）+ 回归评测集
