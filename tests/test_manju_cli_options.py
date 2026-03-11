import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app
from app.openclaw.models import SceneAnchorImageResponse, SceneAnchorReviewResponse


runner = CliRunner()


def _write_catalog(project_root: Path) -> None:
    assets_dir = project_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "catalog.json").write_text(
        json.dumps(
            {
                "total_assets": 2,
                "assets": [
                    {
                        "asset_id": "CHAR_LINBAI__v1",
                        "type": "character",
                        "display_name": "Linbai",
                        "jimeng_ref_name": "CHAR_LINBAI__v1",
                        "files": ["assets/characters/linbai.png"],
                        "tags": ["character", "linbai"],
                    },
                    {
                        "asset_id": "SCENE_GATE__v1",
                        "type": "scene",
                        "display_name": "Gate",
                        "jimeng_ref_name": "SCENE_GATE__v1",
                        "files": ["assets/scenes/gate.png"],
                        "tags": ["scene", "gate"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_root / "assets" / "characters").mkdir(parents=True, exist_ok=True)
    (project_root / "assets" / "scenes").mkdir(parents=True, exist_ok=True)
    (project_root / "assets" / "characters" / "linbai.png").write_bytes(b"image")
    (project_root / "assets" / "scenes" / "gate.png").write_bytes(b"image")


def test_run_manju_one_shot_script_passes_optional_flags(monkeypatch, tmp_path: Path) -> None:
    script_path = tmp_path / "scripts" / "manju_one_shot.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('ok')", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_module._run_manju_one_shot_script(
        project_root=tmp_path,
        image_path=tmp_path / "anchor.png",
        prompt="prompt",
        output_path=tmp_path / "out.mp4",
        mode="普通模式",
        resolution="1080p",
        duration_seconds=5,
        aspect_ratio="16:9",
        model_name="Seedance1.5-pro",
        project_url="https://manju.example/project",
        profile_dir=tmp_path / "profile",
        headless=False,
    )

    assert result.returncode == 0
    command = captured["command"]
    assert "--prompt-file" in command
    prompt_file = Path(command[command.index("--prompt-file") + 1])
    assert prompt_file.read_text(encoding="utf-8") == "prompt"
    assert "--prompt" not in command
    assert command[command.index("--mode") + 1] == "normal"
    assert "--project-url" in command
    assert "https://manju.example/project" in command
    assert "--profile-dir" in command
    assert str(tmp_path / "profile") in command
    assert "--headed" in command


def test_run_manju_scene_shot_forwards_runtime_options(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def fake_generate(self, request_model, *, project_root=None):
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

    def fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="approve",
            review_summary="ok",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
            model_name="gemini-2.5-flash",
        )

    captured: dict[str, object] = {}

    def fake_run_script(**kwargs):
        captured.update(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok\n", stderr="")

    def fake_video_audit(**kwargs):
        return SimpleNamespace(
            action="approve",
            selected_issue_ids=[],
            review_summary="ok",
            prompt_patch="",
            revised_prompt_main="",
            report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", fake_review)
    monkeypatch.setattr(cli_module, "_run_manju_one_shot_script", fake_run_script)
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", fake_video_audit)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "Linbai",
            "--scene-ref",
            "Gate",
            "--storyboard-text",
            "Linbai enters the gate.",
            "--manju-profile-dir",
            str(tmp_path / "manju-profile"),
            "--manju-project-url",
            "https://manju.example/project",
            "--manju-headed",
        ],
    )

    assert result.exit_code == 0
    assert captured["project_url"] == "https://manju.example/project"
    assert captured["profile_dir"] == tmp_path / "manju-profile"
    assert captured["headless"] is False
    assert "manju_project_url: https://manju.example/project" in result.stdout
    assert f"manju_profile_dir: {tmp_path / 'manju-profile'}" in result.stdout
    assert "manju_headless: no" in result.stdout


def test_run_manju_scene_shot_retries_anchor_once_on_revise(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    generate_prompts: list[str] = []
    review_actions = iter(
        [
            SceneAnchorReviewResponse(
                shot_id="manju-scene-Linbai-Gate",
                action="revise",
                review_summary="needs patch",
                selected_issue_ids=["first_frame_unusable"],
                prompt_patch="去掉文字",
                revised_prompt="patched prompt",
                model_name="gemini-2.5-flash",
            ),
            SceneAnchorReviewResponse(
                shot_id="manju-scene-Linbai-Gate",
                action="approve",
                review_summary="ok",
                selected_issue_ids=[],
                prompt_patch="",
                revised_prompt="",
                model_name="gemini-2.5-flash",
            ),
        ]
    )

    def fake_generate(self, request_model, *, project_root=None):
        generate_prompts.append(request_model.prompt)
        output_path = tmp_path / "outputs" / "images" / f"{len(generate_prompts)}.png"
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

    def fake_review(self, request_model):
        return next(review_actions)

    def fake_run_script(**kwargs):
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok\n", stderr="")

    def fake_video_audit(**kwargs):
        return SimpleNamespace(
            action="approve",
            selected_issue_ids=[],
            review_summary="ok",
            prompt_patch="",
            revised_prompt_main="",
            report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
        )

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", fake_review)
    monkeypatch.setattr(cli_module, "_run_manju_one_shot_script", fake_run_script)
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", fake_video_audit)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "Linbai",
            "--scene-ref",
            "Gate",
            "--storyboard-text",
            "Linbai enters the gate.",
        ],
    )

    assert result.exit_code == 0
    assert len(generate_prompts) == 2
    assert generate_prompts[1] == "patched prompt"
    assert "anchor_retry: yes" in result.stdout


def test_run_manju_scene_shot_retries_video_once_on_revise(monkeypatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    monkeypatch.setattr(cli_module, "get_config", lambda: SimpleNamespace(project_root=tmp_path))

    def fake_generate(self, request_model, *, project_root=None):
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

    def fake_review(self, request_model):
        return SceneAnchorReviewResponse(
            shot_id=request_model.shot_id,
            action="approve",
            review_summary="ok",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
            model_name="gemini-2.5-flash",
        )

    run_prompts: list[str] = []

    def fake_run_script(**kwargs):
        run_prompts.append(kwargs["prompt"])
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(args=["python"], returncode=0, stdout="ok\n", stderr="")

    audit_results = iter(
        [
            SimpleNamespace(
                action="revise",
                selected_issue_ids=["text_overlay"],
                review_summary="needs patch",
                prompt_patch="remove text",
                revised_prompt_main="patched video prompt",
                report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
            ),
            SimpleNamespace(
                action="approve",
                selected_issue_ids=[],
                review_summary="ok",
                prompt_patch="",
                revised_prompt_main="",
                report_path=str(tmp_path / "outputs" / "reviews" / "audit.html"),
            ),
        ]
    )

    def fake_video_audit(**kwargs):
        return next(audit_results)

    monkeypatch.setattr(cli_module.OpenClawService, "generate_scene_anchor_image", fake_generate)
    monkeypatch.setattr(cli_module.OpenClawService, "review_scene_anchor_image", fake_review)
    monkeypatch.setattr(cli_module, "_run_manju_one_shot_script", fake_run_script)
    monkeypatch.setattr(cli_module, "_run_gemini_auto_audit", fake_video_audit)

    result = runner.invoke(
        app,
        [
            "run-manju-scene-shot",
            "--character-ref",
            "Linbai",
            "--scene-ref",
            "Gate",
            "--storyboard-text",
            "Linbai enters the gate.",
            "--video-output-path",
            str(tmp_path / "final.mp4"),
        ],
    )

    assert result.exit_code == 0
    assert run_prompts == [
        "主体：Linbai；场景：Gate；动作：Linbai enters the gate.；镜头：固定中景；约束：保持固定机位，构图稳定，不运镜，不切换景别，保持人物脸部、发型、服装和场景结构一致，不新增人物，画面中禁止出现字幕、文字、logo、水印和多余界面元素，背景墙面干净。",
        "patched video prompt",
    ]
    assert "video_retry: yes" in result.stdout
