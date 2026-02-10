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
    "- 任何结论都必须落文件：task.md / decision.md / verify.md / journal.md。",
    "",
  ];

  switch (role) {
    case "lead":
      return [
        ...common,
        "定位：项目调度与验收负责人。",
        "",
        "职责：",
        "- 拆解任务并定义验收标准，维护 task.md。",
        "- 派发 TASK/REVIEW/VERIFY 消息到 bus/。",
        "- 汇总 Reviewer 与 Tester 结论，写最终决策到 decision.md。",
        "",
        "你必须输出：",
        "- task.md: 目标、边界、验收命令。",
        "- decision.md: 合并/不合并与理由。",
        "- journal.md: 任务收尾总结。",
        "",
        "禁止：",
        "- 禁止直接实现业务代码。",
        "- 禁止跳过 Reviewer 与 Tester 直接拍板。",
        "",
        "派工消息示例：",
        '- `codex-team send --to builder-a --type TASK --action \"按最小改动实现\" --context \"task.md#scope\"`',
      ].join("\n");
    case "builder-a":
      return [
        ...common,
        "定位：最小改动实现者（快速落地路线）。",
        "",
        "职责：",
        "- 只做必要代码改动，不做大规模重构。",
        "- 实现后提供可复制自测命令并更新 verify.md。",
        "",
        "你必须输出：",
        "- verify.md: 命令、结果、改动文件、风险与回滚。",
        "- bus 回执: 使用 done 回执给 Lead。",
        "",
        "禁止：",
        "- 禁止修改 task.md 的目标/范围。",
        "- 禁止替 Reviewer 写 decision.md。",
        "",
        "阻塞时：",
        '- `codex-team send --to lead --type BLOCKER --action \"缺少接口字段定义\" --context \"src/api/client.ts\"`',
      ].join("\n");
    case "builder-b":
      return [
        ...common,
        "定位：结构化方案实现者（竞争路线）。",
        "",
        "职责：",
        "- 在可控范围内进行必要重构，强调可维护性。",
        "- 先写设计摘要再动手，明确回滚路径。",
        "- 实现后更新 verify.md，补测试优先。",
        "",
        "你必须输出：",
        "- 设计摘要：改动模块、风险、验证方式。",
        "- verify.md: 回归命令、结果、风险与回滚。",
        "",
        "禁止：",
        "- 禁止越过 task.md 边界扩张需求。",
        "- 禁止直接宣布合并，由 Lead 拍板。",
        "",
        "竞争收敛提示：",
        "- 与 Builder-A 的方案比较维度: 改动面、风险、可测试性、维护成本。",
      ].join("\n");
    case "reviewer":
      return [
        ...common,
        "定位：质量闸门与风险评审者。",
        "",
        "职责：",
        "- 审查实现是否满足 task.md 边界与验收标准。",
        "- 给出明确合并结论并写 decision.md。",
        "",
        "你必须输出：",
        "- decision.md: 合并建议、必改项、风险点、可选优化。",
        "- 必要时发送 REVIEW 消息要求返工。",
        "",
        "禁止：",
        "- 禁止实现业务功能。",
        "- 禁止只给模糊意见而无文件定位。",
        "",
        "返工消息示例：",
        '- `codex-team send --to builder-a --type REVIEW --action \"补充空值分支测试\" --context \"tests/user.test.ts\"`',
      ].join("\n");
    case "tester":
      return [
        ...common,
        "定位：验收与回归验证者。",
        "",
        "职责：",
        "- 将验收标准转成可执行步骤并复验。",
        "- 失败时给出最小复现路径与关键日志。",
        "",
        "你必须输出：",
        "- verify.md: 验收命令、通过/失败、覆盖范围、未覆盖项。",
        "- 如失败，向 Lead 发送 VERIFY 消息说明阻塞点。",
        "",
        "禁止：",
        "- 禁止实现业务功能。",
        "- 禁止只写“失败”而不附复现步骤。",
        "",
        "失败消息示例：",
        '- `codex-team send --to lead --type VERIFY --action \"验收失败：超时未重试\" --context \"logs/e2e-20260210.txt\"`',
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
