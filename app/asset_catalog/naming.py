"""素材命名规则。"""

from __future__ import annotations

import re


TYPE_PREFIX_MAPPING = {
    "character": "CHAR",
    "monster": "MON",
    "scene": "SCENE",
}

DIRECTORY_TYPE_MAPPING = {
    "characters": "character",
    "monsters": "monster",
    "scenes": "scene",
}


def normalize_display_name(name: str) -> str:
    """规整素材展示名。"""

    return re.sub(r"\s+", " ", name.strip())


def sanitize_name_for_asset_id(name: str) -> str:
    """将名称规整为适合 asset_id 的片段。"""

    normalized = normalize_display_name(name)
    sanitized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", normalized)
    return sanitized.strip("_") or "UNKNOWN"


def build_asset_id(asset_type: str, display_name: str) -> str:
    """根据类型与名称构建稳定 asset_id。"""

    prefix = TYPE_PREFIX_MAPPING[asset_type]
    return f"{prefix}_{sanitize_name_for_asset_id(display_name)}__v1"


def infer_type_from_directory(directory_name: str) -> str | None:
    """根据目录名推断素材类型。"""

    return DIRECTORY_TYPE_MAPPING.get(directory_name)
