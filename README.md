# video-agent-system

当前恢复版仓库已找回这些核心部分：

- `app/cli.py`：CLI 入口
- `app/feishu_sync/`：飞书素材读取与下载
- `app/asset_catalog/`：本地素材索引与搜索
- `app/openclaw/`：参考素材选择、提示词生成、场景锚点图
- `app/jimeng_operator/`：即梦网页自动化与 Gemini 审查
- `app/video_analyzer/`：抽帧与承接帧分析
- `scripts/manju_one_shot.py`：Manju 单任务脚本
- `tests/`：多组 CLI / 模块测试

本仓库是从已恢复文件重新拼回的工作目录，详细过程见 `RECOVERY_NOTES.md`。

## 当前状态

- 主体目录结构已恢复
- 多个核心模块源码已放回原位
- 一部分文件来自恢复副本重新组装
- 仍有少量晚期版本代码未完全找回，后续可继续补齐

## 基本命令

```bash
python -m app.cli --help
python -m app.cli doctor
pytest
```
