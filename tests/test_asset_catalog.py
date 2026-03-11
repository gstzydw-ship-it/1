import json
from pathlib import Path

from app.asset_catalog import AssetCatalogService, build_asset_catalog, load_asset_catalog, search_assets


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def test_build_asset_catalog_from_assets_directory(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _touch(assets_dir / "characters" / "林白_1_林白三视图.png")
    _touch(assets_dir / "characters" / "林白_2_林白立绘.png")
    _touch(assets_dir / "monsters" / "赤焰狼_1_赤焰狼.png")
    _touch(assets_dir / "scenes" / "古城门_1_古城门.jpg")

    result = build_asset_catalog(assets_dir)

    assert result.total_assets == 3
    assert result.type_counts == {"character": 1, "monster": 1, "scene": 1}
    catalog_path = assets_dir / "catalog.json"
    assert result.catalog_path == str(catalog_path)
    assert catalog_path.exists()

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert payload["total_assets"] == 3
    assert payload["assets"][0]["jimeng_ref_name"] == payload["assets"][0]["asset_id"]


def test_asset_ids_follow_naming_convention(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _touch(assets_dir / "characters" / "林白_1_林白三视图.png")
    _touch(assets_dir / "monsters" / "赤焰狼_1_赤焰狼.png")
    _touch(assets_dir / "scenes" / "古城门_1_古城门.jpg")

    result = build_asset_catalog(assets_dir)
    asset_ids = {asset.display_name: asset.asset_id for asset in result.assets}

    assert asset_ids["林白"] == "CHAR_林白__v1"
    assert asset_ids["赤焰狼"] == "MON_赤焰狼__v1"
    assert asset_ids["古城门"] == "SCENE_古城门__v1"


def test_search_assets_by_name_and_type(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _touch(assets_dir / "characters" / "林白_1_林白三视图.png")
    _touch(assets_dir / "characters" / "林可_1_林可立绘.png")
    _touch(assets_dir / "scenes" / "林间空地_1_林间空地.jpg")

    catalog = load_asset_catalog(Path(build_asset_catalog(assets_dir).catalog_path))

    name_results = search_assets(catalog.assets, name_query="林")
    type_results = search_assets(catalog.assets, asset_type="character")
    combined_results = search_assets(catalog.assets, name_query="林", asset_type="scene")

    assert len(name_results) == 3
    assert len(type_results) == 2
    assert len(combined_results) == 1
    assert combined_results[0].display_name == "林间空地"


def test_asset_catalog_service_build_and_load(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    _touch(assets_dir / "characters" / "叶明_1_叶明.png")
    service = AssetCatalogService()

    build_result = service.build_catalog(assets_dir)
    catalog = service.load_catalog(Path(build_result.catalog_path))
    results = service.search(catalog, name_query="叶")

    assert build_result.total_assets == 1
    assert catalog.total_assets == 1
    assert results[0].asset_id == "CHAR_叶明__v1"
