import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, create_engine, select

from app.db.models import PromptCacheRecord, RetryRecord, StoryboardRecord, TaskRun, VideoGenerationRecord
from app.jimeng_operator.models import JimengOneShotResult, PromptAuditDecision
from app.openclaw.models import (
    AssetPlannerResponse,
    CatalogAssetSummary,
    PromptComposerResponse,
)
from app.orchestrator import Orchestrator
from app.video_analyzer.models import BestTransitionFrame, TransitionFrameResult


def _build_database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'orchestrator.db').as_posix()}"


def _write_catalog_fixture(tmp_path: Path) -> Path:
    assets_dir = tmp_path / "assets"
    (assets_dir / "characters").mkdir(parents=True, exist_ok=True)
    (assets_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (assets_dir / "characters" / "linbai_1.png").write_bytes(b"char")
    (assets_dir / "characters" / "linbai_2.jpg").write_bytes(b"char-2")
    (assets_dir / "scenes" / "classroom_1.png").write_bytes(b"scene")
    (assets_dir / "scenes" / "classroom_2.jpg").write_bytes(b"scene-2")
    catalog_path = assets_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "total_assets": 2,
                "assets": [
                    {
                        "asset_id": "CHAR_LINBAI__v1",
                        "type": "character",
                        "display_name": "林白",
                        "jimeng_ref_name": "CHAR_LINBAI__v1",
                        "files": ["assets/characters/linbai_2.jpg", "assets/characters/linbai_1.png"],
                        "tags": ["character", "林白"],
                    },
                    {
                        "asset_id": "SCENE_CLASSROOM__v1",
                        "type": "scene",
                        "display_name": "教室",
                        "jimeng_ref_name": "SCENE_CLASSROOM__v1",
                        "files": ["assets/scenes/classroom_2.jpg", "assets/scenes/classroom_1.png"],
                        "tags": ["scene", "教室"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return catalog_path


def _write_two_shot_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "shots.json"
    script_path.write_text(
        json.dumps(
            {
                "shots": [
                    {
                        "storyboard_id": "shot_001",
                        "storyboard_text": "林白在教室门口抬眼看向周浩天，气氛剑拔弩张。",
                        "style_summary": "国风校园玄幻，人物关系清晰。",
                        "current_shot_summary": "林白站定，准备应对冲突。",
                    },
                    {
                        "storyboard_id": "shot_002",
                        "storyboard_text": "周浩天收拳后怒指林白，陈夏娜伸手制止他。",
                        "style_summary": "连续性优先，动作衔接自然。",
                        "current_shot_summary": "周浩天收势后继续怒视林白。",
                        "continuity_requirements": "严格承接上一镜头的最佳承接帧。",
                    },
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return script_path


def _write_manju_scene_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "manju_scene.json"
    script_path.write_text(
        json.dumps(
            {
                "workflow_mode": "manju_scene_shot",
                "storyboard_id": "manju_scene_001",
                "character_ref": "Linbai",
                "scene_ref": "Gate",
                "storyboard_text": "Linbai stands still in the gate scene.",
                "video_output_path": str(tmp_path / "outputs" / "videos" / "manju_scene_001.mp4"),
                "anchor_output_path": str(tmp_path / "outputs" / "images" / "manju_scene_001.png"),
                "manju_headless": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return script_path


def _write_manju_scene_batch_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "manju_scene_batch.json"
    script_path.write_text(
        json.dumps(
            {
                "workflow_mode": "manju_scene_batch",
                "character_ref": "CHAR_LINBAI__v1",
                "scene_ref": "SCENE_CLASSROOM__v1",
                "manju_headless": True,
                "shots": [
                    {
                        "storyboard_id": "manju_batch_001",
                        "storyboard_text": "Linbai stands still in the classroom.",
                        "shot_size": "中景",
                        "camera_angle": "三分之二前侧",
                    },
                    {
                        "storyboard_id": "manju_batch_002",
                        "storyboard_text": "Linbai turns and continues speaking in the classroom.",
                        "shot_size": "中近景",
                        "camera_angle": "正侧面",
                        "cut_reason": "动作延续换角度",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return script_path


class FakeOpenClawClient:
    def __init__(self, *, fail_on_planner: bool = False) -> None:
        self.fail_on_planner = fail_on_planner
        self.planner_requests: list[object] = []
        self.composer_requests: list[object] = []

    def build_asset_planner_request_from_catalog(self, **kwargs):
        request = SimpleNamespace(**kwargs)
        self.planner_requests.append(request)
        return request

    def run_asset_planner(self, request_model) -> AssetPlannerResponse:
        if self.fail_on_planner:
            raise RuntimeError("openclaw planner exploded")
        return AssetPlannerResponse(
            storyboard_id=request_model.storyboard_id,
            selected_assets=[
                CatalogAssetSummary(
                    asset_id="CHAR_LINBAI__v1",
                    type="character",
                    display_name="林白",
                    jimeng_ref_name="CHAR_LINBAI__v1",
                    tags=["character", "林白"],
                ),
                CatalogAssetSummary(
                    asset_id="SCENE_CLASSROOM__v1",
                    type="scene",
                    display_name="教室",
                    jimeng_ref_name="SCENE_CLASSROOM__v1",
                    tags=["scene", "教室"],
                ),
            ],
            selection_reason="命中人物与场景",
            reference_assets=["CHAR_LINBAI__v1", "SCENE_CLASSROOM__v1"],
            reference_strategy="character+scene",
            must_keep=["CHAR_LINBAI__v1"],
            drop_if_needed=[],
        )

    def run_prompt_composer(self, request_model: object) -> PromptComposerResponse:
        self.composer_requests.append(request_model)
        shot_id = request_model.shot_id or request_model.storyboard_id
        previous = request_model.previous_frame_summary.strip()
        prompt_main = f"{request_model.storyboard_text}。"
        if previous:
            prompt_main = f"{prompt_main} 承接：{previous}"
        return PromptComposerResponse(
            storyboard_id=request_model.storyboard_id,
            shot_id=shot_id,
            prompt_main=prompt_main,
            prompt_negative="避免模糊和穿模",
            ref_assets_in_order=[asset.asset_id for asset in request_model.selected_assets],
            continuity_notes=previous or "首镜头无需承接",
        )


@dataclass
class FakeJimengOperator:
    requests: list[object] = field(default_factory=list)
    close_count: int = 0

    def run_one_shot(self, request) -> JimengOneShotResult:
        self.requests.append(request)
        return JimengOneShotResult(
            shot_id=request.shot_id,
            prompt_main=request.prompt_main,
            ref_assets_in_order=list(request.ref_assets_in_order),
            submitted=True,
            generation_completed=True,
            ready_for_download=True,
        )

    def download_latest_video(self, output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video")
        return True

    def close(self) -> None:
        self.close_count += 1


class FakeJimengFactory:
    def __init__(self) -> None:
        self.instances: list[FakeJimengOperator] = []

    def __call__(self) -> FakeJimengOperator:
        operator = FakeJimengOperator()
        self.instances.append(operator)
        return operator


class FakeAuditRunner:
    def __init__(self, action: str = "approve") -> None:
        self.action = action
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs) -> PromptAuditDecision:
        self.calls.append(kwargs)
        return PromptAuditDecision(
            action=self.action,
            review_summary=f"audit:{self.action}",
            report_path=str(kwargs["report_path"]),
        )


class SequencedAuditRunner:
    def __init__(self, actions: list[str]) -> None:
        self.actions = list(actions)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs) -> PromptAuditDecision:
        self.calls.append(kwargs)
        action = self.actions.pop(0) if self.actions else "approve"
        return PromptAuditDecision(
            action=action,
            review_summary=f"audit:{action}",
            report_path=str(kwargs["report_path"]),
        )


class FakeVideoAnalyzer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def analyze_one_shot(self, video_path: str, *, next_shot_summary: str, current_shot_summary: str = "", candidate_frames=None):
        self.calls.append(
            {
                "video_path": video_path,
                "current_shot_summary": current_shot_summary,
                "next_shot_summary": next_shot_summary,
            }
        )
        return TransitionFrameResult(
            video_path=video_path,
            current_shot_summary=current_shot_summary,
            next_shot_summary=next_shot_summary,
            best_frame=BestTransitionFrame(
                frame_index=12,
                timestamp_seconds=1.25,
                frame_path="frames/best.png",
                continuity_score=0.91,
                quality_score=0.88,
                total_score=0.90,
                reason="动作已收势，适合下一镜头接续",
                best_dimensions=["动作收束", "主体清晰"],
            ),
        )


class FakeTransitionExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, float, Path]] = []

    def __call__(self, video_path: Path, timestamp_seconds: float, output_path: Path) -> Path:
        self.calls.append((video_path, timestamp_seconds, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"transition")
        return output_path


class FakeSceneShotRunner:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs) -> dict[str, str]:
        self.calls.append(kwargs)
        if self.should_fail:
            raise RuntimeError("scene shot failed")
        output_path = Path(str(kwargs["video_output_path"]))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"scene-video")
        anchor_output_path = Path(str(kwargs["anchor_output_path"]))
        anchor_output_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_output_path.write_bytes(b"scene-anchor")
        return {
            "output_path": str(output_path),
            "anchor_image_path": str(anchor_output_path),
            "audit_report_path": str(tmp_path := output_path.parent.parent / "reviews" / "manju_scene_001_audit.html"),
            "video_prompt": "scene prompt",
        }


def test_orchestrator_run_executes_real_two_shot_chain(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_two_shot_script(tmp_path)
    _write_catalog_fixture(tmp_path)

    openclaw = FakeOpenClawClient()
    jimeng_factory = FakeJimengFactory()
    audit_runner = FakeAuditRunner(action="approve")
    analyzer = FakeVideoAnalyzer()
    transition_extractor = FakeTransitionExtractor()
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        openclaw=openclaw,
        jimeng_operator_factory=jimeng_factory,
        video_audit_runner=audit_runner,
        video_analyzer=analyzer,
        transition_frame_extractor=transition_extractor,
    )

    result = orchestrator.run(script_path=str(script_path))

    assert result["status"] == "success"
    assert result["workflow_mode"] == "real_multi_shot"
    assert result["shot_count"] == 2
    assert result["steps"]["feishu_sync"]["status"] == "skipped"
    assert result["steps"]["asset_catalog"]["status"] == "loaded"
    assert len(result["steps"]["shots"]) == 2
    assert len(jimeng_factory.instances) == 2
    assert len(audit_runner.calls) == 2
    assert len(analyzer.calls) == 1
    assert len(transition_extractor.calls) == 1
    assert len(openclaw.composer_requests) == 2
    assert "上一镜头最佳承接帧位于" in openclaw.composer_requests[1].previous_frame_summary

    first_request = jimeng_factory.instances[0].requests[0]
    second_request = jimeng_factory.instances[1].requests[0]
    assert len(first_request.reference_file_paths) == 2
    assert second_request.reference_file_paths[0].name.endswith("_transition.png")

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.id.asc())).all()
        prompt_caches = session.exec(select(PromptCacheRecord).order_by(PromptCacheRecord.id.asc())).all()
        video_records = session.exec(select(VideoGenerationRecord).order_by(VideoGenerationRecord.id.asc())).all()

    assert task_run.status == "success"
    assert len(storyboards) == 2
    assert all(storyboard.status == "completed" for storyboard in storyboards)
    assert len(prompt_caches) == 2
    assert json.loads(prompt_caches[0].reference_asset_ids) == ["CHAR_LINBAI__v1", "SCENE_CLASSROOM__v1"]
    assert json.loads(prompt_caches[1].reference_asset_ids) == [
        "CHAR_LINBAI__v1",
        "SCENE_CLASSROOM__v1",
        "@TransitionFrame",
    ]
    assert len(video_records) == 2
    assert all(video_record.status == "completed" for video_record in video_records)
    assert all(video_record.video_path for video_record in video_records)
    assert task_run.current_stage == "completed"
    assert task_run.started_at is not None
    assert task_run.finished_at is not None
    assert task_run.retry_count == 0
    assert all(storyboard.started_at is not None for storyboard in storyboards)
    assert all(storyboard.finished_at is not None for storyboard in storyboards)
    assert all(video_record.finished_at is not None for video_record in video_records)


def test_orchestrator_run_marks_failed_shot_when_audit_rejects(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_two_shot_script(tmp_path)
    _write_catalog_fixture(tmp_path)

    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        openclaw=FakeOpenClawClient(),
        jimeng_operator_factory=FakeJimengFactory(),
        video_audit_runner=FakeAuditRunner(action="revise"),
        video_analyzer=FakeVideoAnalyzer(),
        transition_frame_extractor=FakeTransitionExtractor(),
    )

    with pytest.raises(RuntimeError, match="自动审查未通过"):
        orchestrator.run(script_path=str(script_path))

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.id.asc())).all()
        video_records = session.exec(select(VideoGenerationRecord).order_by(VideoGenerationRecord.id.asc())).all()
        prompt_caches = session.exec(select(PromptCacheRecord).order_by(PromptCacheRecord.id.asc())).all()

    assert task_run.status == "failed:shot_1"
    assert "自动审查未通过" in task_run.error_message
    assert len(storyboards) == 1
    assert storyboards[0].status == "failed:shot_1"
    assert len(video_records) == 1
    assert video_records[0].status == "failed:shot_1"
    assert len(prompt_caches) == 1


def test_orchestrator_resume_task_continues_from_latest_failed_shot(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_two_shot_script(tmp_path)
    _write_catalog_fixture(tmp_path)

    jimeng_factory = FakeJimengFactory()
    audit_runner = SequencedAuditRunner(["approve", "revise", "approve"])
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        openclaw=FakeOpenClawClient(),
        jimeng_operator_factory=jimeng_factory,
        video_audit_runner=audit_runner,
        video_analyzer=FakeVideoAnalyzer(),
        transition_frame_extractor=FakeTransitionExtractor(),
    )

    with pytest.raises(RuntimeError, match="自动审查未通过"):
        orchestrator.run(script_path=str(script_path))

    resumed = orchestrator.resume_task(1)

    assert resumed["status"] == "success"
    assert resumed["resumed"] is True
    assert resumed["resumed_from_shot_index"] == 2
    assert len(jimeng_factory.instances) == 3

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.shot_index.asc())).all()
        retries = session.exec(select(TaskRun)).all()
        retry_records = session.exec(select(RetryRecord).order_by(RetryRecord.id.asc())).all()

    assert task_run.status == "success"
    assert task_run.retry_count == 1
    assert task_run.finished_at is not None
    assert len(retries) == 1
    assert len(storyboards) == 2
    assert storyboards[0].status == "completed"
    assert storyboards[0].retry_count == 0
    assert storyboards[0].transition_frame_path
    assert storyboards[1].status == "completed"
    assert storyboards[1].retry_count == 1
    assert any(record.stage_name.startswith("resume:") for record in retry_records)
    assert any(record.stage_name == "shot:shot_002" for record in retry_records)


def test_orchestrator_retry_shot_reruns_from_explicit_shot_id(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_two_shot_script(tmp_path)
    _write_catalog_fixture(tmp_path)

    jimeng_factory = FakeJimengFactory()
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        openclaw=FakeOpenClawClient(),
        jimeng_operator_factory=jimeng_factory,
        video_audit_runner=FakeAuditRunner(action="approve"),
        video_analyzer=FakeVideoAnalyzer(),
        transition_frame_extractor=FakeTransitionExtractor(),
    )

    result = orchestrator.run(script_path=str(script_path))
    retried = orchestrator.retry_shot("shot_002", task_run_id=result["task_run_id"])

    assert retried["status"] == "success"
    assert retried["resumed"] is True
    assert retried["resumed_from_shot_index"] == 2
    assert len(jimeng_factory.instances) == 3

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.shot_index.asc())).all()

    assert task_run.retry_count == 1
    assert storyboards[0].retry_count == 0
    assert storyboards[1].retry_count == 1


def test_orchestrator_run_executes_manju_scene_shot_workflow(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_manju_scene_script(tmp_path)
    _write_catalog_fixture(tmp_path)
    scene_runner = FakeSceneShotRunner()
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        scene_shot_runner=scene_runner,
    )

    result = orchestrator.run(script_path=str(script_path))

    assert result["status"] == "success"
    assert result["workflow_mode"] == "manju_scene_shot"
    assert result["shot_count"] == 1
    assert result["steps"]["manju_scene_shot"]["status"] == "completed"
    assert len(scene_runner.calls) == 1

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboard = session.exec(select(StoryboardRecord)).one()
        video_record = session.exec(select(VideoGenerationRecord)).one()
        prompt_cache = session.exec(select(PromptCacheRecord)).one()

    assert task_run.status == "success"
    assert task_run.workflow_mode == "manju_scene_shot"
    assert storyboard.status == "completed"
    assert video_record.status == "completed"
    assert video_record.video_path.endswith("manju_scene_001.mp4")
    assert prompt_cache.prompt_text == "scene prompt"
    assert json.loads(prompt_cache.reference_asset_ids) == ["@SceneAnchorImage"]


def test_orchestrator_run_executes_manju_scene_batch_workflow(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_manju_scene_batch_script(tmp_path)
    _write_catalog_fixture(tmp_path)
    scene_runner = FakeSceneShotRunner()
    analyzer = FakeVideoAnalyzer()
    transition_extractor = FakeTransitionExtractor()
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        scene_shot_runner=scene_runner,
        video_analyzer=analyzer,
        transition_frame_extractor=transition_extractor,
    )

    result = orchestrator.run(script_path=str(script_path))

    assert result["status"] == "success"
    assert result["workflow_mode"] == "manju_scene_batch"
    assert result["shot_count"] == 2
    assert len(scene_runner.calls) == 2
    assert result["steps"]["scene_batch"]["status"] == "completed"
    assert scene_runner.calls[0]["character_ref"] == "CHAR_LINBAI__v1"
    assert scene_runner.calls[0]["scene_ref"] == "SCENE_CLASSROOM__v1"
    assert scene_runner.calls[0]["input_anchor_image_path"] == ""
    assert scene_runner.calls[1]["input_anchor_image_path"] == ""
    assert scene_runner.calls[1]["continuity_ref_image_path"].endswith("manju_batch_001_transition.png")
    assert scene_runner.calls[1]["shot_size"] == "中近景"
    assert scene_runner.calls[1]["camera_angle"] == "正侧面"
    assert Path(result["steps"]["scene_batch"]["shots"][0]["character_reference_image"]).name == "linbai_1.png"
    assert Path(result["steps"]["scene_batch"]["shots"][0]["scene_reference_image"]).name == "classroom_1.png"
    assert result["steps"]["scene_batch"]["shots"][0]["transition_frame_path"].endswith("manju_batch_001_transition.png")
    assert result["steps"]["scene_batch"]["shots"][1]["anchor_source"] == "continuity_ref"
    assert result["steps"]["scene_batch"]["shots"][1]["continuity_ref_image_path"].endswith(
        "manju_batch_001_transition.png"
    )
    assert len(analyzer.calls) == 1
    assert len(transition_extractor.calls) == 1

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.shot_index.asc())).all()
        video_records = session.exec(select(VideoGenerationRecord).order_by(VideoGenerationRecord.id.asc())).all()

    assert task_run.status == "success"
    assert task_run.workflow_mode == "manju_scene_batch"
    assert len(storyboards) == 2
    assert all(storyboard.status == "completed" for storyboard in storyboards)
    assert storyboards[0].transition_frame_path
    assert len(video_records) == 2
    assert all(video_record.status == "completed" for video_record in video_records)


def test_orchestrator_manju_scene_batch_force_regenerates_anchor_and_keeps_pet_refs(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    _write_catalog_fixture(tmp_path)
    provided_anchor = tmp_path / "provided_anchor.png"
    provided_anchor.write_bytes(b"anchor")
    script_path = tmp_path / "manju_scene_batch_force.json"
    script_path.write_text(
        json.dumps(
            {
                "workflow_mode": "manju_scene_batch",
                "character_ref": "CHAR_LINBAI__v1",
                "scene_ref": "SCENE_CLASSROOM__v1",
                "pet_refs": ["dog"],
                "input_anchor_image_path": str(provided_anchor),
                "manju_headless": True,
                "shots": [
                    {
                        "storyboard_id": "manju_batch_force_001",
                        "storyboard_text": "Linbai stands with the dog.",
                        "force_regenerate_anchor": True,
                    },
                    {
                        "storyboard_id": "manju_batch_force_002",
                        "storyboard_text": "Linbai continues in the same classroom with the dog.",
                        "input_anchor_image_path": "",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    scene_runner = FakeSceneShotRunner()
    analyzer = FakeVideoAnalyzer()
    transition_extractor = FakeTransitionExtractor()
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        scene_shot_runner=scene_runner,
        video_analyzer=analyzer,
        transition_frame_extractor=transition_extractor,
    )

    result = orchestrator.run(script_path=str(script_path))

    assert result["status"] == "success"
    assert scene_runner.calls[0]["pet_refs"] == ["dog"]
    assert scene_runner.calls[0]["force_regenerate_anchor"] is True
    assert scene_runner.calls[0]["input_anchor_image_path"] == ""
    assert scene_runner.calls[1]["input_anchor_image_path"] == ""
    assert scene_runner.calls[1]["continuity_ref_image_path"].endswith("manju_batch_force_001_transition.png")


def test_orchestrator_resume_retries_manju_scene_shot_workflow(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_manju_scene_script(tmp_path)
    _write_catalog_fixture(tmp_path)
    failing_runner = FakeSceneShotRunner(should_fail=True)
    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        scene_shot_runner=failing_runner,
    )

    with pytest.raises(RuntimeError, match="scene shot failed"):
        orchestrator.run(script_path=str(script_path))

    succeeding_runner = FakeSceneShotRunner()
    orchestrator.scene_shot_runner = succeeding_runner
    resumed = orchestrator.resume_task(1)

    assert resumed["status"] == "success"
    assert resumed["workflow_mode"] == "manju_scene_shot"
    assert resumed["resumed"] is True
    assert len(succeeding_runner.calls) == 1

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboard = session.exec(select(StoryboardRecord)).one()
        video_record = session.exec(select(VideoGenerationRecord)).one()
        retry_records = session.exec(select(RetryRecord).order_by(RetryRecord.id.asc())).all()

    assert task_run.retry_count == 1
    assert storyboard.retry_count == 1
    assert video_record.retry_count == 1
    assert any(record.stage_name.startswith("resume:task:") for record in retry_records)


def test_orchestrator_resume_retries_manju_scene_batch_from_failed_shot(tmp_path: Path) -> None:
    database_url = _build_database_url(tmp_path)
    script_path = _write_manju_scene_batch_script(tmp_path)
    _write_catalog_fixture(tmp_path)
    failing_runner = FakeSceneShotRunner()
    succeeding_runner = FakeSceneShotRunner()
    analyzer = FakeVideoAnalyzer()
    transition_extractor = FakeTransitionExtractor()

    def flaky_scene_runner(**kwargs):
        if kwargs["storyboard_id"] == "manju_batch_002":
            raise RuntimeError("scene shot failed")
        return failing_runner(**kwargs)

    orchestrator = Orchestrator(
        database_url=database_url,
        project_root=tmp_path,
        scene_shot_runner=flaky_scene_runner,
        video_analyzer=analyzer,
        transition_frame_extractor=transition_extractor,
    )

    with pytest.raises(RuntimeError, match="scene shot failed"):
        orchestrator.run(script_path=str(script_path))

    orchestrator.scene_shot_runner = succeeding_runner
    resumed = orchestrator.resume_task(1)

    assert resumed["status"] == "success"
    assert resumed["workflow_mode"] == "manju_scene_batch"
    assert resumed["resumed"] is True
    assert resumed["resumed_from_shot_index"] == 2
    assert len(succeeding_runner.calls) == 1
    assert succeeding_runner.calls[0]["storyboard_id"] == "manju_batch_002"
    assert succeeding_runner.calls[0]["input_anchor_image_path"] == ""
    assert succeeding_runner.calls[0]["continuity_ref_image_path"].endswith("manju_batch_001_transition.png")

    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        task_run = session.exec(select(TaskRun)).one()
        storyboards = session.exec(select(StoryboardRecord).order_by(StoryboardRecord.shot_index.asc())).all()
        retry_records = session.exec(select(RetryRecord).order_by(RetryRecord.id.asc())).all()

    assert task_run.retry_count == 1
    assert task_run.status == "success"
    assert storyboards[0].retry_count == 0
    assert storyboards[1].retry_count == 1
    assert any(record.stage_name == "shot:manju_batch_002" for record in retry_records)
