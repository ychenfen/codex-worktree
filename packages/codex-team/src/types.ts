export const ROLES = ["lead", "builder-a", "reviewer", "tester", "builder-b"] as const;
export type Role = (typeof ROLES)[number];

export const MESSAGE_TYPES = ["TASK", "REVIEW", "VERIFY", "BLOCKER", "FYI", "PROPOSE", "COMPARE"] as const;
export type MessageType = (typeof MESSAGE_TYPES)[number];

export interface WorktreeSpec {
  role: Role;
  name: string;
  branch: string;
}

export interface CodexTeamConfig {
  version: number;
  createdAt: string;
  repoRoot: string;
  ctxDir: string;
  busDir: string;
  logsDir: string;
  defaultLayout: "quad";
  defaultModel: string;
  worktrees: WorktreeSpec[];
}

export interface UpOptions {
  layout: "quad";
  withBuilderB: boolean;
}

export interface SendOptions {
  to: Role;
  type: MessageType;
  action: string;
  context: string;
  replyTo: string;
  from: string;
}
