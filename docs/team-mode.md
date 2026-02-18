# 团队分类模式（Team Classification Mode）

目标：在开始实现前先“分类 + 路由”，把任务拆成可验证的交付，并稳定落在正确的角色与文档里。

## 1. 分类维度

每个子任务至少标注以下 3 项：
- **Work Type（工作类型）**：实现 / 重构 / 缺陷修复 / 调研 / 文档 / 验收
- **Risk（风险）**：低 / 中 / 高（回归面、边界条件、不可逆操作）
- **Evidence（证据）**：至少一个（命令、文件、测试、截图、日志）

## 2. 角色路由（对标 Claude Code team 模式）

| 子任务特征 | 推荐角色 | 产出位置（必须） |
| --- | --- | --- |
| 需求澄清、拆解、收敛、里程碑推进 | Lead | `shared/task.md`, `roles/*/inbox.md`, `shared/journal.md` |
| 最小改动实现（低风险、可快速回滚） | Builder-A | `roles/builder-a/outbox.md`, `shared/verify.md` |
| 系统性方案/必要重构（结构化、可测试） | Builder-B（可选） | `roles/builder-b/outbox.md`, `shared/verify.md` |
| 评审、风险把关、方案对比、合并建议 | Reviewer | `shared/decision.md`, `roles/reviewer/outbox.md` |
| 验收、复现、回归、覆盖范围说明 | Tester | `shared/verify.md`, `roles/tester/outbox.md` |

说明：
- 双 Builder 竞争仅发生在“方案存在明显分歧/权衡”的场景，不要滥用。
- 所有“最终结论”只能落在 `shared/*.md`，不要只写在 outbox。
- 无人值守模式下，派工与追问建议通过消息总线 `bus/inbox/<role>/`（见 `docs/bus.md`），避免对话歧义与重复执行。
- 建议每次派工都绑定 `task_id`（`state/tasks/tasks.json`），让执行闭环可验证、可领取、可依赖。

### Intent 路由约定（建议）

为了让“分类 + 路由”能自动闭环，推荐在 bus 消息里使用固定的 `intent`：

| intent | 典型发送者 | 典型接收者 | 语义 |
| --- | --- | --- | --- |
| bootstrap | system/lead | lead | 读取 task 并拆解派工 |
| implement | lead | builder-* | 实现任务（必须含 acceptance） |
| review | lead/builder-* | reviewer | 评审并给出合并建议或必改项 |
| test | lead/builder-* | tester | 验收并写回 verify 证据 |
| fix | reviewer/tester/lead | builder-* | 补修/补证据（必须给可复制验收点） |
| question | 任意 | 任意 | 需要澄清（必须是具体问题） |
| info | 任意 | lead | 非阻塞信息同步（便于收敛） |

无人值守推荐结合 `::bus-send{...}` 路由指令（见 `docs/bus.md`），让角色在回执里直接触发下一跳。

## 2.1 任务状态机（强烈建议）

最小字段：
- `id`：任务编号
- `status`：`pending / in_progress / completed / failed`
- `owner`：建议执行角色
- `depends_on`：依赖任务 ID 列表
- `acceptance`：验收条件

行为约束：
- 任务必须先 claim 才执行（避免多人抢同一任务）。
- 依赖未满足的任务不能 claim。
- 回执必须回写到同一个 `task_id`。
- `lead/bootstrap` 可自动生成这张任务图；后续任务会在依赖满足时自动派发。

## 3. 交付契约（每个 outbox 必须包含）

最小交付格式（建议直接按模板填）：
- **Task IDs**：这次交付覆盖的任务编号
- **What changed**：改动摘要（<= 5 条；Builder-B <= 8 条）
- **How to verify**：可复制命令 + 预期结果
- **Risks**：已知风险与边界
- **Rollback**：回滚方式（或说明为什么不需要）
- **Next**：下一步（如果需要他人接力）

## 4. 常见反模式（禁止）

- 只有“已完成/应该没问题”，没有任何可执行证据。
- 角色跨读：Builder-A 读取 Builder-B 私有 worklog/inbox/outbox（反之亦然）。
- 在 worktree 内建共享上下文（导致共享事实被拆散成多份）。
- Lead 直接写业务代码绕过交接（除非用户明确授权）。
