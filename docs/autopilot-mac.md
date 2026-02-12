# Autopilot (mac)

目标：让 Lead/Builder/Reviewer/Tester **自动接收消息 -> 自动执行 -> 自动回写**，同时保证消息互通且不冲突。

## 设计原则
- **互通**：所有动作通过“消息总线”路由（见 `docs/bus.md`），角色之间可以互发任务/问题。
- **不冲突**：每条消息一个文件（原子 rename），处理时目录锁 + done 哨兵（幂等）。
- **无人值守**：每个角色是一个长期运行的 worker，自动从队列取消息并调用 `codex exec` 执行。

## 前置条件
- macOS + git
- 已登录 Codex CLI（能运行 `codex exec ...`）
- 会话已创建（推荐带 worktrees）：`pwsh ./scripts/new-session.ps1 -SessionName <id> -CreateWorktrees`
- 可选：创建会话时投递 lead bootstrap 消息：`pwsh ./scripts/new-session.ps1 -SessionName <id> -CreateWorktrees -BootstrapBus`

## 文件约定
- 消息队列：
  - `sessions/<id>/bus/inbox/<role>/*.md`
  - `sessions/<id>/bus/outbox/*.md`（回执）
  - `sessions/<id>/bus/deadletter/<role>/*.md`
- 幂等状态：
  - `sessions/<id>/state/processing/`
  - `sessions/<id>/state/done/`
  - `sessions/<id>/state/archive/<role>/`
- Autopilot 状态：
  - `sessions/<id>/artifacts/autopilot/`

## 启动 Autopilot

启动全部角色守护进程：

```bash
./scripts/autopilot.sh start <session-id>
```

说明：
- 会同时启动 `router` 守护进程，用于把 `bus/outbox/*.md` 回执转发为 `bus/inbox/*/*.md` 消息（Lead/Requester 自动收到进展）。

查看状态：

```bash
./scripts/autopilot.sh status <session-id>
```

停止：

```bash
./scripts/autopilot.sh stop <session-id>
```

## 触发规则（简化）
- 只要 `bus/inbox/<role>/` 里有消息，worker 就会自动取出并执行。

## 如何投递消息（不需要手动输入到终端里）

你可以直接把消息文件写到 `bus/inbox/<role>/`（见 `docs/bus.md`），或用脚本生成：

```bash
./scripts/bus-send.sh --session <id> --from lead --to builder-a --intent implement --message "..." --accept "..." --risk medium
```

## 注意
- 如果 `shared/task.md` 目标/验收为空，Lead worker 可能只能写 “blocked” 并请求澄清。
- 正式交付仍以 outbox/decision/verify 为准。
