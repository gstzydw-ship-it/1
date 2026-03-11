"""视频抽帧辅助工具。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


_KNOWN_FFMPEG_PATHS = (
    r"C:\WeGameApps\英雄联盟\Cross\icreate\recorder-release\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
)


def find_ffmpeg_executable() -> str:
    """定位可用的 ffmpeg 可执行文件。"""

    env_path = os.getenv("FFMPEG_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered

    for candidate in _KNOWN_FFMPEG_PATHS:
        if Path(candidate).exists():
            return candidate

    raise RuntimeError("未找到可用的 ffmpeg。请先安装 ffmpeg，或通过 FFMPEG_PATH 指定路径。")


def extract_transition_frame(video_path: Path, timestamp_seconds: float, output_path: Path) -> Path:
    """从视频中抽取指定时间点的一帧，供 `@TransitionFrame` 使用。"""

    ffmpeg_path = find_ffmpeg_executable()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{timestamp_seconds:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-update",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not output_path.exists():
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown ffmpeg error"
        raise RuntimeError(f"抽取承接帧失败: {stderr}")
    return output_path


def get_video_duration_seconds(video_path: Path) -> float:
    """读取视频时长，供自动审查抽取关键帧。"""

    ffmpeg_path = find_ffmpeg_executable()
    command = [
        ffmpeg_path,
        "-i",
        str(video_path),
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    output = "\n".join(filter(None, [completed.stdout, completed.stderr]))
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        raise RuntimeError("无法读取视频时长，ffmpeg 输出中未找到 Duration。")

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def extract_review_frames(video_path: Path, output_dir: Path, frame_count: int = 3) -> list[Path]:
    """为 Gemini 自动审查抽取少量关键帧。"""

    duration = max(get_video_duration_seconds(video_path), 0.3)
    output_dir.mkdir(parents=True, exist_ok=True)

    if frame_count <= 1:
        fractions = [0.5]
    elif frame_count == 2:
        fractions = [0.33, 0.72]
    else:
        fractions = [0.20, 0.50, 0.80][:frame_count]
        if frame_count > 3:
            step = 0.60 / (frame_count - 1)
            fractions = [0.20 + step * index for index in range(frame_count)]

    extracted_paths: list[Path] = []
    for index, fraction in enumerate(fractions, start=1):
        timestamp = min(max(duration * fraction, 0.10), max(duration - 0.05, 0.10))
        output_path = output_dir / f"review_frame_{index:02d}.jpg"
        extracted_paths.append(extract_transition_frame(video_path, timestamp, output_path))
    return extracted_paths
