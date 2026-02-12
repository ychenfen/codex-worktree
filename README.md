# codex-worktree

一个面向 Codex CLI 的多角色并行协作模板，目标是把以下三件事做成可复用流程：

- 多角色并行（Lead / Builder-A / Reviewer / Tester，可选 Builder-B 竞争）
- 对话隔离（每个角色独立 inbox/outbox/worklog，禁止直接串线）
- 全程 Markdown 留痕（任务、决策、验证、日志都可审计）

## 为什么这版更稳

原始做法里最常见问题是：`git worktree` 彼此文件不共享，导致所谓 `ctx/*.md` 实际被拆成多份，角色无法正确交接。

这版默认采用 **会话中心目录**：

- `sessions/<session-id>/shared/*`：共享事实来源（task/decision/verify/pitfalls/journal）
- `sessions/<session-id>/roles/<role>/*`：角色专属收件箱、发件箱、工作日志
- 角色只通过 Markdown 文件交接，不靠口头上下文
- 对标 Claude Code 的团队分类模式：先分类路由再实现交付（见 `docs/team-mode.md`）

## 快速开始

前置条件：
- 安装 Git（用于 `git worktree`）。
- 安装 PowerShell 7（命令为 `pwsh`，Windows/macOS/Linux 均可）。
  - mac 上如果不想装 `pwsh`，可用 `./scripts/new-session.sh ...` 创建会话（功能对齐：模板复制 + bus/state + 可选 worktrees + 可选 bootstrap）。
- 脚本会自动定位到“主 worktree”（`git worktree list` 的第一项），所以你可以在任意角色 worktree 内运行 `scripts/*.ps1`，会话目录仍会落在主 worktree 的 `sessions/` 下。

1. 在仓库根目录创建会话

```powershell
pwsh ./scripts/new-session.ps1 -SessionName feature-login -CreateWorktrees
```

mac/Linux（无需 PowerShell）：

```bash
./scripts/new-session.sh feature-login --create-worktrees
```

2. 如果要双 Builder 竞争方案

```powershell
pwsh ./scripts/new-session.ps1 -SessionName feature-login -CreateWorktrees -WithBuilderB
```

mac/Linux（无需 PowerShell）：

```bash
./scripts/new-session.sh feature-login --create-worktrees --with-builder-b
```

3. 打开会话指引

```powershell
Get-Content ./sessions/feature-login/SESSION.md
```

`SESSION.md` 里会给出：

- 每个角色建议使用的 worktree 路径
- 每个角色要粘贴的 prompt 文件路径
- 日志记录命令样例

## 目录结构

```text
.
├─ docs
│  ├─ prompts
│  ├─ templates
│  ├─ protocol.md
│  └─ task-journal.md
├─ scripts
│  ├─ new-session.ps1
│  ├─ dispatch.ps1
│  ├─ log-entry.ps1
│  └─ check-session.ps1
└─ sessions
   └─ <session-id>
      ├─ SESSION.md
      ├─ shared
      │  ├─ task.md
      │  ├─ decision.md
      │  ├─ verify.md
      │  ├─ pitfalls.md
      │  └─ journal.md
      └─ roles
         └─ <role>
            ├─ prompt.md
            ├─ inbox.md
            ├─ outbox.md
            └─ worklog.md
```

## 协作最小闭环

1. Lead 用 `dispatch.ps1` 派工到某个角色 `inbox.md`
2. Builder 实现后写 `outbox.md` + `shared/verify.md`
3. Reviewer 根据 diff 和 outbox 写 `shared/decision.md`
4. Tester 复验并更新 `shared/verify.md`
5. Lead 收敛最终决策并在 `shared/journal.md` 记录结论

## 常用命令

创建会话：

```powershell
pwsh ./scripts/new-session.ps1 -SessionName fix-uds-timeout -CreateWorktrees -WithBuilderB
```

无人值守（可选）：创建时投递一条 lead bootstrap 消息（让 Lead 自动拆解并派工）：

```powershell
pwsh ./scripts/new-session.ps1 -SessionName fix-uds-timeout -CreateWorktrees -BootstrapBus
```

Lead 派工：

```powershell
pwsh ./scripts/dispatch.ps1 -SessionName fix-uds-timeout -Role builder-a -Message "修复 UDS 超时重试" -Acceptance "测试用例 test_uds_retry 通过"
```

任意角色追加日志：

```powershell
pwsh ./scripts/log-entry.ps1 -SessionName fix-uds-timeout -Role builder-a -Channel worklog -Status doing -Message "开始实现重试逻辑" -Evidence "src/uds/client.py"
```

会话健康检查：

```powershell
pwsh ./scripts/check-session.ps1 -SessionName fix-uds-timeout
```

角色对话（mac 推荐，写入 `shared/chat/messages/*.md`，避免并发冲突）：

```powershell
pwsh ./scripts/chat.ps1 -SessionName fix-uds-timeout -Role builder-a -Message "我打算改 scripts/new-session.ps1 的路径拼接，这样 OK 吗？" -Mention reviewer
```

或无需 PowerShell（mac/Linux）：

```bash
./scripts/chat.sh fix-uds-timeout builder-a "我打算改 scripts/new-session.ps1 的路径拼接，这样 OK 吗？" reviewer
```

渲染对话为可读线程：

```bash
./scripts/render-chat.sh fix-uds-timeout
```

Autopilot（mac，无人值守多角色执行）：

```bash
./scripts/autopilot.sh start fix-uds-timeout
```

说明：
- 会同时启动 `router`，把 `bus/outbox` 回执自动转发为 `bus/inbox` 消息（Lead/Requester 自动收到进展，无需手动查看 outbox）。
- 如果你配置了自定义默认模型/提供商（例如 GLM），并且遇到默认模型不可用，见 `docs/autopilot-mac.md` 的“自定义默认模型”。

投递一条任务给某角色（消息总线，不冲突、可追踪）：

```bash
./scripts/bus-send.sh --session fix-uds-timeout --from lead --to builder-a --intent implement --message "实现 xxx" --accept "pytest -q" --risk medium
```

Claude Code 风格的“对话式终端入口”（mac）：

```bash
./scripts/team.sh demo-team-20260213
```

## 对话隔离规则（强约束）

- Builder-A 不读取 Builder-B 的 `inbox/outbox/worklog`，反之亦然。
- Reviewer/Tester 不改业务代码，只写评审/验证结果。
- Lead 不直接实现业务代码，只拆解、派工、收敛。
- 共享结论仅以 `shared/*.md` 为准，口头消息不算。

## 我建议的增强实践

- 每个会话单独 `session-id`，不要复用旧目录。
- 每条关键动作都写日志，并附证据（命令、文件、测试结果）。
- 竞争式双 Builder 时，用统一对比表收敛：改动面、风险、可测试性、回滚难度、维护成本。

## 上传到 GitHub

网络可用时执行：

```powershell
git add .
git commit -m "feat: add isolated multi-role codex workflow"
git remote add origin https://github.com/ychenfen/codex-worktree.git  # 已存在则跳过
git push -u origin HEAD
```

如果远端已有历史，请先拉取并 rebase（把 `<default-branch>` 替换成远端默认分支，通常是 `main` 或 `master`）：

```powershell
git fetch origin
git rebase origin/<default-branch>
```
