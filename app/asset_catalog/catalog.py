"""素材目录构建逻辑。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from app.asset_catalog.models import AssetCatalog, CatalogAsset, CatalogBuildResult
from app.asset_catalog.naming import build_asset_id, infer_type_from_directory, normalize_display_name


def build_asset_catalog(assets_dir: Path) -> CatalogBuildResult:
    """扫描 assets 目录并构建 catalog.json。"""

    grouped_files: dict[tuple[str, str], list[Path]] = defaultdict(list)

    for type_dir in ("characters", "monsters", "scenes"):
        root = assets_dir / type_dir
        if not root.exists():
            continue

        asset_type = infer_type_from_directory(type_dir)
        if asset_type is None:
            continue

        for file_path in root.glob("*"):
            if not file_path.is_file():
                continue
            display_name = infer_display_name_from_file(file_path)
            grouped_files[(asset_type, display_name)].append(file_path)

    assets: list[CatalogAsset] = []
    type_counts: dict[str, int] = {"character": 0, "monster": 0, "scene": 0}

    for (asset_type, display_name), files in sorted(grouped_files.items()):
        asset_id = build_asset_id(asset_type, display_name)
        tags = [asset_type, display_name]
        asset = CatalogAsset(
            asset_id=asset_id,
            type=asset_type,
            display_name=display_name,
            jimeng_ref_name=asset_id,
            files=[str(path) for path in sorted(files)],
            tags=tags,
        )
        assets.append(asset)
        type_counts[asset_type] += 1

    catalog = AssetCatalog(total_assets=len(assets), assets=assets)
    catalog_path = assets_dir / "catalog.json"
    catalog_path.write_text(_serialize_catalog(catalog), encoding="utf-8")

    return CatalogBuildResult(
        total_assets=catalog.total_assets,
        type_counts=type_counts,
        catalog_path=str(catalog_path),
        assets=assets,
    )


def load_asset_catalog(catalog_path: Path) -> AssetCatalog:
    """从 catalog.json 加载素材目录。"""

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assets = [CatalogAsset(**item) for item in payload.get("assets", [])]
    return AssetCatalog(total_assets=payload.get("total_assets", len(assets)), assets=assets)


def infer_display_name_from_file(file_path: Path) -> str:
    """从文件名推断素材展示名。"""

    stem = file_path.stem
    match = re.match(r"^(?P<name>.+?)_(?P<index>\d+)(?:_.+)?$", stem)
    if match:
        return normalize_display_name(match.group("name"))
    return normalize_display_name(stem)


def _serialize_catalog(catalog: AssetCatalog) -> str:
    payload = {
        "total_assets": catalog.total_assets,
        "assets": [
            {
                "asset_id": asset.asset_id,
                "type": asset.type,
                "display_name": asset.display_name,
                "jimeng_ref_name": asset.jimeng_ref_name,
                "files": asset.files,
                "tags": asset.tags,
            }
            for asset in catalog.assets
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
