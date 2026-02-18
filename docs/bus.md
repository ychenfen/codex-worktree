# Message Bus Protocol

目标：让多角色在 mac 上 **消息互通、无冲突、无人值守自动执行**。

## 目录约定（每个会话）

以 `sessions/<sid>/` 为例：

- `bus/inbox/<role>/`：该角色待处理消息（队列）。
- `bus/outbox/`：角色处理回执（给其它角色/Lead 归档与追踪）。
- `bus/deadletter/<role>/`：超过最大重试次数仍失败的消息。

- `state/processing/`：处理中锁（目录锁，避免重复执行）。
- `state/done/`：完成哨兵（幂等）。
- `state/archive/<role>/`：已处理消息归档。
- `state/tasks/tasks.json`：任务状态机（`pending / in_progress / completed / failed` + `depends_on`）。

- `artifacts/locks/`：全局锁（串行化执行，避免共享文件冲突）。

## 消息文件（一个消息一个文件）

消息文件放在 `bus/inbox/<role>/`，必须包含 YAML frontmatter：

```md
---
id: 20260212-235959-acde12
from: lead
to: builder-a
intent: implement
thread: <sid>
risk: low
task_id: T20260216-113245-e34a71
acceptance:
  - "..."
  - "..."
---
正文：具体要求、上下文、限制、验证方式……
```

规则：
- `id` 必须全局唯一（同会话内）。
- worker 只处理 `to == <role>` 的消息。
- 推荐带 `task_id`，让消息与任务状态机一一对应（便于 claim/完成判定）。

## 任务状态机（Task Board）

对标 team 模式的最小任务模型：
- `pending`：待领取
- `in_progress`：已领取执行中
- `completed`：已完成
- `failed`：达到重试上限或终止

依赖关系：
- `depends_on` 中的任务必须全部 `completed`，当前任务才能被 claim。

CLI：

```bash
python3 ./scripts/tasks.py list --session <sid>
python3 ./scripts/tasks.py add --session <sid> --title "..." --owner builder-a --accept "pytest -q"
python3 ./scripts/tasks.py claim --session <sid> --role builder-a --task <task_id>
python3 ./scripts/tasks.py complete --session <sid> --role builder-a --task <task_id>
```

## 幂等与无冲突

- **无冲突写入**：消息写入采用“写临时文件 -> rename”为原子操作。
- **单消息单执行**：worker 处理消息时创建 `state/processing/<id>.<role>.lockdir`（mkdir 成功即获得锁）。
- **完成哨兵**：成功后写入 `state/done/<id>.<role>.ok`，并把原消息移动到 `state/archive/<role>/`。

## 回执（Receipt）

worker 处理完成后写回执到 `bus/outbox/<id>.<role>.md`：

```md
---
id: 20260212-235959-acde12
role: builder-a
thread: "demo"
request_from: "lead"
request_to: "builder-a"
request_intent: "implement"
status: done
codex_rc: 0
finished_at: "2026-02-12 23:59:59"
task_id: "T20260216-113245-e34a71"
---
<codex 最后一条消息（或摘要）>
```

## Router（回执转发）

为实现“消息互通而不冲突”，建议启用 Router：

- Router 轮询 `bus/outbox/`，把新的/更新后的回执转发成 **新的 bus 消息**：
  - 发送到 `lead`（默认收敛者）
  - 同时发送到 `request_from`（如果它是有效角色）
- 这样 Lead/Requester 不需要手动去读 `outbox/`，也不需要共享文件抢写。

mac 上用 `./scripts/autopilot.sh start <sid>` 会自动启动 router 守护进程。

## 路由指令（对标 team 模式：自动接力派工）

为了做到“收到消息就自己执行，不需要人手动输入”，worker 可以在 **最终输出** 里追加指令，Router 会自动把它们转成 bus 消息投递到目标角色 inbox：

```text
::bus-send{to="reviewer" intent="review" risk="low" message="请评审这次改动：..."}
::bus-send{to="tester" intent="test" risk="low" message="请验收：..." accept="pytest -q"}
::bus-send{to="builder-a" intent="fix" risk="high" message="必改：..." accept="pytest -q"}
```

说明：
- 指令只在回执里生效（即 worker 完成一次处理后写入 `bus/outbox` 的最后输出）。
- Router 会对回执做 hash 去重，避免重复派工。
- 保护规则：非 Lead 角色默认不允许派发 `intent="implement"`（避免无限扩散的“自生任务”）。

## Autopilot

Autopilot 守护进程会轮询 `bus/inbox/<role>/`：
- 有消息则自动调用 `codex exec` 执行；
- 失败会重试，超过次数进入 `deadletter/`；
- 默认并行执行（更接近 Claude Code team 模式）；如需保守串行可启用全局锁：
  - `./scripts/autopilot.sh start <sid> 2 --serial`
  - 或设置环境变量：`export AUTOPILOT_GLOBAL_LOCK=1`

补充（任务状态机模式）：
- `lead/bootstrap` 会自动把 `shared/task.md` 变成任务图并写入 `state/tasks/tasks.json`。
- 任务完成时会自动检查 `depends_on`，把新解锁的任务自动投递到对应 `bus/inbox/<owner>/`。
