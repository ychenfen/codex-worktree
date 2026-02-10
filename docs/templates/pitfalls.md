# Pitfalls (Session: {{SESSION_ID}})

1. Git worktree 文件不共享，禁止把共享上下文放在 worktree 内。
2. 角色必须通过 inbox/outbox 交接，避免口头遗漏。
3. 没有验证证据的“完成”视为未完成。
4. 双 Builder 竞争时，不允许互相读取对方草稿以防方案污染。
