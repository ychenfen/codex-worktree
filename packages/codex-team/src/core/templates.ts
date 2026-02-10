import path from "node:path";
import { Role, ROLES } from "../types.js";
import { roleFileName } from "../utils/paths.js";
import { writeTextFile } from "../utils/fs.js";

function rolePrompt(role: Role, ctxDir: string): string {
  const shared = `${ctxDir} (task.md / decision.md / verify.md / pitfalls.md / journal.md / bus/)`;

  const common = [
    `# ${role.toUpperCase()} Role Prompt`,
    "",
    "共享上下文目录（repo 外）：",
    `- ${shared}`,
    "",
    "沟通规则：",
    "- 只通过 bus/ 消息文件互相通知，不在聊天里直接 @ 其他角色。",
    "- 每个任务完成后，在 journal.md 追加最多 20 行总结。",
    "",
  ];

  switch (role) {
    case "lead":
      return [
        ...common,
        "职责边界：",
        "- 只做拆解、派工、验收和最终拍板。",
        "- 禁止直接实现业务代码。",
      ].join("\n");
    case "builder-a":
    case "builder-b":
      return [
        ...common,
        "职责边界：",
        "- 只做实现和自测，结果写入 verify.md。",
        "- 禁止修改 task.md 目标与范围。",
      ].join("\n");
    case "reviewer":
      return [
        ...common,
        "职责边界：",
        "- 只做代码审查与风险评估，结论写 decision.md。",
        "- 禁止实现业务功能。",
      ].join("\n");
    case "tester":
      return [
        ...common,
        "职责边界：",
        "- 只做验证/回归，结论写 verify.md。",
        "- 禁止实现业务功能。",
      ].join("\n");
    default:
      return common.join("\n");
  }
}

export function writeRolePrompts(repoRoot: string, ctxDir: string): string[] {
  const rolesDir = path.join(repoRoot, "roles");
  const created: string[] = [];

  for (const role of ROLES) {
    const filePath = path.join(rolesDir, roleFileName(role));
    writeTextFile(filePath, rolePrompt(role, ctxDir) + "\n");
    created.push(filePath);
  }

  return created;
}

export function writeRepoTemplates(repoRoot: string): string[] {
  const templatesDir = path.join(repoRoot, "templates");

  const files: Array<{ name: string; content: string }> = [
    {
      name: "task-card.md",
      content: [
        "# Task Card",
        "",
        "- 目标：",
        "- 背景：",
        "- 约束：",
        "- 可改动范围：",
        "- 禁止改动范围：",
        "",
        "## 验收标准",
        "1. ",
        "2. ",
      ].join("\n"),
    },
    {
      name: "message.md",
      content: [
        "From:",
        "To:",
        "Type: TASK|REVIEW|VERIFY|BLOCKER|FYI",
        "Context:",
        "Action:",
        "Reply-to:",
      ].join("\n"),
    },
    {
      name: "review-template.md",
      content: [
        "# Review Template",
        "",
        "- 合并建议：合并 / 不合并 / 需修改后合并",
        "- 必改项：",
        "- 风险点：",
        "- 可选改进：",
      ].join("\n"),
    },
    {
      name: "verify-template.md",
      content: [
        "# Verify Template",
        "",
        "- 验收命令：",
        "- 验证结果：通过 / 失败",
        "- 关键日志：",
        "- 覆盖范围：",
      ].join("\n"),
    },
  ];

  const created: string[] = [];
  for (const file of files) {
    const filePath = path.join(templatesDir, file.name);
    writeTextFile(filePath, file.content + "\n");
    created.push(filePath);
  }

  return created;
}
