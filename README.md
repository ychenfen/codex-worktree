# codex-worktree

跨平台（Windows + macOS）单机多角色 Codex 协作工具。

## 当前状态

- 新路线：`packages/codex-team`（Node.js + TypeScript CLI）
- 旧原型：`scripts/*.ps1`（保留，不再继续扩展功能）

## 功能概览

`codex-team` 提供以下命令：

- `codex-team init`
  - 生成 `.codex-team/config.json`
  - 在 repo 外创建共享上下文目录（默认 `%USERPROFILE%\\codex-ctx` / `$HOME/codex-ctx`）
  - 初始化 `task.md` / `decision.md` / `verify.md` / `pitfalls.md` / `journal.md`
  - 创建 `bus/`、`logs/`
  - 在 repo 内生成 `roles/ROLE_*.md` 和 `templates/`

- `codex-team up [--layout quad] [--with-builder-b]`
  - 创建/检查 `main` 分支与角色 worktree
  - Windows: 使用 `wt` 四格启动 `codex`
  - macOS: 使用 iTerm2 AppleScript 四格启动 `codex`

- `codex-team send/inbox/done`
  - 基于 `ctx_dir/bus` 的文件总线互相调用与通知
  - 固定消息格式：
    - `From:`
    - `To:`
    - `Type:`
    - `Context:`
    - `Action:`
    - `Reply-to:`

## 安装与构建

```powershell
cd packages/codex-team
npm install
npm run build
```

构建后可用：

```powershell
node dist/cli.js --help
```

## 最小可用 Demo

### 1) init

```powershell
node packages/codex-team/dist/cli.js init
```

### 2) up

```powershell
node packages/codex-team/dist/cli.js up --layout quad
```

启用竞争式双 Builder：

```powershell
node packages/codex-team/dist/cli.js up --layout quad --with-builder-b
```

### 3) send

```powershell
node packages/codex-team/dist/cli.js send --to reviewer --type REVIEW --action "请审查 builder-a 的最小改动" --context "PR#12"
```

### 4) inbox

```powershell
node packages/codex-team/dist/cli.js inbox --me reviewer
```

### 5) done

```powershell
node packages/codex-team/dist/cli.js done --msg 20260210-1400_to_reviewer_REVIEW.md --summary "已完成审查，见 decision.md" --artifacts "decision.md"
```

## 协作协议（对话隔离）

- 所有角色共享 repo 外 `ctx_dir`
- 角色之间统一通过 `bus/` 消息文件通知，不靠聊天互 @
- 每次任务结束在 `journal.md` 追加总结（建议 <=20 行）
- 角色边界：
  - Lead：拆解/派工/验收/拍板，不写实现
  - Builder：只实现，不改任务边界
  - Reviewer：只审查，不做实现
  - Tester：只验证，不做实现

## Publishing

网络可用时：

```powershell
git add .
git commit -m "feat: add cross-platform codex-team cli"
git branch -M main
git push -u origin main
```

如果远端已有提交：

```powershell
git fetch origin
git rebase origin/main
```
