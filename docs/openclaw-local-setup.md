# OpenClaw Local Setup

This repository is wired to a local OpenClaw agent named `video-agent-system`.

## What is installed

- OpenClaw runs inside local `WSL2` Ubuntu on this machine.
- The dedicated project agent id is `video-agent-system`.
- Its workspace lives at `D:\agent\video-agent-system\.openclaw-workspace`.
- The OpenClaw dashboard is exposed at `http://127.0.0.1:18789/`.

## Quick start

Open the local control panel:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1
```

Run OpenClaw commands from Windows through the WSL wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agents list --json
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --agent video-agent-system --message "Check the current Jimeng workflow" --json
```

## Project-friendly commands

Useful project commands that OpenClaw can call:

```powershell
python -m app.cli doctor
python -m app.cli test-asset-planner
python -m app.cli test-prompt-composer
python -m app.cli run --script-path tmp/episode7_shot_plan.json
python -m app.cli watch-jimeng-job
```

## Notes

- `.openclaw-workspace/` is intentionally git-ignored because it contains local state and memory files.
- `scripts/openclaw-wsl.sh` is the canonical OpenClaw launcher inside WSL.
- `scripts/openclaw.ps1` is the Windows wrapper for calling that launcher.
- `scripts/openclaw_control_server.py` serves the local control UI under `openclaw-ui/`.
- On this machine, `openclaw agent ...` may fall back to embedded mode if the gateway websocket closes. The command still completes successfully.
