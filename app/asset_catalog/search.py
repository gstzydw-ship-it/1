"""素材目录基础检索。"""

from __future__ import annotations

from app.asset_catalog.models import CatalogAsset


def search_assets(
    assets: list[CatalogAsset],
    *,
    name_query: str | None = None,
    asset_type: str | None = None,
) -> list[CatalogAsset]:
    """按名称和类型执行基础搜索。"""

    results = assets
    if asset_type:
        results = [asset for asset in results if asset.type == asset_type]
    if name_query:
        keyword = name_query.casefold()
        results = [asset for asset in results if keyword in asset.display_name.casefold()]
    return results
