# OpenClaw 本地部署说明

这个仓库已经接入了一个本地 OpenClaw agent，名字是 `video-agent-system`。

## 当前安装状态

- OpenClaw 跑在这台机器的 `WSL2 Ubuntu` 里。
- 专用项目 agent 的 id 是 `video-agent-system`。
- 它的工作区位于 `D:\agent\video-agent-system\.openclaw-workspace`。
- OpenClaw 控制面板地址是 `http://127.0.0.1:18789/`。

## 快速开始

打开本地控制台：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1
```

在 Windows 里通过 WSL 包装器执行 OpenClaw 命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agents list --json
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --agent video-agent-system --message "检查当前即梦工作流" --json
```

## 适合项目使用的命令

这些命令都适合让 OpenClaw 直接调用：

```powershell
python -m app.cli doctor
python -m app.cli test-asset-planner
python -m app.cli test-prompt-composer
python -m app.cli run --script-path tmp/episode7_shot_plan.json
python -m app.cli watch-jimeng-job
```

## 备注

- `.openclaw-workspace/` 已故意加入 `gitignore`，因为里面会保存本地状态和记忆文件。
- `scripts/openclaw-wsl.sh` 是 WSL 里的 OpenClaw 主启动脚本。
- `scripts/openclaw.ps1` 是 Windows 侧调用这个脚本的包装器。
- `scripts/openclaw_control_server.py` 会把 `openclaw-ui/` 目录作为本地控制台页面提供出来。
- 这台机器上如果 gateway websocket 暂时断开，`openclaw agent ...` 可能会自动回退到 embedded 模式，但命令仍然可以正常完成。
