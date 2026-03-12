import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app
from app.openclaw.models import SceneAnchorImageResponse, SceneAnchorReviewResponse

runner = CliRunner()


def _write_catalog(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "assets"
    (catalog_dir / "characters").mkdir(parents=True, exist_ok=True)
    (catalog_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (catalog_dir / "characters" / "林白_1.png").write_bytes(b"fake-character")
    (catalog_dir / "scenes" / "古城门1.jpg").write_bytes(b"fake-scene")
    (catalog_dir / "catalog.json").write_text(
        json.dumps(
            {
                "total_assets": 3,
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
                        "asset_id": "MON_赤焰狼__v1",
                        "type": "monster",
                        "display_name": "赤焰狼",
                        "jimeng_ref_name": "MON_赤焰狼__v1",
                        "files": ["assets/monsters/赤焰狼1.png"],
                        "tags": ["monster", "赤焰狼"],
                    },
                    {
                        "asset_id": "SCENE_古城门__v1",
                        "type": "scene",
                        "display_name": "古城门",
                        "jimeng_ref_name": "SCENE_古城门__v1",
                        "files": ["assets/scenes/古城门1.jpg"],
                        "tags": ["scene", "古城门"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_cli_test_prompt_composer_supports_template_switch(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-prompt-composer", "--template", "cinematic"])

    assert result.exit_code == 0
    assert "PromptComposer 本地测试结果" in result.stdout
    assert "- template: cinematic" in result.stdout
    assert "shot_id" in result.stdout
    assert "prompt_main" in result.stdout
    assert "prompt_negative" in result.stdout
    assert "ref_assets_in_order" in result.stdout
    assert "continuity_notes" in result.stdout


def test_cli_test_prompt_composer_supports_explicit_inputs(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(
        app,
        [
            "test-prompt-composer",
            "--template",
            "continuity_first",
            "--previous-frame-summary",
            "上一镜头结束时林白正面看向赤焰狼",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
        ],
    )

    assert result.exit_code == 0
    assert "- previous_frame_summary: 上一镜头结束时林白正面看向赤焰狼" in result.stdout
    assert "- character_ref: 林白" in result.stdout
    assert "- scene_ref: 古城门" in result.stdout
    assert "- continuity_requirements: 保持林白服装、发型和视线方向一致，保持古城门空间朝向和镜头方位一致" in result.stdout
    assert "@TransitionFrame" in result.stdout
    assert "古城门" in result.stdout


def test_cli_test_prompt_composer_reports_invalid_template(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-prompt-composer", "--template", "unknown"])

    assert result.exit_code != 0
    assert "不支持的模板" in result.stdout or "不支持的模板" in result.stderr


def test_cli_test_prompt_composer_reports_missing_custom_ref(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["test-prompt-composer", "--character-ref", "不存在角色"])

    assert result.exit_code != 0
    assert "未找到匹配的角色素材" in result.stdout or "未找到匹配的角色素材" in result.stderr


def test_cli_generate_scene_anchor_outputs_result(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def _fake_generate(self, request_model, *, project_root=None):
        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(tmp_path / "outputs" / "images" / "scene_anchor.png"),
            source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", _fake_generate)

    result = runner.invoke(
        app,
        [
            "generate-scene-anchor",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
            "--storyboard-text",
            "林白在古城门前站定，准备进入新场景。",
        ],
    )

    assert result.exit_code == 0
    assert "Scene Anchor Image 生成结果" in result.stdout
    assert "- character_ref: 林白" in result.stdout
    assert "- scene_ref: 古城门" in result.stdout
    assert "- review_status: pending" in result.stdout
    assert "scene_anchor.png" in result.stdout


def test_cli_generate_scene_anchor_reports_missing_image(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    (tmp_path / "assets" / "characters" / "林白_1.png").unlink()
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    result = runner.invoke(app, ["generate-scene-anchor", "--character-ref", "林白", "--scene-ref", "古城门"])

    assert result.exit_code != 0
    assert "角色素材缺少可用图片文件" in result.stdout or "角色素材缺少可用图片文件" in result.stderr


def test_cli_generate_scene_anchor_supports_auto_review(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def _fake_generate(self, request_model, *, project_root=None):
        output_path = tmp_path / "outputs" / "images" / "scene_anchor.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"generated")
        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(output_path),
            source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
        )

    def _fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="approve",
            review_summary="人物和场景稳定，可直接作为首帧。",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
            model_name="gemini-2.5-flash",
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", _fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", _fake_review)

    result = runner.invoke(
        app,
        [
            "generate-scene-anchor",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
            "--auto-review",
        ],
    )

    assert result.exit_code == 0
    assert "- auto_review:" in result.stdout
    assert "action: approve" in result.stdout
    assert "人物和场景稳定，可直接作为首帧。" in result.stdout


def test_estimate_manju_duration_seconds_prefers_short_locked_shots() -> None:
    assert cli_module._estimate_manju_duration_seconds("林白看向前方，固定中景，对白开始。") == 4
    assert cli_module._estimate_manju_duration_seconds("周浩天收回拳头后指向林白，固定中景。") == 5
    assert cli_module._estimate_manju_duration_seconds("众人围观，周浩天搂着陈夏娜走出去。") >= 6


def test_estimate_manju_duration_seconds_keeps_dialogue_reaction_shots_short() -> None:
    assert cli_module._estimate_manju_duration_seconds("林可儿（同样惊讶）：你认识我？固定中景，对话反应镜头。") == 4
    assert cli_module._estimate_manju_duration_seconds("林白（语气试探）：还真有件事想请你帮忙。固定机位，人物站定。") == 4


def test_estimate_manju_duration_seconds_gives_establishing_shots_more_time() -> None:
    assert cli_module._estimate_manju_duration_seconds("林白来到学校宿舍外道路，镜头建立放学路上的空间关系。") == 5
    assert cli_module._estimate_manju_duration_seconds("林白走进街角事故现场，周围路人与浓烟同时进入画面。") >= 6


def test_estimate_manju_duration_seconds_prefers_longer_disaster_and_rescue_motion() -> None:
    assert cli_module._estimate_manju_duration_seconds("街角突然爆炸，浓烟滚滚，林白带着小黄朝事发地奔跑。") >= 6
    assert cli_module._estimate_manju_duration_seconds("林白救人要紧，脱下校服裹住拳头，一拳砸碎车窗，再把少女拉出来。") == 7


def test_cli_run_manju_scene_shot_stops_when_anchor_review_fails(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def _fake_generate(self, request_model, *, project_root=None):
        output_path = tmp_path / "outputs" / "images" / "scene_anchor.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"generated")
        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(output_path),
            source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
        )

    def _fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="revise",
            review_summary="人物稳定，但场景有轻微跑偏。",
            selected_issue_ids=["scene_mismatch"],
            prompt_patch="保持普通教室空间结构，不要混入额外建筑元素。",
            revised_prompt="补丁后的提示词",
            model_name="gemini-2.5-flash",
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", _fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", _fake_review)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
            "--storyboard-text",
            "林白在新场景中站定。",
        ],
    )

    assert result.exit_code != 0
    assert "图审未通过，本次不会进入 Manju 视频生成。" in result.stdout or "图审未通过，本次不会进入 Manju 视频生成。" in result.stderr


def test_cli_run_manju_scene_shot_invokes_stable_script_after_anchor_review(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def _fake_generate(self, request_model, *, project_root=None):
        output_path = tmp_path / "outputs" / "images" / "scene_anchor.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"generated")
        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(output_path),
            source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
        )

    def _fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="approve",
            review_summary="人物和场景稳定，可继续生成视频。",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
            model_name="gemini-2.5-flash",
        )

    def _fake_subprocess(
        *,
        project_root,
        image_path,
        prompt,
        output_path,
        mode,
        resolution,
        duration_seconds,
        aspect_ratio,
        model_name,
        project_url="",
        profile_dir=None,
        headless=True,
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(
            args=["python", "scripts/manju_one_shot.py"],
            returncode=0,
            stdout="执行成功\n任务：视频任务 #1-9\n视频：D:/agent/video-agent-system/outputs/videos/fake.mp4\n",
            stderr="",
        )

    def _fake_video_audit(**kwargs):
        return SimpleNamespace(
            action="approve",
            selected_issue_ids=[],
            review_summary="视频稳定，可下载。",
            prompt_patch="",
            revised_prompt_main="",
            report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", _fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", _fake_review)
    monkeypatch.setattr(cli_module, "_run_manju_one_shot_script", _fake_subprocess)
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", _fake_video_audit)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
            "--storyboard-text",
            "林白在新场景中站定。",
        ],
    )

    assert result.exit_code == 0
    assert "Manju 场景首帧结果" in result.stdout
    assert "anchor_review_action: approve" in result.stdout
    assert "Manju 视频生成结果" in result.stdout
    assert "manju_mode: 普通模式" in result.stdout
    assert "manju_resolution: 1080p" in result.stdout
    assert "video_review_action: approve" in result.stdout
    assert "- status: success" in result.stdout


def test_cli_run_manju_scene_shot_blocks_download_when_video_review_fails(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def _fake_generate(self, request_model, *, project_root=None):
        output_path = tmp_path / "outputs" / "images" / "scene_anchor.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"generated")
        return SceneAnchorImageResponse(
            shot_id=request_model.shot_id,
            prompt=request_model.prompt,
            model_name=request_model.model_name,
            aspect_ratio=request_model.aspect_ratio,
            output_path=str(output_path),
            source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
        )

    def _fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="approve",
            review_summary="首帧稳定。",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
            model_name="gemini-2.5-flash",
        )

    captured: dict[str, object] = {}

    def _fake_subprocess(
        *,
        project_root,
        image_path,
        prompt,
        output_path,
        mode,
        resolution,
        duration_seconds,
        aspect_ratio,
        model_name,
        project_url="",
        profile_dir=None,
        headless=True,
    ):
        captured["duration_seconds"] = duration_seconds
        captured["mode"] = mode
        captured["resolution"] = resolution
        captured["aspect_ratio"] = aspect_ratio
        captured["model_name"] = model_name
        captured["project_url"] = project_url
        captured["profile_dir"] = profile_dir
        captured["headless"] = headless
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(
            args=["python", "scripts/manju_one_shot.py"],
            returncode=0,
            stdout="执行成功\n",
            stderr="",
        )

    def _fake_video_audit(**kwargs):
        return SimpleNamespace(
            action="revise",
            selected_issue_ids=["character_drift"],
            review_summary="人物脸部有轻微漂移，建议小修正后重生。",
            prompt_patch="保持人物脸部稳定，不要出现脸部漂移。",
            revised_prompt_main="修正后视频提示词",
            report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", _fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", _fake_review)
    monkeypatch.setattr(cli_module, "_run_manju_one_shot_script", _fake_subprocess)
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", _fake_video_audit)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "林白",
            "--scene-ref",
            "古城门",
            "--storyboard-text",
            "林白在新场景中站定。",
        ],
    )

    assert result.exit_code != 0
    assert captured["mode"] == "普通模式"
    assert captured["resolution"] == "1080p"
    assert captured["aspect_ratio"] == "16:9"
    assert captured["model_name"] == "Seedance1.5-pro"
    assert captured["duration_seconds"] >= 4
    assert "video_review_action: revise" in result.stdout
    assert "status: blocked_by_video_review" in result.stdout or "status: blocked_by_video_review" in result.stderr
