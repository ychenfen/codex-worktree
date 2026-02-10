# codex-worktree

跨平台（Windows + macOS）单机多角色 Codex 协作工具。

## 当前状态

- 新路线：`packages/codex-team`（Node.js + TypeScript CLI）
- 旧原型：`scripts/*.ps1`（保留，不再继续扩展功能）

## 命令总览

- `codex-team init`
- `codex-team up [--layout quad] [--with-builder-b]`
- `codex-team send`
- `codex-team broadcast`
- `codex-team inbox`
- `codex-team watch`
- `codex-team thread`
- `codex-team done`（支持 `--msg` 或 `--latest/--oldest --me`）

消息类型：`TASK|REVIEW|VERIFY|BLOCKER|FYI|PROPOSE|COMPARE`

## 安装与构建

```powershell
cd packages/codex-team
npm install
npm run build
cd ..\..
```

查看帮助：

```powershell
node .\packages\codex-team\dist\cli.js --help
```

## 最小可用 Demo（全一行）

1. 初始化（共享 ctx 在 repo 外）：

```powershell
node .\packages\codex-team\dist\cli.js init --ctx-dir C:\Users\<you>\codex-ctx
```

2. 启动四窗口（可选双 Builder）：

```powershell
node .\packages\codex-team\dist\cli.js up --layout quad --with-builder-b
```

3. 发送单条消息：

```powershell
node .\packages\codex-team\dist\cli.js send --to reviewer --type REVIEW --action "请审查最小改动" --context "issue:login-retry" --from lead
```

4. 广播派工（A/B 同时接任务）：

```powershell
node .\packages\codex-team\dist\cli.js broadcast --to builder-a,builder-b --type TASK --action "同任务双方案竞争" --context "issue:login-retry" --from lead
```

5. 实时监听（每个角色建议开一个 watch）：

```powershell
node .\packages\codex-team\dist\cli.js watch --me reviewer --interval 2 --context "issue:login-retry"
```

6. 查看线程（按 context 聚合）：

```powershell
node .\packages\codex-team\dist\cli.js thread --context "issue:login-retry"
```

7. 完成消息（指定文件）：

```powershell
node .\packages\codex-team\dist\cli.js done --msg 20260210-1529_to_reviewer_REVIEW.md --summary "审查完成" --artifacts "decision.md" --from reviewer
```

8. 完成消息（自动取最新，避免 `$msg`）：

```powershell
node .\packages\codex-team\dist\cli.js done --latest --me reviewer --summary "审查完成" --artifacts "decision.md" --from reviewer
```

## 四窗口到底有什么用

四窗口的价值不是“自动执行对方会话”，而是：

- 每个角色独立上下文，不互相污染
- 通过 `bus/` 文件总线进行可追溯协作
- `watch` 提供实时新消息通知
- `thread --context` 让竞争交流可回放、可裁决

## 推荐竞争协议（A/B）

- Lead 对 A/B 使用同一个 `--context` 派工
- A/B 互发 `REVIEW/COMPARE/PROPOSE`，必须带同一 `context`
- Reviewer 在同一 `context` 下输出对比结论
- Lead 以 `decision.md` 收敛，Tester 以 `verify.md` 验证

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
