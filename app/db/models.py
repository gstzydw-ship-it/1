"""数据库核心模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.db.base import TimestampedModel


class TaskRun(TimestampedModel, table=True):
    """一次总控执行记录。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_name: str
    status: str = "pending"
    workflow_mode: str = "placeholder"
    script_path: Optional[str] = None
    current_stage: str = "pending"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0


class StoryboardRecord(TimestampedModel, table=True):
    """镜头级运行记录。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_run_id: Optional[int] = Field(default=None, foreign_key="taskrun.id")
    storyboard_key: str
    shot_index: int = 0
    summary: str
    status: str = "pending"
    current_stage: str = "pending"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    previous_frame_summary: str = ""
    transition_frame_path: Optional[str] = None
    transition_frame_summary: str = ""


class AssetRecord(TimestampedModel, table=True):
    """素材下载/索引记录。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    asset_key: str
    asset_type: str
    source: str
    local_path: Optional[str] = None


class VideoGenerationRecord(TimestampedModel, table=True):
    """镜头生成视频记录。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    storyboard_id: Optional[int] = Field(default=None, foreign_key="storyboardrecord.id")
    provider_job_id: str = ""
    status: str = "pending"
    current_stage: str = "pending"
    video_path: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int = 0


class RetryRecord(TimestampedModel, table=True):
    """恢复/重试记录。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    task_run_id: Optional[int] = Field(default=None, foreign_key="taskrun.id")
    stage_name: str
    retry_count: int = 0
    last_error: Optional[str] = None


class PromptCacheRecord(TimestampedModel, table=True):
    """提示词摘要持久化。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    cache_key: str
    prompt_text: str
    reference_asset_ids: str = ""


__all__ = [
    "SQLModel",
    "TaskRun",
    "StoryboardRecord",
    "AssetRecord",
    "VideoGenerationRecord",
    "RetryRecord",
    "PromptCacheRecord",
]
