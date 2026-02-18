# Ops Quick Runbook

## Minimal Start/Stop/Status

```bash
./scripts/autopilot.sh stop demo-team-20260213 || true
./scripts/autopilot.sh start demo-team-20260213 2 --model gpt-5.2-codex
./scripts/autopilot.sh status demo-team-20260213
```

## Trigger Bootstrap

```bash
./scripts/team.sh demo-team-20260213 <<'EOF'
/bootstrap
/exit
EOF
```

## One-Page Diagnostics

```bash
./scripts/diag.sh demo-team-20260213
```
