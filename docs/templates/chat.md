# Shared Chat (Session: {{SESSION_ID}})

用途：角色间快速澄清与问答（非正式）。正式交付仍以 inbox/outbox/decision/verify 为准。

## Messages directory

- `sessions/{{SESSION_ID}}/shared/chat/messages/`：每条消息一个文件（避免并发写冲突）。
- `shared/chat.md` 可用 `scripts/render-chat.sh` 或 `scripts/render-chat.ps1` 重新渲染为可读线程。

## Thread (rendered)

- 暂无消息（请运行 render-chat 脚本渲染）
