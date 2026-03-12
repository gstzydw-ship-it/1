import json
from pathlib import Path

from app.script_splitter import ScriptSplitRequest, ScriptSplitterService


def test_script_splitter_breaks_dense_script_into_small_shots() -> None:
    service = ScriptSplitterService()
    result = service.split_script(
        ScriptSplitRequest(
            script_text=(
                "场景：教室内\n"
                "林白猛地站起，盯住周浩天。周浩天拍桌起身：你再说一遍？"
                "这时陈夏岚快步上前，伸手拦住两人。林白压住情绪，继续看着周浩天。"
            ),
            character_ref="CHAR_LINBAI__v1",
            scene_ref="SCENE_CLASSROOM__v1",
            shot_prefix="classroom",
            max_chars_per_shot=36,
            max_units_per_shot=2,
        )
    )

    assert result.workflow_mode == "manju_scene_batch"
    assert len(result.shots) >= 3
    assert result.shots[0].storyboard_id == "classroom_001"
    assert all(shot.character_ref == "CHAR_LINBAI__v1" for shot in result.shots)
    assert all(shot.scene_ref == "SCENE_CLASSROOM__v1" for shot in result.shots)
    assert result.shots[0].shot_kind
    assert result.shots[0].shot_size
    assert result.shots[0].camera_angle
    assert result.shots[0].cut_reason
    assert any("拍桌起身" in shot.storyboard_text for shot in result.shots)
    assert any("陈夏岚快步上前" in shot.storyboard_text for shot in result.shots)
    assert "动作从上一镜头收尾处继续" in result.shots[-1].continuity_requirements


def test_script_splitter_payload_is_ready_for_manju_scene_batch() -> None:
    service = ScriptSplitterService()
    result = service.split_script(
        ScriptSplitRequest(
            script_text="场景：室内。角色抬头看向前方，然后轻微抬手示意。",
            character_ref="CHAR_ManjuTest__v1",
            scene_ref="SCENE_TestRoom__v1",
            shot_prefix="manju",
        )
    )
    payload = service.to_payload(result)

    assert payload["workflow_mode"] == "manju_scene_batch"
    assert payload["character_ref"] == "CHAR_ManjuTest__v1"
    assert payload["scene_ref"] == "SCENE_TestRoom__v1"
    assert payload["shots"][0]["storyboard_id"] == "manju_001"
    assert payload["shots"][0]["style_summary"]
    assert payload["shots"][0]["shot_size"]
    assert payload["shots"][0]["camera_angle"]
    assert payload["shots"][0]["cut_reason"]


def test_split_script_cli_writes_json(monkeypatch, tmp_path: Path) -> None:
    import app.cli as cli_module
    from app.cli import app
    from typer.testing import CliRunner

    runner = CliRunner()
    monkeypatch.setattr(cli_module, "get_config", lambda: type("Cfg", (), {"project_root": tmp_path})())
    script_path = tmp_path / "script.txt"
    output_path = tmp_path / "out.json"
    script_path.write_text("场景：办公室\n角色推门进来。角色停下脚步，抬头观察四周。", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "split-script",
            "--script-path",
            str(script_path),
            "--character-ref",
            "CHAR_TEST__v1",
            "--scene-ref",
            "SCENE_OFFICE__v1",
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["workflow_mode"] == "manju_scene_batch"
    assert payload["character_ref"] == "CHAR_TEST__v1"
    assert payload["scene_ref"] == "SCENE_OFFICE__v1"
    assert len(payload["shots"]) >= 1


def test_script_splitter_separates_dialogue_from_following_action() -> None:
    service = ScriptSplitterService()
    result = service.split_script(
        ScriptSplitRequest(
            script_text=(
                "林白（笑了笑）：林氏千金，常上新闻，想不认识都难。"
                "林可儿拍了拍身上的灰尘，站直身子，郑重地看着林白。"
            ),
            shot_prefix="dialogue",
            max_chars_per_shot=80,
            max_units_per_shot=2,
        )
    )

    assert len(result.shots) >= 2
    assert result.shots[0].storyboard_text.startswith("林白（笑了笑）：")
    assert "林可儿拍了拍身上的灰尘" in result.shots[1].storyboard_text
    assert result.shots[0].shot_kind == "dialogue"
    assert result.shots[0].shot_size == "中近景"


def test_script_splitter_keeps_dialogue_continuation_before_action() -> None:
    service = ScriptSplitterService()
    result = service.split_script(
        ScriptSplitRequest(
            script_text=(
                "林可儿（语气郑重、语速平缓）：你今天救了我的命。"
                "以后有需要，尽管开口，我一定帮。"
                "林白眼睛一亮，搓着手，有些不好意思地凑了过去。"
            ),
            shot_prefix="continuity",
            max_chars_per_shot=80,
            max_units_per_shot=2,
        )
    )

    assert len(result.shots) >= 2
    assert "以后有需要，尽管开口，我一定帮。" in result.shots[1].storyboard_text
    assert result.shots[1].storyboard_text.startswith("林可儿（语气郑重、语速平缓）：")
    assert "林白眼睛一亮" in result.shots[2].storyboard_text
    assert result.shots[2].shot_kind == "reaction"
    assert result.shots[2].shot_size == "近景"
