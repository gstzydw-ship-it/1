# 恢复说明

这个目录是从 `D:\` 根目录已恢复文件里重新拼出来的“恢复版项目目录”。

## 已放入主结构的文件

这些文件内容可直接读出，先按仓库结构放回：

- `app/cli.py` <- `D:\cli_48.py`
- `app/openclaw/models.py` <- `D:\models.py`
- `tests/test_orchestrator.py` <- `D:\test_orchestrator.py`

## 候选文件

这些文件也已复制进来，但目前放在 `recovered_candidates/` 下：

- 有些看起来属于本项目，但内容疑似损坏或编码异常
- 有些是 README/脚本/测试的候选版本，后续需要人工甄别

已复制的候选文件包括：

- `recovered_candidates/root_files/README_58.md`
- `recovered_candidates/root_files/.gitignore`
- `recovered_candidates/root_files/AGENTS.md`
- `recovered_candidates/root_files/Plan.md`
- `recovered_candidates/root_files/ManjuStrategy.md`
- `recovered_candidates/scripts/manju_one_shot.py`
- `recovered_candidates/app/web_operator.py`
- `recovered_candidates/app/gemini_audit.py`
- `recovered_candidates/app/service_34.py`
- `recovered_candidates/app/service_35.py`
- `recovered_candidates/app/service_36.py`
- `recovered_candidates/app/service_39.py`
- `recovered_candidates/tests/` 下的各个测试文件

## 当前状态

- 已经有一个可继续整理的恢复目录
- 但这还不是完整仓库
- 后续需要继续从 `recovered_candidates/` 里筛文件，或者继续用恢复工具补找
