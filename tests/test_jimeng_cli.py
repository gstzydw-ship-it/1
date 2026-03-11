import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app
from app.openclaw.models import CatalogAssetSummary

runner = CliRunner()


class FakeJimengOperator:
    def __init__(self, _config) -> None:
        self.called_with = None
        self.closed = False

    def run_dry_run(self, request):
        self.called_with = request
        return SimpleNamespace(
            page_opened=True,
            reference_mode_ready=True,
            prompt_filled=True,
            references_selected=True,
            validation_passed=True,
            uploaded_reference_names=["图片1", "图片2"],
            selected_reference_names=["图片1", "图片2"],
        )

    def run_one_shot(self, request):
        self.called_with = request
        if not request.hold_for_audit:
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"fake-video")
        return SimpleNamespace(
            shot_id=request.shot_id,
            prompt_main=request.prompt_main,
            ref_assets_in_order=request.ref_assets_in_order,
            transition_reference=request.transition_reference,
            uploaded_reference_names=["图片1", "图片2"],
            selected_reference_names=["图片1", "图片2"],
            submitted=True,
            ready_for_download=request.hold_for_audit,
            download_succeeded=not request.hold_for_audit,
            download_path="" if request.hold_for_audit else str(request.output_path),
            audit_report_path="",
            audit_action="",
            audit_summary="",
            prompt_patch="",
            revised_prompt_main="",
            failed_stage="",
            messages=["页面打开步骤已执行。", "点击生成步骤已执行。", "下载视频步骤已执行。"],
        )

    def watch_and_download(self, *, output_path, timeout_seconds, poll_interval_seconds):
        self.called_with = {
            "output_path": output_path,
            "timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
        }
        return SimpleNamespace(
            page_opened=True,
            reference_mode_ready=True,
            generation_completed=True,
            download_succeeded=True,
            poll_status="ready_marker_increased",
            failed_stage="",
            download_path=str(output_path),
            messages=["页面打开步骤已执行。", "轮询生成结果步骤已执行。", "下载视频步骤已执行。"],
        )

    def download_latest_video(self, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video")
        return True

    def close(self):
        self.closed = True


def _write_catalog(tmp_path: Path) -> Path:
    catalog_path = tmp_path / "assets" / "catalog.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
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
                        "files": ["assets/scenes/古城门.jpg"],
                        "tags": ["scene", "古城门"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (tmp_path / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "scenes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "characters" / "林白_1.png").write_bytes(b"fake")
    (tmp_path / "assets" / "scenes" / "古城门.jpg").write_bytes(b"fake")
    return catalog_path


def _patch_cli_dependencies(monkeypatch, tmp_path: Path, catalog_path: Path) -> None:
    monkeypatch.setattr(cli_module, "_resolve_catalog_path", lambda _project_root: catalog_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr(
        cli_module,
        "OpenClawClient",
        lambda: SimpleNamespace(
            build_asset_planner_request_from_catalog=lambda **kwargs: SimpleNamespace(),
            run_asset_planner=lambda _request: SimpleNamespace(
                selected_assets=[
                    CatalogAssetSummary(
                        asset_id="CHAR_林白__v1",
                        type="character",
                        display_name="林白",
                        jimeng_ref_name="CHAR_林白__v1",
                        tags=["character"],
                    ),
                    CatalogAssetSummary(
                        asset_id="SCENE_古城门__v1",
                        type="scene",
                        display_name="古城门",
                        jimeng_ref_name="SCENE_古城门__v1",
                        tags=["scene"],
                    ),
                ]
            ),
            run_prompt_composer=lambda request: SimpleNamespace(
                shot_id=request.shot_id or request.storyboard_id,
                prompt_main="主体：林白；动作：准备迎战；场景：古城门；镜头：中景推进；光影：自然层次；风格：电影感；连续性：承接 @TransitionFrame。",
                prompt_negative="避免主体模糊。",
                ref_assets_in_order=["@TransitionFrame", "CHAR_林白__v1", "SCENE_古城门__v1"],
                continuity_notes="下一镜头继续优先参考 @TransitionFrame。",
            ),
        ),
    )
    monkeypatch.setattr(cli_module, "JimengWebOperator", FakeJimengOperator)


def test_cli_jimeng_dry_run_command_exists(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)

    result = runner.invoke(app, ["jimeng-dry-run"])

    assert result.exit_code == 0
    assert "Jimeng Dry Run 结果" in result.stdout
    assert "页面是否打开成功" in result.stdout
    assert "是否进入全能参考模式" in result.stdout
    assert "prompt 是否填写成功" in result.stdout
    assert "参考图是否按顺序选中成功" in result.stdout
    assert "上传后的参考图引用名" in result.stdout


def test_cli_run_one_shot_command_exists(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)

    result = runner.invoke(app, ["run-one-shot"])

    assert result.exit_code == 0
    assert "run-one-shot 结果" in result.stdout
    assert "shot_id: demo-storyboard-001" in result.stdout or "shot_id: demo_shot_001" in result.stdout
    assert "是否成功提交生成: 是" in result.stdout
    assert "是否成功下载: 是" in result.stdout
    assert "下载文件路径" in result.stdout


def test_cli_run_one_shot_supports_audit_before_download(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(cli_module, "_open_audit_report", lambda _report_path: None)

    result = runner.invoke(app, ["run-one-shot", "--audit-before-download"], input="approve\n")

    assert result.exit_code == 0
    assert "run-one-shot 结果" in result.stdout
    assert "审计报告路径" in result.stdout
    assert "审计动作: approve" in result.stdout
    assert "是否成功下载: 是" in result.stdout


def test_cli_run_one_shot_supports_auto_audit(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(
        cli_module,
        "_run_gemini_auto_audit",
        lambda **kwargs: SimpleNamespace(
            action="revise",
            selected_issue_ids=["character_drift"],
            review_summary="Gemini 发现人物脸部轻微漂移，建议小幅补强人物一致性。",
            prompt_patch="保持主角外观、服装、发型和脸部稳定，不要换脸、串角或服装突变。",
            revised_prompt_main="主体：林白；动作：准备迎战；补充约束：保持主角外观、服装、发型和脸部稳定，不要换脸、串角或服装突变。",
            report_path=str(tmp_path / "outputs" / "reviews" / "demo-storyboard-001_audit.html"),
        ),
    )

    result = runner.invoke(app, ["run-one-shot", "--auto-audit"])

    assert result.exit_code == 0
    assert "审计动作: revise" in result.stdout
    assert "审计摘要: Gemini 发现人物脸部轻微漂移" in result.stdout
    assert "小幅修正补丁" in result.stdout
    assert "是否成功下载: 否" in result.stdout


def test_cli_watch_jimeng_job_command_exists(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(cli_module, "_notify_local", lambda title, message: None)

    result = runner.invoke(app, ["watch-jimeng-job", "--poll-interval-seconds", "15", "--timeout-seconds", "600"])

    assert result.exit_code == 0
    assert "watch-jimeng-job 结果" in result.stdout
    assert "页面是否打开成功: 是" in result.stdout
    assert "是否等到任务完成: 是" in result.stdout
    assert "是否成功下载: 是" in result.stdout
    assert "下载文件路径" in result.stdout


def test_cli_run_two_shots_command_exists(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(
        cli_module,
        "extract_transition_frame",
        lambda video_path, timestamp_seconds, output_path: output_path,
    )

    result = runner.invoke(app, ["run-two-shots"])

    assert result.exit_code == 0
    assert "run-two-shots 结果" in result.stdout
    assert "shot_1 生成成功" in result.stdout
    assert "shot_1_transition_frame_path" in result.stdout
    assert "shot_2 是否已使用承接帧: 是" in result.stdout
    assert "shot_2_prompt_main" in result.stdout
    assert "shot_2_ref_assets_in_order" in result.stdout
    assert "shot_2_video_path" in result.stdout


def test_cli_run_two_shots_supports_audit_before_download(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(
        cli_module,
        "extract_transition_frame",
        lambda video_path, timestamp_seconds, output_path: output_path,
    )
    monkeypatch.setattr(cli_module, "_open_audit_report", lambda _report_path: None)

    result = runner.invoke(
        app,
        ["run-two-shots", "--audit-before-download"],
        input="approve\napprove\n",
    )

    assert result.exit_code == 0
    assert "run-two-shots 结果" in result.stdout
    assert "shot_1 生成成功: 是" in result.stdout
    assert "shot_2 生成成功: 是" in result.stdout
    assert "shot_2 审计动作: approve" in result.stdout


def test_cli_run_two_shots_supports_auto_audit(monkeypatch, tmp_path: Path) -> None:
    catalog_path = _write_catalog(tmp_path)
    _patch_cli_dependencies(monkeypatch, tmp_path, catalog_path)
    monkeypatch.setattr(
        cli_module,
        "extract_transition_frame",
        lambda video_path, timestamp_seconds, output_path: output_path,
    )

    actions = iter(
        [
            SimpleNamespace(
                action="approve",
                selected_issue_ids=[],
                review_summary="Gemini 判定 shot_1 可直接下载。",
                prompt_patch="",
                revised_prompt_main="",
                report_path=str(tmp_path / "outputs" / "reviews" / "demo_shot_001_audit.html"),
            ),
            SimpleNamespace(
                action="revise",
                selected_issue_ids=["motion_mismatch"],
                review_summary="Gemini 判定 shot_2 动作阶段需要更明确约束。",
                prompt_patch="动作必须从当前镜头要求的阶段开始，不要回退到上一动作，也不要跳到后续动作。",
                revised_prompt_main="主体：林白；动作：准备迎战；补充约束：动作必须从当前镜头要求的阶段开始，不要回退到上一动作，也不要跳到后续动作。",
                report_path=str(tmp_path / "outputs" / "reviews" / "demo_shot_002_audit.html"),
            ),
        ]
    )
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", lambda **kwargs: next(actions))

    result = runner.invoke(app, ["run-two-shots", "--auto-audit"])

    assert result.exit_code == 0
    assert "shot_1 生成成功: 是" in result.stdout
    assert "shot_2 审计动作: revise" in result.stdout
    assert "shot_2 审计摘要: Gemini 判定 shot_2 动作阶段需要更明确约束。" in result.stdout
