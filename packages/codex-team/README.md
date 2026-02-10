# codex-team package

Cross-platform CLI package for single-machine multi-role Codex collaboration.

Build:

```bash
npm install
npm run build
```

Run:

```bash
node dist/cli.js --help
```

Main commands:

```bash
node dist/cli.js init
node dist/cli.js up --layout quad --with-builder-b
node dist/cli.js send --to reviewer --type REVIEW --action "please review" --context "issue:demo"
node dist/cli.js broadcast --to builder-a,builder-b --type TASK --action "same task competition" --context "issue:demo"
node dist/cli.js watch --me reviewer --interval 2 --context "issue:demo"
node dist/cli.js thread --context "issue:demo"
node dist/cli.js done --latest --me reviewer --summary "done" --artifacts "decision.md"
node dist/cli.js auto --me builder-a --interval 3 --context "issue:demo"
node dist/cli.js orchestrate --context "issue:demo" --with-builder-b --interval 3
node dist/cli.js orchestrate --stop --context "issue:demo"
```

Recommended start flow:

```bash
node dist/cli.js init
node dist/cli.js orchestrate --context "issue:demo" --with-builder-b --interval 3
# lead sends tasks via send/broadcast
node dist/cli.js thread --context "issue:demo"
node dist/cli.js orchestrate --stop --context "issue:demo"
```
