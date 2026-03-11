import json
from pathlib import Path

import pytest

from app.feishu_sync import FeishuSyncConfig, SyncResult, sync_assets
from app.feishu_sync.client import FeishuApiError
from app.feishu_sync.service import inspect_feishu_link_source, parse_feishu_link


class MockFeishuClient:
    def __init__(self) -> None:
        self.download_calls: list[tuple[str, Path]] = []
        self.bitable_calls: list[dict] = []

    def get_tenant_access_token(self) -> str:
        return "tenant-token"

    def read_multiple_ranges(self, spreadsheet_token: str, ranges: list[str]) -> dict:
        assert spreadsheet_token == "sheet-token"
        assert ranges == ["Sheet1!A:C"]
        return {
            "code": 0,
            "data": {
                "valueRanges": [
                    {
                        "range": "Sheet1!A:C",
                        "values": [
                            ["人物", "李青", {"file_token": "file_char_1"}],
                            ["妖兽", "白虎机甲", {"fileToken": "file_monster_1"}],
                            ["场景", "古城门", [{"file_token": "file_scene_1"}]],
                        ],
                    }
                ]
            },
        }

    def read_bitable_records(self, app_token: str, table_id: str, view_id: str = "", page_size: int = 500) -> dict:
        self.bitable_calls.append(
            {
                "app_token": app_token,
                "table_id": table_id,
                "view_id": view_id,
                "page_size": page_size,
            }
        )
        assert app_token == "app-token"
        assert table_id == "tbl-token"
        assert page_size == 500
        if view_id == "vew-token":
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "record_id": "rec_1",
                            "fields": {
                                "类型": "人物",
                                "名称": "李青",
                                "图片": [{"file_token": "file_char_1"}],
                            },
                        },
                        {
                            "record_id": "rec_2",
                            "fields": {
                                "类型": "场景",
                                "名称": "古城门",
                                "图片": {"fileToken": "file_scene_1"},
                            },
                        },
                    ]
                },
            }
        return {
            "code": 0,
            "data": {
                "items": [
                    {
                        "record_id": "rec_1",
                        "fields": {
                            "类型": "人物",
                            "名称": "李青",
                            "图片": [{"file_token": "file_char_1"}],
                        },
                    }
                ]
            },
        }

    def download_media(self, file_token: str, out_path: Path) -> Path:
        self.download_calls.append((file_token, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mock-image")
        return out_path


def test_sync_assets_basic_flow(tmp_path: Path) -> None:
    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sheet-token",
        ranges=["Sheet1!A:C"],
        output_dir=tmp_path / "assets",
    )
    client = MockFeishuClient()

    result = sync_assets(config=config, client=client)

    assert isinstance(result, SyncResult)
    assert result.total_rows == 3
    assert result.success_count == 3
    assert result.failed_count == 0
    assert len(result.assets) == 3
    assert result.manifest_path.endswith("feishu_sync_manifest.json")
    assert result.assets[0].asset_type == "人物"
    assert result.assets[0].name == "李青"
    assert result.assets[0].feishu_file_tokens == ["file_char_1"]
    assert len(result.assets[0].local_files) == 1


def test_sync_assets_type_mapping(tmp_path: Path) -> None:
    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sheet-token",
        ranges=["Sheet1!A:C"],
        output_dir=tmp_path / "assets",
    )
    client = MockFeishuClient()

    sync_assets(config=config, client=client)

    downloaded_paths = {token: path for token, path in client.download_calls}
    assert "characters" in str(downloaded_paths["file_char_1"])
    assert "monsters" in str(downloaded_paths["file_monster_1"])
    assert "scenes" in str(downloaded_paths["file_scene_1"])


def test_sync_result_structure_with_missing_token(tmp_path: Path) -> None:
    class MissingTokenClient(MockFeishuClient):
        def read_multiple_ranges(self, spreadsheet_token: str, ranges: list[str]) -> dict:
            return {
                "code": 0,
                "data": {
                    "valueRanges": [
                        {
                            "values": [
                                ["人物", "无图角色", {"text": "没有 token"}],
                            ]
                        }
                    ]
                },
            }

    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sheet-token",
        ranges=["Sheet1!A:C"],
        output_dir=tmp_path / "assets",
    )

    result = sync_assets(config=config, client=MissingTokenClient())

    assert result.total_rows == 1
    assert result.success_count == 0
    assert result.failed_count == 1
    assert result.assets[0].feishu_file_tokens == []
    assert result.assets[0].local_files == []


def test_sync_assets_bitable_mode(tmp_path: Path) -> None:
    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="app-token",
        table_id="tbl-token",
        view_id="vew-token",
        output_dir=tmp_path / "assets",
    )
    client = MockFeishuClient()

    result = sync_assets(config=config, client=client)

    assert config.use_bitable is True
    assert result.total_rows == 2
    assert result.success_count == 2
    assert result.failed_count == 0
    assert result.assets[0].asset_type == "人物"
    assert result.assets[0].name == "李青"
    assert result.assets[1].asset_type == "场景"
    assert "characters" in str(client.download_calls[0][1])
    assert "scenes" in str(client.download_calls[1][1])


def test_sync_assets_bitable_mode_with_realistic_field_names(tmp_path: Path) -> None:
    class RealFieldClient(MockFeishuClient):
        def read_bitable_records(self, app_token: str, table_id: str, view_id: str = "", page_size: int = 500) -> dict:
            self.bitable_calls.append(
                {
                    "app_token": app_token,
                    "table_id": table_id,
                    "view_id": view_id,
                    "page_size": page_size,
                }
            )
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "record_id": "rec_1",
                            "fields": {
                                "人物或场景名称": "林白",
                                "类型": "人物",
                                "附件": [
                                    {
                                        "file_token": "real_field_file_1",
                                        "name": "林白三视图.png",
                                    }
                                ],
                            },
                        }
                    ]
                },
            }

    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="app-token",
        table_id="tbl-token",
        view_id="vew-token",
        output_dir=tmp_path / "assets",
    )
    client = RealFieldClient()

    result = sync_assets(config=config, client=client)

    assert result.total_rows == 1
    assert result.success_count == 1
    assert result.failed_count == 0
    assert result.assets[0].name == "林白"
    assert result.assets[0].feishu_file_tokens == ["real_field_file_1"]
    assert "characters" in str(client.download_calls[0][1])
    assert str(client.download_calls[0][1]).endswith("林白_1_林白三视图.png")


def test_sync_assets_skips_existing_file(tmp_path: Path) -> None:
    class ExistingFileClient(MockFeishuClient):
        def read_bitable_records(self, app_token: str, table_id: str, view_id: str = "", page_size: int = 500) -> dict:
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "record_id": "rec_1",
                            "fields": {
                                "人物或场景名称": "林白",
                                "类型": "人物",
                                "附件": [
                                    {
                                        "file_token": "real_field_file_1",
                                        "name": "林白三视图.png",
                                    }
                                ],
                            },
                        }
                    ]
                },
            }

    existing_path = tmp_path / "assets" / "characters" / "林白_1_林白三视图.png"
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_bytes(b"existing")

    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="app-token",
        table_id="tbl-token",
        view_id="vew-token",
        output_dir=tmp_path / "assets",
    )
    client = ExistingFileClient()

    result = sync_assets(config=config, client=client)

    assert result.success_count == 1
    assert result.assets[0].local_files == [str(existing_path)]
    assert client.download_calls == []


def test_sync_assets_reuses_legacy_bin_file(tmp_path: Path) -> None:
    class ExistingLegacyClient(MockFeishuClient):
        def read_bitable_records(self, app_token: str, table_id: str, view_id: str = "", page_size: int = 500) -> dict:
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "record_id": "rec_1",
                            "fields": {
                                "人物或场景名称": "林白",
                                "类型": "人物",
                                "附件": [
                                    {
                                        "file_token": "legacy_file_1",
                                        "name": "林白三视图.png",
                                    }
                                ],
                            },
                        }
                    ]
                },
            }

    legacy_path = tmp_path / "assets" / "characters" / "林白_1.bin"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"legacy")

    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="app-token",
        table_id="tbl-token",
        view_id="vew-token",
        output_dir=tmp_path / "assets",
    )
    client = ExistingLegacyClient()

    result = sync_assets(config=config, client=client)

    assert result.success_count == 1
    assert result.assets[0].local_files == [str(legacy_path)]
    assert client.download_calls == []


def test_sync_assets_writes_manifest(tmp_path: Path) -> None:
    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        spreadsheet_token="sheet-token",
        ranges=["Sheet1!A:C"],
        output_dir=tmp_path / "assets",
    )
    client = MockFeishuClient()

    result = sync_assets(config=config, client=client)

    manifest_path = Path(result.manifest_path)
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_mode"] == "spreadsheet"
    assert manifest["asset_count"] == 3
    assert manifest["assets"][0]["name"] == "李青"


def test_sync_assets_bitable_retry_without_view_id(tmp_path: Path) -> None:
    class RetryClient(MockFeishuClient):
        def read_bitable_records(self, app_token: str, table_id: str, view_id: str = "", page_size: int = 500) -> dict:
            self.bitable_calls.append({"view_id": view_id})
            if view_id:
                raise FeishuApiError(
                    "飞书 API HTTP 请求失败: status=400",
                    url="https://open.feishu.cn/open-apis/bitable/v1/apps/app-token/tables/tbl-token/records?page_size=500&view_id=vew-token",
                    method="GET",
                    query_params={"page_size": 500, "view_id": "vew-token"},
                    response_body='{"code":91402,"msg":"NOTEXIST","data":{}}',
                    status_code=400,
                    api_code=91402,
                    api_msg="NOTEXIST",
                )
            return {
                "code": 0,
                "data": {
                    "items": [
                        {
                            "record_id": "rec_1",
                            "fields": {
                                "类型": "人物",
                                "名称": "李青",
                                "图片": [{"file_token": "file_char_1"}],
                            },
                        }
                    ]
                },
            }

    config = FeishuSyncConfig(
        app_id="cli_xxx",
        app_secret="secret",
        app_token="app-token",
        table_id="tbl-token",
        view_id="vew-token",
        output_dir=tmp_path / "assets",
    )
    client = RetryClient()

    result = sync_assets(config=config, client=client)

    assert result.total_rows == 1
    assert [call["view_id"] for call in client.bitable_calls] == ["vew-token", ""]


def test_parse_feishu_base_link() -> None:
    parsed = parse_feishu_link(
        "https://example.feishu.cn/base/Xi97bcNsXaRi1Cs6UotclJTm7e?table=tblMUpgrajbkgN9e&view=vewigY7qkV"
    )

    assert parsed["link_type"] == "base"
    assert parsed["app_token"] == "Xi97bcNsXaRi1Cs6UotclJTm7e"
    assert parsed["table_id"] == "tblMUpgrajbkgN9e"
    assert parsed["view_id"] == "vewigY7qkV"
    assert parsed["wiki_token"] == ""


def test_parse_feishu_wiki_link() -> None:
    parsed = parse_feishu_link(
        "https://xcniift1luqh.feishu.cn/wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b?table=tbleiSUQP4sIPRr6&view=vewigY7qkV"
    )

    assert parsed["link_type"] == "wiki"
    assert parsed["wiki_token"] == "L56KwZ0lsigvPHkVVSkcjG0Nn6b"
    assert parsed["table_id"] == "tbleiSUQP4sIPRr6"
    assert parsed["view_id"] == "vewigY7qkV"
    assert parsed["app_token"] == ""
    assert "无法仅凭该链接确定 bitable app_token" in parsed["warning"]


def test_inspect_feishu_link_source_extracts_candidates() -> None:
    class HtmlClient(MockFeishuClient):
        def fetch_public_page_html(self, url: str) -> str:
            assert "wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b" in url
            return """
            <html>
              <body>
                <a href="/base/Abc123Token?table=tbleiSUQP4sIPRr6&view=vewAzgGzAp">base</a>
                <script>
                  window.__DATA__ = {"app_token":"Bitable999","table":"tbleiSUQP4sIPRr6","view":"vewAzgGzAp"};
                </script>
              </body>
            </html>
            """

    result = inspect_feishu_link_source(
        "https://xcniift1luqh.feishu.cn/wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b?table=tbleiSUQP4sIPRr6&view=vewAzgGzAp",
        client=HtmlClient(),
    )

    assert result["link_info"]["link_type"] == "wiki"
    assert "Abc123Token" in result["possible_base_tokens"]
    assert "Bitable999" in result["possible_app_tokens"]
    assert "tbleiSUQP4sIPRr6" in result["possible_table_ids"]
    assert "vewAzgGzAp" in result["possible_view_ids"]
