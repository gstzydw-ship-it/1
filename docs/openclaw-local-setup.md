# OpenClaw 本地部署说明

这个仓库已经接入了一个本地 OpenClaw agent，名字是 `video-agent-system`。

## 当前安装状态

- OpenClaw 跑在这台机器的 `WSL2 Ubuntu` 里。
- 项目专用 agent 的 id 是 `video-agent-system`。
- 它的工作区在 `D:\agent\video-agent-system\.openclaw-workspace`。
- 官方 Dashboard 地址是 `http://127.0.0.1:18789/`。
- 日常主入口建议用本地控制台：`http://127.0.0.1:8765/`。

## 快速开始

打开本地控制台：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1
```

默认建议直接使用本地控制台，因为它已经把最常用的动作集中起来了：

- 状态总览：看 OpenClaw、网关、工作区和 Git 分支。
- 快捷操作：跑项目体检、提示词测试、视频输出目录和 Dashboard 修复。
- Agent 控制区：直接给 `video-agent-system` 发送中文任务。
- 最近视频、常用命令和运行日志：都在同一页里。

在 Windows 里通过包装脚本执行 OpenClaw 命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agents list --json
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --agent video-agent-system --message "检查当前即梦工作流" --json
```

## 处理 token mismatch

如果官方 Dashboard 页面提示：

```text
unauthorized: gateway token mismatch
```

优先用下面这两种方法修：

1. 在本地控制台里点“复制带令牌链接”，再用新的链接重新打开页面。
2. 或者直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 dashboard --no-open
```

这条命令会把带当前 token 的 Dashboard 链接复制到剪贴板。

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

- `.openclaw-workspace/` 已加入 `.gitignore`，因为里面会保存本地状态和记忆文件。
- `scripts/openclaw-wsl.sh` 是 WSL 里的 OpenClaw 主启动脚本。
- `scripts/openclaw.ps1` 是 Windows 侧调用 OpenClaw 的包装器。
- `scripts/openclaw_control_server.py` 会把 `openclaw-ui/` 目录作为本地控制台页面提供出来。
- 官方 Dashboard 仍然可用，但在这台机器上偶尔会遇到 Windows 侧直连不稳定或 token 不一致，所以日常建议优先用本地控制台。
- 这台机器上如果 gateway websocket 暂时断开，`openclaw agent ...` 可能会自动回退到 embedded 模式，但命令通常仍能完成。
