import json

from app.asset_catalog import load_asset_catalog


def test_load_asset_catalog_accepts_utf8_bom(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    payload = {
        "total_assets": 1,
        "assets": [
            {
                "asset_id": "CHAR_TEST__v1",
                "type": "character",
                "display_name": "Test",
                "jimeng_ref_name": "CHAR_TEST__v1",
                "files": ["assets/characters/test.png"],
                "tags": ["character", "test"],
            }
        ],
    }

    catalog_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    catalog = load_asset_catalog(catalog_path)

    assert catalog.total_assets == 1
    assert catalog.assets[0].asset_id == "CHAR_TEST__v1"
