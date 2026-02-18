# 协议说明（Protocol）

## 1. 核心目标

- 保证多角色并行不互相污染上下文
- 保证每次交接都有可追溯的 Markdown 证据
- 保证最终决策可被 Reviewer/Tester 复核
- 对标 Claude Code 的 team 模式：先分类路由，再实现交付（见 `docs/team-mode.md`）。

## 2. 会话生命周期

1. Create
- 运行 `scripts/new-session.ps1`
- 生成 shared 与 role mailbox 结构
- 提示：脚本会自动定位到“主 worktree”（基于 `git rev-parse --git-common-dir` 推导），确保多 worktree 场景下会话目录只有一份。

2. Dispatch
- Lead 将子任务写入目标角色 `inbox.md`
- Lead 在自己的 `worklog.md` 记录派工依据

3. Build / Review / Test
- Builder 在本角色 worktree 实现
- Reviewer 输出 `shared/decision.md`
- Tester 输出 `shared/verify.md`

4. Close
- Lead 在 `shared/journal.md` 记录最终决策与后续行动

## 3. 文件边界

共享文件（所有角色可读）：
- `shared/task.md`
- `shared/pitfalls.md`
- `shared/decision.md`
- `shared/verify.md`
- `shared/journal.md`
- `shared/chat.md`（快速问答通道，非正式）

角色专属（仅本角色可写）：
- `roles/<role>/inbox.md`
- `roles/<role>/outbox.md`
- `roles/<role>/worklog.md`

## 4. 日志规范

每次关键动作至少记录：
- 发生时间
- 动作摘要
- 证据（命令、文件、测试结果之一）
- 下一步

## 4.1 任务分类与路由（推荐）

在派工前先按 `docs/team-mode.md` 做分类：
- Work Type（实现/重构/修复/调研/文档/验收）
- Risk（低/中/高）
- Evidence（至少一个可复现证据）

然后把子任务派发到正确角色，并在 outbox/decision/verify 中形成闭环。

## 5. 竞争式双 Builder 收敛

Lead 或 Reviewer 采用以下对比维度收敛：

- 改动面：影响文件/模块数量与复杂度
- 风险：回归与边界条件风险
- 可测试性：是否有可复制验证命令
- 回滚难度：单次回退成本
- 维护成本：后续扩展/排障成本

## 6. 失败与回滚

- 若 Builder 自测失败，先在 `outbox.md` 写失败复现，不要静默重试。
- 若 Tester 失败，必须写最小复现步骤。
- Lead 必须在 `shared/decision.md` 标注 “不合并” 或 “需修改后合并”。

## 7. 角色对话（mac 推荐）

- 角色之间允许通过 `shared/chat.md` 进行快速澄清与问答。
- 为避免并发写冲突，聊天消息推荐通过脚本写入 `shared/chat/messages/*.md`，再渲染到 `shared/chat.md`：
  - `./scripts/chat.sh <session> <role> "<message>" [mention]`
  - `./scripts/render-chat.sh <session>`
- 任何“最终结论/验收/合并建议”必须沉淀回：
  - `shared/decision.md`（评审结论）
  - `shared/verify.md`（可执行验收）
  - `roles/<role>/outbox.md`（实现交付）

## 8. 无人值守执行（Autopilot + Bus）

- 为保证“消息互通且不冲突”，自动执行推荐使用消息总线（见 `docs/bus.md`）：
  - 任务/问题：`bus/inbox/<role>/*.md`
  - 回执：`bus/outbox/*.md`
  - 失败：`bus/deadletter/<role>/*.md`
- Autopilot 守护进程会自动消费 `bus/inbox/<role>/`，并调用 `codex exec` 执行。
