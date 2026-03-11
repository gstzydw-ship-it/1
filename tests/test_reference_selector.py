import json
from pathlib import Path

from app.asset_catalog.reference_selector import find_catalog_asset, resolve_catalog_asset_reference


def test_find_catalog_asset_matches_display_name_and_asset_id(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    (assets_dir / "characters").mkdir(parents=True, exist_ok=True)
    (assets_dir / "characters" / "hero_1.png").write_bytes(b"hero")
    catalog_path = assets_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "total_assets": 1,
                "assets": [
                    {
                        "asset_id": "CHAR_HERO__v1",
                        "type": "character",
                        "display_name": "Hero",
                        "jimeng_ref_name": "CHAR_HERO__v1",
                        "files": ["assets/characters/hero_1.png"],
                        "tags": ["character", "Hero"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert find_catalog_asset(catalog_path, "Hero", "character").asset_id == "CHAR_HERO__v1"
    assert find_catalog_asset(catalog_path, "CHAR_HERO__v1", "character").display_name == "Hero"


def test_resolve_catalog_asset_reference_prefers_stable_lowest_index_file(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    (assets_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (assets_dir / "scenes" / "classroom_2.jpg").write_bytes(b"scene-2")
    (assets_dir / "scenes" / "classroom_1.png").write_bytes(b"scene-1")
    catalog_path = assets_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "total_assets": 1,
                "assets": [
                    {
                        "asset_id": "SCENE_CLASSROOM__v1",
                        "type": "scene",
                        "display_name": "Classroom",
                        "jimeng_ref_name": "SCENE_CLASSROOM__v1",
                        "files": ["assets/scenes/classroom_2.jpg", "assets/scenes/classroom_1.png"],
                        "tags": ["scene", "Classroom"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    resolved = resolve_catalog_asset_reference(catalog_path, "SCENE_CLASSROOM__v1", "scene")

    assert resolved.asset.asset_id == "SCENE_CLASSROOM__v1"
    assert resolved.selected_index == 0
    assert resolved.selected_file.name == "classroom_1.png"
    assert [path.name for path in resolved.ordered_files] == ["classroom_1.png", "classroom_2.jpg"]
