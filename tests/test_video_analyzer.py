from pathlib import Path

from typer.testing import CliRunner

from app.cli import app
from app.video_analyzer.models import CandidateFrame
from app.video_analyzer.service import VideoAnalyzerService

runner = CliRunner()


def test_video_analyzer_prefers_front_facing_stable_frame_for_character_continuity() -> None:
    service = VideoAnalyzerService()
    candidates = [
        CandidateFrame(
            frame_index=72,
            timestamp_seconds=2.4,
            relative_position=0.60,
            frame_path="outputs/frames/front.png",
            scene_tags=["古城门"],
            character_state_tags=["站定", "准备"],
            pose_tags=["正面", "视线朝前"],
            composition_tags=["主体清晰", "近景", "面部可见"],
            action_phase="settled",
            blur_level=0.18,
            exposure_score=0.82,
            subject_visibility=0.92,
        ),
        CandidateFrame(
            frame_index=118,
            timestamp_seconds=3.9,
            relative_position=0.96,
            frame_path="outputs/frames/tail.png",
            scene_tags=["古城门"],
            character_state_tags=["转身"],
            pose_tags=["背身", "视线缺失"],
            composition_tags=["主体完整"],
            action_phase="transition",
            blur_level=0.08,
            exposure_score=0.88,
            subject_visibility=0.83,
        ),
    ]

    result = service.analyze_one_shot(
        "demo.mp4",
        current_shot_summary="当前镜头里角色正在停住。",
        next_shot_summary="下一镜头从人物正面近景开始，角色正面看向前方，状态稳定。",
        candidate_frames=candidates,
    )

    assert result.best_frame is not None
    assert result.best_frame.frame_path == "outputs/frames/front.png"
    assert "姿势连续性" in result.best_frame.best_dimensions or "人物状态连续性" in result.best_frame.best_dimensions


def test_video_analyzer_prefers_scene_complete_frame_when_next_shot_needs_scene_continuity() -> None:
    service = VideoAnalyzerService()
    candidates = [
        CandidateFrame(
            frame_index=64,
            timestamp_seconds=2.13,
            relative_position=0.53,
            frame_path="outputs/frames/hallway.png",
            scene_tags=["走廊", "门框", "长廊透视"],
            character_state_tags=["站定"],
            pose_tags=["侧身"],
            composition_tags=["主体清晰", "中景", "环境完整"],
            action_phase="settled",
            blur_level=0.20,
            exposure_score=0.78,
            subject_visibility=0.84,
        ),
        CandidateFrame(
            frame_index=88,
            timestamp_seconds=2.93,
            relative_position=0.73,
            frame_path="outputs/frames/closeup.png",
            scene_tags=["室外", "广场"],
            character_state_tags=["站定"],
            pose_tags=["正面"],
            composition_tags=["近景", "主体清晰"],
            action_phase="settled",
            blur_level=0.10,
            exposure_score=0.86,
            subject_visibility=0.94,
        ),
    ]

    result = service.analyze_one_shot(
        "demo.mp4",
        current_shot_summary="当前镜头还是室内空间。",
        next_shot_summary="下一镜头需要延续走廊场景，保留门框和长廊透视关系。",
        candidate_frames=candidates,
    )

    assert result.best_frame is not None
    assert result.best_frame.frame_path == "outputs/frames/hallway.png"
    assert "场景连续性" in result.best_frame.best_dimensions


def test_video_analyzer_does_not_pick_blurry_mid_action_frame_just_because_it_is_near_the_tail() -> None:
    service = VideoAnalyzerService()
    candidates = [
        CandidateFrame(
            frame_index=70,
            timestamp_seconds=2.33,
            relative_position=0.58,
            frame_path="outputs/frames/stable.png",
            scene_tags=["古城门"],
            character_state_tags=["站定", "起手前"],
            pose_tags=["正面", "视线朝前"],
            composition_tags=["主体清晰", "主体完整"],
            action_phase="settled",
            blur_level=0.16,
            exposure_score=0.80,
            subject_visibility=0.88,
        ),
        CandidateFrame(
            frame_index=119,
            timestamp_seconds=3.97,
            relative_position=0.97,
            frame_path="outputs/frames/mid_action_blur.png",
            scene_tags=["古城门"],
            character_state_tags=["动作中"],
            pose_tags=["朝向不稳"],
            composition_tags=["主体偏移"],
            action_phase="mid_action",
            blur_level=0.58,
            exposure_score=0.82,
            subject_visibility=0.60,
        ),
    ]

    result = service.analyze_one_shot(
        "demo.mp4",
        current_shot_summary="当前镜头动作接近收束。",
        next_shot_summary="下一镜头需要从角色稳定站定的状态开始。",
        candidate_frames=candidates,
    )

    assert result.best_frame is not None
    assert result.best_frame.frame_path == "outputs/frames/stable.png"
    blurry_candidate = next(frame for frame in result.candidate_frames if frame.frame_path.endswith("mid_action_blur.png"))
    assert blurry_candidate.continuity_score < 0.5


def test_video_analyzer_prefers_interrupted_bridge_frame_when_script_has_pullback_and_dialogue() -> None:
    service = VideoAnalyzerService()
    candidates = [
        CandidateFrame(
            frame_index=88,
            timestamp_seconds=8.5,
            relative_position=0.56,
            frame_path="outputs/frames/pre_interrupt.png",
            scene_tags=["教室", "环境完整"],
            character_state_tags=["暴怒", "出手前"],
            pose_tags=["正面", "高举拳头"],
            composition_tags=["主体清晰", "中景"],
            action_phase="mid_action",
            blur_level=0.18,
            exposure_score=0.83,
            subject_visibility=0.90,
        ),
        CandidateFrame(
            frame_index=115,
            timestamp_seconds=11.5,
            relative_position=0.76,
            frame_path="outputs/frames/interrupted_bridge.png",
            scene_tags=["教室", "环境完整", "多人对峙"],
            character_state_tags=["被拉住", "收势", "怒视对手"],
            pose_tags=["侧身", "视线朝前", "重心稳定"],
            composition_tags=["多人同框", "关系清晰", "中景", "主体完整"],
            action_phase="transition",
            blur_level=0.14,
            exposure_score=0.84,
            subject_visibility=0.93,
        ),
    ]

    current_shot_summary = (
        "周浩天暴怒拍桌，扬起拳头就要砸向林白。"
        "广播突然响起，陈夏娜急促地拉住周浩天的胳膊，让他别浪费时间。"
    )
    next_shot_summary = (
        "周浩天这才收回拳头，恶狠狠地指着林白放狠话。"
        "后面还有陈夏娜鄙夷、跟班哄笑，以及周浩天搂着陈夏娜离开教室。"
    )

    result = service.analyze_one_shot(
        "demo.mp4",
        current_shot_summary=current_shot_summary,
        next_shot_summary=next_shot_summary,
        candidate_frames=candidates,
    )

    assert result.best_frame is not None
    assert result.best_frame.frame_path == "outputs/frames/interrupted_bridge.png"
    best_reason = result.best_frame.reason
    assert "被打断后收势再放话" in best_reason
    assert "人物关系" in best_reason or "场面信息" in best_reason


def test_cli_analyze_one_shot_outputs_fixed_scores(tmp_path: Path) -> None:
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake-video")

    result = runner.invoke(
        app,
        [
            "analyze-one-shot",
            "--video",
            str(video_path),
            "--next-shot",
            "下一镜头需要人物正面稳定承接。",
            "--current-shot",
            "当前镜头已经收束。",
        ],
    )

    assert result.exit_code == 0
    assert '"candidate_frames"' in result.stdout
    assert '"continuity_score"' in result.stdout
    assert '"quality_score"' in result.stdout
    assert '"total_score"' in result.stdout
    assert '"best_frame"' in result.stdout
