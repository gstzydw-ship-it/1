"""本地素材库服务。"""

from __future__ import annotations

from pathlib import Path

from app.asset_catalog.catalog import build_asset_catalog, load_asset_catalog
from app.asset_catalog.models import AssetCatalog, CatalogBuildResult
from app.asset_catalog.search import search_assets


class AssetCatalogService:
    """构建、加载和检索本地素材库。"""

    def build_catalog(self, assets_dir: Path) -> CatalogBuildResult:
        """从 assets 目录构建 catalog.json。"""

        return build_asset_catalog(assets_dir)

    def load_catalog(self, catalog_path: Path) -> AssetCatalog:
        """加载现有 catalog.json。"""

        return load_asset_catalog(catalog_path)

    def search(self, catalog: AssetCatalog, *, name_query: str | None = None, asset_type: str | None = None):
        """执行基础名称 / 类型检索。"""

        return search_assets(catalog.assets, name_query=name_query, asset_type=asset_type)

    def rebuild_index(self) -> dict[str, object]:
        """兼容旧骨架接口。"""

        return {
            "status": "placeholder",
            "indexed_assets": 0,
            "notes": "请改用 build_catalog(Path('assets')) 构建真实 catalog。",
        }
