"""飞书素材同步数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FeishuSyncConfig:
    """飞书素材同步配置。

    `ranges` 使用飞书表格区间表达式，例如：`["Sheet1!A:C"]`。
    """

    app_id: str
    app_secret: str
    spreadsheet_token: str = ""
    app_token: str = ""
    table_id: str = ""
    view_id: str = ""
    base_url: str = "https://open.feishu.cn"
    ranges: list[str] = field(default_factory=lambda: ["Sheet1!A:C"])
    output_dir: Path = field(default_factory=lambda: Path("assets"))

    @property
    def manifest_path(self) -> Path:
        """返回默认 manifest 文件路径。"""

        return self.output_dir / "feishu_sync_manifest.json"

    @property
    def use_bitable(self) -> bool:
        """判断当前是否使用多维表格模式。"""

        return bool(self.app_token and self.table_id)


@dataclass(slots=True)
class AssetRaw:
    """一条素材行的标准化结果。"""

    asset_type: str
    name: str
    feishu_file_tokens: list[str] = field(default_factory=list)
    local_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SyncResult:
    """飞书素材同步结果摘要。"""

    total_rows: int
    success_count: int
    failed_count: int
    assets: list[AssetRaw] = field(default_factory=list)
    manifest_path: str = ""
