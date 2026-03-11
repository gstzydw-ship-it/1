"""本地素材库数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CatalogAsset:
    """单个素材资产记录。"""

    asset_id: str
    type: str
    display_name: str
    jimeng_ref_name: str
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AssetCatalog:
    """完整素材目录。"""

    total_assets: int
    assets: list[CatalogAsset] = field(default_factory=list)


@dataclass(slots=True)
class CatalogBuildResult:
    """素材库构建结果摘要。"""

    total_assets: int
    type_counts: dict[str, int] = field(default_factory=dict)
    catalog_path: str = ""
    assets: list[CatalogAsset] = field(default_factory=list)
