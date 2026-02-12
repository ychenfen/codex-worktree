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
acceptance:
  - "..."
  - "..."
---
正文：具体要求、上下文、限制、验证方式……
```

规则：
- `id` 必须全局唯一（同会话内）。
- worker 只处理 `to == <role>` 的消息。

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
status: done
codex_rc: 0
finished_at: "2026-02-12 23:59:59"
---
<codex 最后一条消息（或摘要）>
```

## Autopilot

Autopilot 守护进程会轮询 `bus/inbox/<role>/`：
- 有消息则自动调用 `codex exec` 执行；
- 失败会重试，超过次数进入 `deadletter/`；
- 执行默认用全局锁串行化（稳，避免共享文件写冲突）。

