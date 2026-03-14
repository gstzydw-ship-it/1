# OpenClaw 本地部署说明

这个仓库已经接入了一个本地 OpenClaw agent，名字是 `video-agent-system`。

## 当前状态

- OpenClaw 跑在这台机器的 `WSL2 Ubuntu` 里。
- 项目专用 agent 的 id 是 `video-agent-system`。
- 工作区在 `D:\agent\video-agent-system\.openclaw-workspace`。
- 官方 Dashboard 地址是 `http://127.0.0.1:18789/`。
- 日常主入口建议用本地任务中心：`http://127.0.0.1:8765/`。

## 最推荐的使用方式

打开本地任务中心：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw-ui.ps1
```

现在最稳的路径是直接在本地任务中心里发任务，因为它默认走：

```powershell
openclaw agent --local ...
```

也就是直接使用 embedded 模式，不再先碰官方 gateway websocket。

本地任务中心现在负责这些事情：

- 看 OpenClaw、网关、工作区和 Git 分支状态。
- 发布中文任务给 `video-agent-system`。
- 自动记录最近任务、结果摘要和时间。
- 一键回填旧任务，或者直接重新发送。
- 打开视频输出目录。
- 在需要时复制官方 Dashboard 的带令牌链接。

## 常用命令

在 Windows 里通过包装脚本执行 OpenClaw：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 status
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agents list --json
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 agent --local --agent video-agent-system --message "检查当前即梦工作流" --json
```

## 处理官方 Dashboard 断开或 token 报错

如果官方 Dashboard 页面提示：

```text
unauthorized: gateway token missing
unauthorized: gateway token mismatch
disconnected (1006)
```

优先这样处理：

1. 回到本地任务中心。
2. 点“复制带令牌链接”。
3. 再点“打开官方 Dashboard”。

如果你更喜欢命令行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/openclaw.ps1 dashboard --no-open
```

这条命令会把带当前 token 的 Dashboard 链接复制到剪贴板。

## 适合本项目的命令

```powershell
python -m app.cli doctor
python -m app.cli test-asset-planner
python -m app.cli test-prompt-composer
python -m app.cli run --script-path tmp/episode7_shot_plan.json
python -m app.cli watch-jimeng-job
```

## 备注

- `.openclaw-workspace/` 已加入 `.gitignore`，因为里面会保存本地状态和记忆文件。
- `scripts/openclaw-wsl.sh` 是 WSL 里的 OpenClaw 启动脚本。
- `scripts/openclaw.ps1` 是 Windows 侧调用 OpenClaw 的包装器。
- `scripts/openclaw_control_server.py` 会把 `openclaw-ui/` 目录作为本地任务中心提供出来。
- 官方 Dashboard 在这台机器上不是最稳定的主入口，所以建议把它当辅助页面，而不是日常发任务的入口。
