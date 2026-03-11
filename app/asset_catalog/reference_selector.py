"""Helpers for stable catalog asset lookup and deterministic reference selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.asset_catalog.catalog import load_asset_catalog
from app.asset_catalog.models import CatalogAsset

_IMAGE_SUFFIX_PRIORITY = {
    ".png": 0,
    ".jpg": 1,
    ".jpeg": 2,
    ".webp": 3,
    ".bmp": 4,
}
_DEFAULT_IMAGE_SUFFIXES = tuple(_IMAGE_SUFFIX_PRIORITY)
_TYPE_LABELS = {"character": "角色", "scene": "场景", "monster": "怪物"}


@dataclass(slots=True)
class ResolvedCatalogAssetReference:
    """Resolved asset record plus deterministic file ordering metadata."""

    asset: CatalogAsset
    ordered_files: list[Path]
    selected_file: Path
    selected_index: int


def find_catalog_asset(catalog_path: Path, query: str, expected_type: str) -> CatalogAsset:
    """Find a catalog asset by asset_id, jimeng_ref_name, or display_name."""

    catalog = load_asset_catalog(catalog_path)
    normalized_query = query.strip().casefold()
    for asset in catalog.assets:
        if asset.type != expected_type:
            continue
        haystacks = [asset.asset_id, asset.jimeng_ref_name, asset.display_name]
        if any(item.casefold() == normalized_query for item in haystacks):
            return asset
    raise ValueError(f"未找到匹配的{_TYPE_LABELS.get(expected_type, expected_type)}素材: {query}")


def resolve_catalog_asset_reference(
    catalog_path: Path,
    query: str,
    expected_type: str,
    *,
    preferred_index: int = 0,
    allowed_suffixes: tuple[str, ...] = _DEFAULT_IMAGE_SUFFIXES,
) -> ResolvedCatalogAssetReference:
    """Resolve a catalog asset and deterministically select one local reference file."""

    asset = find_catalog_asset(catalog_path, query, expected_type)
    candidate_files = _resolve_asset_files(catalog_path, asset, allowed_suffixes=allowed_suffixes)
    if not candidate_files:
        raise ValueError(f"{_TYPE_LABELS.get(expected_type, expected_type)}素材缺少可用图片文件: {query}")

    ordered_files = sorted(candidate_files, key=_reference_sort_key)
    selected_index = min(max(int(preferred_index), 0), len(ordered_files) - 1)
    return ResolvedCatalogAssetReference(
        asset=asset,
        ordered_files=ordered_files,
        selected_file=ordered_files[selected_index],
        selected_index=selected_index,
    )


def resolve_asset_files(
    catalog_path: Path,
    query: str,
    expected_type: str,
    *,
    allowed_suffixes: tuple[str, ...],
) -> list[Path]:
    """Resolve all matching local files for an asset, filtered by suffix."""

    asset = find_catalog_asset(catalog_path, query, expected_type)
    return _resolve_asset_files(catalog_path, asset, allowed_suffixes=allowed_suffixes)


def _resolve_asset_files(catalog_path: Path, asset: CatalogAsset, *, allowed_suffixes: tuple[str, ...]) -> list[Path]:
    resolved: list[Path] = []
    suffix_set = {suffix.lower() for suffix in allowed_suffixes}
    for raw_file in asset.files:
        candidate = _resolve_relative_catalog_path(catalog_path, raw_file)
        if candidate.exists() and candidate.suffix.lower() in suffix_set and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _resolve_relative_catalog_path(catalog_path: Path, raw_file: str) -> Path:
    candidate = Path(raw_file)
    if candidate.is_absolute():
        return candidate

    search_roots = [catalog_path.parent, catalog_path.parent.parent]
    for root in search_roots:
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return resolved
    return (catalog_path.parent.parent / candidate).resolve()


def _reference_sort_key(path: Path) -> tuple[int, int, str]:
    match = re.search(r"_(\d+)(?:_[^_]*)?$", path.stem)
    number = int(match.group(1)) if match else 10**9
    suffix_rank = _IMAGE_SUFFIX_PRIORITY.get(path.suffix.lower(), len(_IMAGE_SUFFIX_PRIORITY) + 1)
    return (number, suffix_rank, path.name.casefold())
