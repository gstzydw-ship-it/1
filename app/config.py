"""应用配置定义。

当前阶段只提供本地开发骨架配置，后续可扩展为环境变量驱动或多环境配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    """集中管理系统默认路径与数据库配置。"""

    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    data_dir: Path = field(init=False)
    assets_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    outputs_dir: Path = field(init=False)
    database_url: str = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir = self.project_root / "data"
        self.assets_dir = self.data_dir / "assets"
        self.cache_dir = self.data_dir / "cache"
        self.outputs_dir = self.data_dir / "outputs"
        self.database_url = f"sqlite:///{(self.data_dir / 'video_agent.db').as_posix()}"


def get_config() -> AppConfig:
    """返回应用默认配置实例。"""

    return AppConfig()
