import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app


runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "视频 Agent 系统命令行入口" in result.stdout


def test_cli_doctor() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert '"status": "ok"' in result.stdout


def test_cli_feishu_sync_test_uses_bitable_mode(monkeypatch, tmp_path) -> None:
    @dataclass
    class DummyResult:
        total_rows: int
        success_count: int
        failed_count: int
        assets: list[dict]
        manifest_path: str = ""

    captured = {}

    def fake_sync_assets(config):
        captured["use_bitable"] = config.use_bitable
        captured["app_token"] = config.app_token
        captured["table_id"] = config.table_id
        captured["view_id"] = config.view_id
        captured["output_dir"] = str(config.output_dir)
        return DummyResult(total_rows=1, success_count=1, failed_count=0, assets=[])

    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    monkeypatch.setenv("FEISHU_APP_TOKEN", "app-token")
    monkeypatch.setenv("FEISHU_TABLE_ID", "table-id")
    monkeypatch.setenv("FEISHU_VIEW_ID", "view-id")
    monkeypatch.delenv("SPREADSHEET_TOKEN", raising=False)
    monkeypatch.setattr(cli_module, "sync_assets", fake_sync_assets)

    result = runner.invoke(app, ["feishu-sync-test", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "bitable"
    assert captured["use_bitable"] is True
    assert captured["app_token"] == "app-token"
    assert captured["table_id"] == "table-id"
    assert captured["view_id"] == "view-id"


def test_cli_parse_feishu_link_base() -> None:
    result = runner.invoke(
        app,
        [
            "parse-feishu-link",
            "https://example.feishu.cn/base/Xi97bcNsXaRi1Cs6UotclJTm7e?table=tblMUpgrajbkgN9e&view=vewigY7qkV",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["link_type"] == "base"
    assert payload["app_token"] == "Xi97bcNsXaRi1Cs6UotclJTm7e"
    assert payload["table_id"] == "tblMUpgrajbkgN9e"
    assert payload["view_id"] == "vewigY7qkV"


def test_cli_parse_feishu_link_wiki() -> None:
    result = runner.invoke(
        app,
        [
            "parse-feishu-link",
            "https://xcniift1luqh.feishu.cn/wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b?table=tbleiSUQP4sIPRr6&view=vewigY7qkV",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["link_type"] == "wiki"
    assert payload["wiki_token"] == "L56KwZ0lsigvPHkVVSkcjG0Nn6b"
    assert payload["app_token"] == ""
    assert payload["table_id"] == "tbleiSUQP4sIPRr6"
    assert payload["view_id"] == "vewigY7qkV"


def test_cli_inspect_feishu_link_source(monkeypatch) -> None:
    def fake_inspect(url: str):
        assert "wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b" in url
        return {
            "link_info": {"link_type": "wiki"},
            "html_length": 1234,
            "possible_base_tokens": ["Abc123Token"],
            "possible_app_tokens": ["Bitable999"],
            "possible_table_ids": ["tbleiSUQP4sIPRr6"],
            "possible_view_ids": ["vewAzgGzAp"],
            "html_preview": "<html>...</html>",
        }

    monkeypatch.setattr(cli_module, "inspect_feishu_link_source", fake_inspect)
    result = runner.invoke(
        app,
        [
            "inspect-feishu-link-source",
            "https://xcniift1luqh.feishu.cn/wiki/L56KwZ0lsigvPHkVVSkcjG0Nn6b?table=tbleiSUQP4sIPRr6&view=vewAzgGzAp",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["possible_base_tokens"] == ["Abc123Token"]
    assert payload["possible_app_tokens"] == ["Bitable999"]


def test_cli_test_asset_planner(monkeypatch, tmp_path: Path) -> None:
    catalog_dir = tmp_path / "assets"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "catalog.json").write_text(
        json.dumps(
            {
                "total_assets": 1,
                "assets": [
                    {
                        "asset_id": "CHAR_林白__v1",
                        "type": "character",
                        "display_name": "林白",
                        "jimeng_ref_name": "CHAR_林白__v1",
                        "files": ["assets/characters/林白_1.png"],
                        "tags": ["character", "林白"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-asset-planner"])

    assert result.exit_code == 0
    assert "AssetPlanner 本地测试结果" in result.stdout
    assert "reference_assets" in result.stdout
    assert "reference_strategy" in result.stdout
    assert "must_keep" in result.stdout
    assert "drop_if_needed" in result.stdout


def test_cli_test_prompt_composer(monkeypatch, tmp_path: Path) -> None:
    catalog_dir = tmp_path / "assets"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "catalog.json").write_text(
        json.dumps(
            {
                "total_assets": 2,
                "assets": [
                    {
                        "asset_id": "CHAR_林白__v1",
                        "type": "character",
                        "display_name": "林白",
                        "jimeng_ref_name": "CHAR_林白__v1",
                        "files": ["assets/characters/林白_1.png"],
                        "tags": ["character", "林白"],
                    },
                    {
                        "asset_id": "SCENE_古城门__v1",
                        "type": "scene",
                        "display_name": "古城门",
                        "jimeng_ref_name": "SCENE_古城门__v1",
                        "files": ["assets/scenes/古城门_1.jpg"],
                        "tags": ["scene", "古城门"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-prompt-composer"])

    assert result.exit_code == 0
    assert "PromptComposer 本地测试结果" in result.stdout
    assert "shot_id" in result.stdout
    assert "prompt_main" in result.stdout
    assert "prompt_negative" in result.stdout
    assert "ref_assets_in_order" in result.stdout
    assert "continuity_notes" in result.stdout


def test_cli_test_asset_planner_requires_catalog(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-asset-planner"])

    assert result.exit_code != 0
    assert "未找到 catalog.json" in result.stdout or "未找到 catalog.json" in result.stderr
