# Autopilot (mac)

目标：让 Lead/Builder/Reviewer/Tester **自动接收消息 -> 自动执行 -> 自动回写**，同时保证消息互通且不冲突。

## 设计原则
- **互通**：所有动作通过“消息总线”路由（见 `docs/bus.md`），角色之间可以互发任务/问题。
- **不冲突**：每条消息一个文件（原子 rename），处理时目录锁 + done 哨兵（幂等）。
- **无人值守**：每个角色是一个长期运行的 worker，自动从队列取消息并调用 `codex exec` 执行。

## 前置条件
- macOS + git
- 已登录 Codex CLI（能运行 `codex exec ...`）
- 会话已创建（推荐带 worktrees）：
  - `pwsh ./scripts/new-session.ps1 -SessionName <id> -CreateWorktrees`
  - 或（无需 PowerShell）：`./scripts/new-session.sh <id> --create-worktrees`
- 可选：创建会话时投递 lead bootstrap 消息：
  - `pwsh ./scripts/new-session.ps1 -SessionName <id> -CreateWorktrees -BootstrapBus`
  - 或（无需 PowerShell）：`./scripts/new-session.sh <id> --create-worktrees --bootstrap-bus`

## 自定义默认模型（例如 GLM）

你这次遇到的报错 `The model ... does not exist or you do not have access to it.` 本质是 `~/.codex/config.toml` 里默认 `model` 配了不可用的值。

本仓库的 Autopilot 已做了兜底：会自动选一个你当前账号“确实可用”的 Codex 模型；但如果你希望默认走自定义模型（例如 `glm5`），推荐在 `~/.codex/config.toml` 配置 `model_provider` + `model_providers.<id>`。

重要安全提示：
- 不要把 API key 写进仓库文件或提交到 git。
- 建议只用环境变量提供鉴权 header（见 `env_http_headers`），并尽快轮换已泄露的 key。

示例（OpenAI-compatible 的 `chat` 接口，供参考；`base_url` 按你自己的网关/厂商接口调整）：

```toml
model = "glm5"
model_provider = "glm"

[model_providers.glm]
name = "GLM"
wire_api = "chat"
base_url = "https://YOUR_OPENAI_COMPATIBLE_BASE_URL"
env_http_headers = { Authorization = "GLM_AUTH" }
env_key_instructions = "export GLM_AUTH='Bearer <YOUR_KEY>'"
```

如果只想临时覆盖模型，不改全局 config：

```bash
./scripts/autopilot.sh start <session-id> 2 --model gpt-5.2-codex
```

## 文件约定
- 消息队列：
  - `sessions/<id>/bus/inbox/<role>/*.md`
  - `sessions/<id>/bus/outbox/*.md`（回执）
  - `sessions/<id>/bus/deadletter/<role>/*.md`
- 幂等状态：
  - `sessions/<id>/state/processing/`
  - `sessions/<id>/state/done/`
  - `sessions/<id>/state/archive/<role>/`
  - `sessions/<id>/state/tasks/tasks.json`（任务状态机）
- Autopilot 状态：
  - `sessions/<id>/artifacts/autopilot/`

## 启动 Autopilot

启动全部角色守护进程：

```bash
./scripts/autopilot.sh start <session-id>
```

macOS 下默认会使用 `kqueue` 监听 inbox/outbox 目录变化以降低延迟；如需禁用（兼容性排查）：

```bash
export AUTOPILOT_USE_KQUEUE=0
export ROUTER_USE_KQUEUE=0
```

默认并行执行（更接近 Claude Code team 模式）。如需保守串行（全局锁）：

```bash
./scripts/autopilot.sh start <session-id> 2 --serial
```

如需强制指定模型（当 `~/.codex/config.toml` 里配置了不可用 model 时）：

```bash
./scripts/autopilot.sh start <session-id> 2 --model gpt-5.2-codex
```

也可用 `--dry-run` 验证队列/锁/回执链路（不调用模型）：

```bash
./scripts/autopilot.sh start <session-id> 2 --dry-run
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
- `lead` 收到 `intent: bootstrap` 时会先走确定性任务规划：
  - 读取 `shared/task.md`
  - 自动创建 `state/tasks/tasks.json` 任务图（implement -> review/test 依赖）
  - 自动派发当前可执行任务（通常先到 builder）
- 当某个 `task_id` 完成后，Autopilot 会自动派发被依赖解锁的后续任务。

## 如何投递消息（不需要手动输入到终端里）

你可以直接把消息文件写到 `bus/inbox/<role>/`（见 `docs/bus.md`），或用脚本生成：

```bash
./scripts/bus-send.sh --session <id> --from lead --to builder-a --intent implement --task <task_id> --message "..." --accept "..." --risk medium
```

任务状态查看（对标 Team 的 pending/in_progress/completed）：

```bash
python3 ./scripts/tasks.py list --session <id>
```

## 注意
- 如果 `shared/task.md` 目标/验收为空，Lead worker 可能只能写 “blocked” 并请求澄清。
- 正式交付仍以 outbox/decision/verify 为准。
