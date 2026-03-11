"""本地素材目录模块导出。"""

from app.asset_catalog.catalog import build_asset_catalog, load_asset_catalog
from app.asset_catalog.models import AssetCatalog, CatalogAsset, CatalogBuildResult
from app.asset_catalog.reference_selector import ResolvedCatalogAssetReference, find_catalog_asset, resolve_catalog_asset_reference
from app.asset_catalog.search import search_assets
from app.asset_catalog.service import AssetCatalogService

__all__ = [
    "AssetCatalog",
    "AssetCatalogService",
    "CatalogAsset",
    "CatalogBuildResult",
    "ResolvedCatalogAssetReference",
    "build_asset_catalog",
    "find_catalog_asset",
    "load_asset_catalog",
    "resolve_catalog_asset_reference",
    "search_assets",
]
