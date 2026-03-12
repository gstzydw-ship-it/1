"""流程编排服务。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, select

from app.asset_catalog import AssetCatalogService, load_asset_catalog, resolve_catalog_asset_reference
from app.db.engine import build_engine
from app.db.models import PromptCacheRecord, RetryRecord, StoryboardRecord, TaskRun, VideoGenerationRecord
from app.jimeng_operator.models import JimengOneShotRequest, PromptAuditDecision
from app.jimeng_operator.web_operator import JimengWebOperator, build_default_jimeng_config
from app.openclaw import CatalogAssetSummary, OpenClawClient, PromptComposerRequest
from app.prompt_cache import PromptCacheService
from app.video_analyzer import VideoAnalyzerService
from app.video_analyzer.analyze import extract_transition_frame

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Orchestrator:
    """统一编排当前恢复版工作流。"""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        project_root: Path | None = None,
        openclaw: object | None = None,
        jimeng_operator_factory: object | None = None,
        scene_shot_runner: object | None = None,
        video_audit_runner: object | None = None,
        video_analyzer: object | None = None,
        transition_frame_extractor: object | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        default_database_url = f"sqlite:///{(self.project_root / 'data' / 'video_agent.db').as_posix()}"
        self.database_url = database_url or default_database_url
        self.engine = build_engine(self.database_url)
        SQLModel.metadata.create_all(self.engine)

        self.asset_catalog = AssetCatalogService()
        self.openclaw = openclaw or OpenClawClient()
        self.prompt_cache = PromptCacheService()
        self.video_analyzer = video_analyzer or VideoAnalyzerService()
        self.transition_frame_extractor = transition_frame_extractor or extract_transition_frame
        self.video_audit_runner = video_audit_runner or self._default_audit_runner
        self.jimeng_operator_factory = jimeng_operator_factory or self._default_jimeng_operator_factory
        self.scene_shot_runner = scene_shot_runner or self._default_scene_shot_runner

    def run(self, script_path: str | None = None) -> dict[str, Any]:
        if not script_path:
            return self._run_placeholder(script_path)

        resolved_script_path = Path(script_path).resolve()
        task_payload = self._load_task_payload(resolved_script_path)
        workflow_mode = self._detect_workflow_mode(task_payload)

        task_run = TaskRun(
            task_name="orchestrator.run",
            script_path=str(resolved_script_path),
            workflow_mode=workflow_mode,
            status="running",
            current_stage="bootstrap",
            started_at=_utc_now(),
        )
        with Session(self.engine) as session:
            session.add(task_run)
            session.commit()
            session.refresh(task_run)
        if workflow_mode == "manju_scene_shot":
            return self._execute_manju_scene_task(task_run_id=task_run.id, task_payload=task_payload, resumed=False)
        if workflow_mode == "manju_scene_batch":
            return self._execute_manju_scene_batch_task(
                task_run_id=task_run.id,
                task_payload=task_payload,
                start_shot_index=1,
                resumed=False,
            )
        return self._execute_task(task_run_id=task_run.id, start_shot_index=1, resumed=False)

    def resume_task(self, task_run_id: int, *, shot_id: str | None = None) -> dict[str, Any]:
        with Session(self.engine) as session:
            task_run = session.get(TaskRun, task_run_id)
            if task_run is None:
                raise ValueError(f"任务不存在: {task_run_id}")
            if task_run.workflow_mode == "manju_scene_shot":
                task_run.retry_count += 1
                task_run.status = "running"
                task_run.current_stage = "resume:scene_shot"
                task_run.error_message = None
                task_run.finished_at = None
                session.add(
                    RetryRecord(
                        task_run_id=task_run.id,
                        stage_name=f"resume:task:{task_run.id}",
                        retry_count=task_run.retry_count,
                        last_error=None,
                    )
                )
                session.add(task_run)
                session.commit()
                task_payload = self._load_task_payload(Path(task_run.script_path or ""))
                return self._execute_manju_scene_task(task_run_id=task_run.id, task_payload=task_payload, resumed=True)

            storyboards = session.exec(
                select(StoryboardRecord)
                .where(StoryboardRecord.task_run_id == task_run_id)
                .order_by(StoryboardRecord.shot_index.asc())
            ).all()

            start_shot_index = self._resolve_resume_shot_index(storyboards, shot_id=shot_id)
            start_storyboard = next(
                (storyboard for storyboard in storyboards if storyboard.shot_index == start_shot_index),
                None,
            )
            task_run.retry_count += 1
            task_run.status = "running"
            task_run.current_stage = f"resume:shot_{start_shot_index}"
            task_run.error_message = None
            task_run.finished_at = None
            session.add(
                RetryRecord(
                    task_run_id=task_run.id,
                    stage_name=f"resume:task:{task_run.id}",
                    retry_count=task_run.retry_count,
                    last_error=None,
                )
            )
            session.add(
                RetryRecord(
                    task_run_id=task_run.id,
                    stage_name=f"shot:{start_storyboard.storyboard_key if start_storyboard else shot_id or start_shot_index}",
                    retry_count=task_run.retry_count,
                    last_error=None,
                )
            )
            session.add(task_run)
            session.commit()

        if task_run.workflow_mode == "manju_scene_batch":
            task_payload = self._load_task_payload(Path(task_run.script_path or ""))
            return self._execute_manju_scene_batch_task(
                task_run_id=task_run_id,
                task_payload=task_payload,
                start_shot_index=start_shot_index,
                resumed=True,
            )
        return self._execute_task(task_run_id=task_run_id, start_shot_index=start_shot_index, resumed=True)

    def retry_shot(self, shot_id: str, *, task_run_id: int | None = None) -> dict[str, Any]:
        with Session(self.engine) as session:
            if task_run_id is None:
                statement = select(StoryboardRecord).where(StoryboardRecord.storyboard_key == shot_id).order_by(
                    StoryboardRecord.id.desc()
                )
                storyboard = session.exec(statement).first()
                if storyboard is None or storyboard.task_run_id is None:
                    raise ValueError(f"镜头不存在: {shot_id}")
                task_run_id = storyboard.task_run_id
        return self.resume_task(task_run_id, shot_id=shot_id)

    def resume_task(self, task_run_id: int, *, shot_id: str | None = None) -> dict[str, Any]:
        workflow_mode = ""
        script_path = ""
        with Session(self.engine) as session:
            task_run = session.get(TaskRun, task_run_id)
            if task_run is None:
                raise ValueError(f"浠诲姟涓嶅瓨鍦? {task_run_id}")
            workflow_mode = str(task_run.workflow_mode or "")
            script_path = str(task_run.script_path or "")
            if workflow_mode == "manju_scene_shot":
                task_run.retry_count += 1
                task_run.status = "running"
                task_run.current_stage = "resume:scene_shot"
                task_run.error_message = None
                task_run.finished_at = None
                session.add(
                    RetryRecord(
                        task_run_id=task_run.id,
                        stage_name=f"resume:task:{task_run.id}",
                        retry_count=task_run.retry_count,
                        last_error=None,
                    )
                )
                session.add(task_run)
                session.commit()
                task_payload = self._load_task_payload(Path(script_path))
                return self._execute_manju_scene_task(task_run_id=task_run.id, task_payload=task_payload, resumed=True)

            storyboards = session.exec(
                select(StoryboardRecord)
                .where(StoryboardRecord.task_run_id == task_run_id)
                .order_by(StoryboardRecord.shot_index.asc())
            ).all()

            start_shot_index = self._resolve_resume_shot_index(storyboards, shot_id=shot_id)
            start_storyboard = next(
                (storyboard for storyboard in storyboards if storyboard.shot_index == start_shot_index),
                None,
            )
            task_run.retry_count += 1
            task_run.status = "running"
            task_run.current_stage = f"resume:shot_{start_shot_index}"
            task_run.error_message = None
            task_run.finished_at = None
            session.add(
                RetryRecord(
                    task_run_id=task_run.id,
                    stage_name=f"resume:task:{task_run.id}",
                    retry_count=task_run.retry_count,
                    last_error=None,
                )
            )
            session.add(
                RetryRecord(
                    task_run_id=task_run.id,
                    stage_name=f"shot:{start_storyboard.storyboard_key if start_storyboard else shot_id or start_shot_index}",
                    retry_count=task_run.retry_count,
                    last_error=None,
                )
            )
            session.add(task_run)
            session.commit()

        if workflow_mode == "manju_scene_batch":
            task_payload = self._load_task_payload(Path(script_path))
            return self._execute_manju_scene_batch_task(
                task_run_id=task_run_id,
                task_payload=task_payload,
                start_shot_index=start_shot_index,
                resumed=True,
            )
        return self._execute_task(task_run_id=task_run_id, start_shot_index=start_shot_index, resumed=True)

    def _execute_task(self, *, task_run_id: int, start_shot_index: int, resumed: bool) -> dict[str, Any]:
        with Session(self.engine) as session:
            task_run = session.get(TaskRun, task_run_id)
            if task_run is None:
                raise ValueError(f"任务不存在: {task_run_id}")
            if not task_run.script_path:
                raise RuntimeError("任务缺少 script_path，无法恢复执行。")
            shots = self._load_shots(Path(task_run.script_path))
            catalog, asset_catalog_status = self._load_catalog()
            task_run.workflow_mode = "real_multi_shot"
            task_run.current_stage = "prepare_assets"
            session.add(task_run)
            session.commit()

            shot_results: list[dict[str, object]] = []
            previous_transition_path: Path | None = None
            previous_frame_summary = ""

            for shot_index, shot in enumerate(shots, start=1):
                storyboard = self._get_or_create_storyboard_record(
                    session=session,
                    task_run=task_run,
                    shot_index=shot_index,
                    shot=shot,
                )
                if shot_index < start_shot_index and storyboard.status == "completed":
                    shot_results.append(
                        {
                            "shot_id": storyboard.storyboard_key,
                            "status": storyboard.status,
                            "video_path": self._lookup_video_path(session, storyboard),
                            "transition_frame_path": storyboard.transition_frame_path,
                        }
                    )
                    if storyboard.transition_frame_path:
                        previous_transition_path = Path(storyboard.transition_frame_path)
                        previous_frame_summary = storyboard.transition_frame_summary or storyboard.previous_frame_summary
                    continue

                if shot_index >= start_shot_index and resumed:
                    storyboard.retry_count += 1
                storyboard.status = "planning"
                storyboard.current_stage = "planning"
                storyboard.error_message = None
                storyboard.started_at = storyboard.started_at or _utc_now()
                if previous_transition_path and previous_frame_summary:
                    storyboard.previous_frame_summary = previous_frame_summary
                session.add(storyboard)
                session.commit()

                video_record = self._get_or_create_video_record(session, storyboard)
                if shot_index >= start_shot_index and resumed:
                    video_record.retry_count += 1
                video_record.status = "generating"
                video_record.current_stage = "generating"
                video_record.error_message = None
                video_record.started_at = video_record.started_at or _utc_now()
                session.add(video_record)
                session.commit()

                try:
                    shot_result, previous_transition_path, previous_frame_summary = self._run_single_shot(
                        session=session,
                        task_run=task_run,
                        storyboard=storyboard,
                        video_record=video_record,
                        shot=shot,
                        shot_index=shot_index,
                        shots=shots,
                        catalog_assets=catalog.assets,
                        previous_transition_path=previous_transition_path,
                        previous_frame_summary=previous_frame_summary,
                    )
                    shot_results.append(shot_result)
                except Exception as exc:
                    failure_status = f"failed:shot_{shot_index}"
                    storyboard.status = failure_status
                    storyboard.current_stage = storyboard.current_stage or "failed"
                    storyboard.error_message = str(exc)
                    storyboard.finished_at = _utc_now()
                    video_record.status = failure_status
                    video_record.current_stage = video_record.current_stage or "failed"
                    video_record.error_message = str(exc)
                    video_record.finished_at = _utc_now()
                    task_run.status = failure_status
                    task_run.current_stage = storyboard.current_stage
                    task_run.error_message = str(exc)
                    task_run.finished_at = _utc_now()
                    session.add(storyboard)
                    session.add(video_record)
                    session.add(task_run)
                    session.commit()
                    raise

            task_run.status = "success"
            task_run.current_stage = "completed"
            task_run.finished_at = _utc_now()
            task_run.error_message = None
            session.add(task_run)
            session.commit()

            return {
                "status": "success",
                "workflow_mode": "real_multi_shot",
                "task_run_id": task_run.id,
                "shot_count": len(shots),
                "resumed": resumed,
                "resumed_from_shot_index": start_shot_index if resumed else None,
                "steps": {
                    "feishu_sync": {"status": "skipped", "reason": "恢复版优先复用本地 catalog.json"},
                    "asset_catalog": {"status": asset_catalog_status, "catalog_assets": catalog.total_assets},
                    "shots": shot_results,
                },
            }

    def _execute_manju_scene_task(
        self,
        *,
        task_run_id: int,
        task_payload: dict[str, Any],
        resumed: bool,
    ) -> dict[str, Any]:
        with Session(self.engine) as session:
            task_run = session.get(TaskRun, task_run_id)
            if task_run is None:
                raise ValueError(f"任务不存在: {task_run_id}")

            storyboard_id = str(task_payload.get("storyboard_id") or "manju_scene_shot")
            storyboard_text = str(task_payload.get("storyboard_text") or "").strip()
            storyboard = self._get_or_create_storyboard_record(
                session=session,
                task_run=task_run,
                shot_index=1,
                shot={
                    "storyboard_id": storyboard_id,
                    "storyboard_text": storyboard_text,
                },
            )
            video_record = self._get_or_create_video_record(session, storyboard)

            if resumed:
                storyboard.retry_count += 1
                video_record.retry_count += 1

            task_run.workflow_mode = "manju_scene_shot"
            task_run.current_stage = "running:scene_shot"
            task_run.error_message = None
            storyboard.status = "generating"
            storyboard.current_stage = "generating"
            storyboard.started_at = storyboard.started_at or _utc_now()
            storyboard.error_message = None
            video_record.status = "generating"
            video_record.current_stage = "generating"
            video_record.started_at = video_record.started_at or _utc_now()
            video_record.error_message = None
            session.add(task_run)
            session.add(storyboard)
            session.add(video_record)
            session.commit()

            try:
                run_result = self.scene_shot_runner(
                    project_root=self.project_root,
                    storyboard_id=storyboard_id,
                    character_ref=str(task_payload["character_ref"]),
                    scene_ref=str(task_payload["scene_ref"]),
                    pet_refs=list(task_payload.get("pet_refs") or []),
                    storyboard_text=storyboard_text,
                    continuity_ref_image_path=str(task_payload.get("continuity_ref_image_path") or ""),
                    continuity_note=str(task_payload.get("continuity_note") or ""),
                    shot_size=str(task_payload.get("shot_size") or ""),
                    camera_angle=str(task_payload.get("camera_angle") or ""),
                    camera_focus=str(task_payload.get("camera_focus") or ""),
                    cut_reason=str(task_payload.get("cut_reason") or ""),
                    anchor_prompt=str(task_payload.get("anchor_prompt") or ""),
                    video_prompt=str(task_payload.get("video_prompt") or ""),
                    input_anchor_image_path=str(task_payload.get("input_anchor_image_path") or task_payload.get("anchor_image_path") or ""),
                    force_regenerate_anchor=bool(task_payload.get("force_regenerate_anchor", False)),
                    aspect_ratio=str(task_payload.get("aspect_ratio") or "16:9"),
                    model_name=str(task_payload.get("model_name") or "nano-banana-2"),
                    duration_seconds=int(task_payload.get("duration_seconds") or 0),
                    manju_mode=str(task_payload.get("manju_mode") or "普通模式"),
                    manju_resolution=str(task_payload.get("manju_resolution") or "1080p"),
                    manju_model_name=str(task_payload.get("manju_model_name") or "Seedance1.5-pro"),
                    manju_profile_dir=str(task_payload.get("manju_profile_dir") or ""),
                    manju_project_url=str(task_payload.get("manju_project_url") or ""),
                    manju_headless=bool(task_payload.get("manju_headless", True)),
                    anchor_output_path=str(task_payload.get("anchor_output_path") or ""),
                    video_output_path=str(task_payload.get("video_output_path") or ""),
                )
            except Exception as exc:
                failure_status = "failed:scene_shot"
                storyboard.status = failure_status
                storyboard.current_stage = "failed"
                storyboard.error_message = str(exc)
                storyboard.finished_at = _utc_now()
                video_record.status = failure_status
                video_record.current_stage = "failed"
                video_record.error_message = str(exc)
                video_record.finished_at = _utc_now()
                task_run.status = failure_status
                task_run.current_stage = "failed"
                task_run.error_message = str(exc)
                task_run.finished_at = _utc_now()
                session.add(task_run)
                session.add(storyboard)
                session.add(video_record)
                session.commit()
                raise

            output_path = str(run_result.get("output_path") or "")
            reference_asset_ids = ["@SceneAnchorImage"]
            if str(task_payload.get("continuity_ref_image_path") or "").strip():
                reference_asset_ids = ["@ContinuityReference", "@SceneAnchorImage"]
            self._replace_prompt_cache_record(
                session=session,
                cache_key=storyboard_id,
                prompt_text=str(run_result.get("video_prompt") or ""),
                reference_asset_ids=json.dumps(reference_asset_ids, ensure_ascii=False),
            )
            storyboard.status = "completed"
            storyboard.current_stage = "completed"
            storyboard.finished_at = _utc_now()
            storyboard.error_message = None
            video_record.provider_job_id = storyboard_id
            video_record.video_path = output_path
            video_record.status = "completed"
            video_record.current_stage = "completed"
            video_record.finished_at = _utc_now()
            video_record.error_message = None
            task_run.status = "success"
            task_run.current_stage = "completed"
            task_run.error_message = None
            task_run.finished_at = _utc_now()
            session.add(task_run)
            session.add(storyboard)
            session.add(video_record)
            session.commit()

            return {
                "status": "success",
                "workflow_mode": "manju_scene_shot",
                "task_run_id": task_run.id,
                "shot_count": 1,
                "resumed": resumed,
                "resumed_from_shot_index": 1 if resumed else None,
                "steps": {
                    "scene_anchor": {
                        "status": "completed",
                        "output_path": str(run_result.get("anchor_image_path") or ""),
                    },
                    "manju_scene_shot": {
                        "status": "completed",
                        "shot_id": storyboard_id,
                        "video_path": output_path,
                        "audit_report_path": str(run_result.get("audit_report_path") or ""),
                    },
                },
            }

    def _execute_manju_scene_batch_task(
        self,
        *,
        task_run_id: int,
        task_payload: dict[str, Any],
        start_shot_index: int,
        resumed: bool,
    ) -> dict[str, Any]:
        with Session(self.engine) as session:
            task_run = session.get(TaskRun, task_run_id)
            if task_run is None:
                raise ValueError(f"浠诲姟涓嶅瓨鍦? {task_run_id}")

            shots = self._load_manju_scene_batch_shots(task_payload)
            task_run.workflow_mode = "manju_scene_batch"
            task_run.current_stage = "prepare:scene_batch"
            task_run.error_message = None
            session.add(task_run)
            session.commit()

            shot_results: list[dict[str, object]] = []
            previous_transition_path: Path | None = None
            previous_transition_summary = ""
            previous_character_asset_id = ""
            previous_scene_asset_id = ""
            previous_pet_refs_key = ""
            for shot_index, shot in enumerate(shots, start=1):
                storyboard = self._get_or_create_storyboard_record(
                    session=session,
                    task_run=task_run,
                    shot_index=shot_index,
                    shot=shot,
                )
                video_record = self._get_or_create_video_record(session, storyboard)

                if shot_index < start_shot_index and storyboard.status == "completed":
                    prepared_completed_shot = self._prepare_manju_scene_shot(shot)
                    shot_results.append(
                        {
                            "shot_id": storyboard.storyboard_key,
                            "status": storyboard.status,
                            "video_path": self._lookup_video_path(session, storyboard),
                            "transition_frame_path": storyboard.transition_frame_path or "",
                        }
                    )
                    previous_character_asset_id = prepared_completed_shot["character_asset_id"]
                    previous_scene_asset_id = prepared_completed_shot["scene_asset_id"]
                    previous_pet_refs_key = prepared_completed_shot["pet_refs_key"]
                    if storyboard.transition_frame_path:
                        previous_transition_path = Path(storyboard.transition_frame_path)
                        previous_transition_summary = storyboard.transition_frame_summary or ""
                    continue

                if shot_index >= start_shot_index and resumed:
                    storyboard.retry_count += 1
                    video_record.retry_count += 1

                task_run.current_stage = f"running:scene_batch:{shot_index}"
                storyboard.status = "generating"
                storyboard.current_stage = "generating"
                storyboard.started_at = storyboard.started_at or _utc_now()
                storyboard.finished_at = None
                storyboard.error_message = None
                video_record.status = "generating"
                video_record.current_stage = "generating"
                video_record.started_at = video_record.started_at or _utc_now()
                video_record.finished_at = None
                video_record.error_message = None
                session.add(task_run)
                session.add(storyboard)
                session.add(video_record)
                session.commit()

                try:
                    prepared_shot = self._prepare_manju_scene_shot(shot)
                    explicit_anchor_image_path = str(shot.get("input_anchor_image_path") or "").strip()
                    force_regenerate_anchor = bool(prepared_shot["runner_kwargs"].get("force_regenerate_anchor"))
                    anchor_strategy = str(prepared_shot.get("anchor_strategy") or "auto").strip() or "auto"
                    has_same_references = (
                        previous_transition_path is not None
                        and prepared_shot["character_asset_id"] == previous_character_asset_id
                        and prepared_shot["scene_asset_id"] == previous_scene_asset_id
                        and prepared_shot["pet_refs_key"] == previous_pet_refs_key
                    )
                    if explicit_anchor_image_path and not force_regenerate_anchor:
                        prepared_shot["runner_kwargs"]["input_anchor_image_path"] = explicit_anchor_image_path
                        prepared_shot["runner_kwargs"]["continuity_ref_image_path"] = ""
                        anchor_source = "provided"
                    elif has_same_references and not force_regenerate_anchor and anchor_strategy == "reuse_previous_transition":
                        prepared_shot["runner_kwargs"]["input_anchor_image_path"] = str(previous_transition_path)
                        prepared_shot["runner_kwargs"]["continuity_ref_image_path"] = ""
                        anchor_source = "transition"
                    elif has_same_references and previous_transition_path is not None:
                        prepared_shot["runner_kwargs"]["input_anchor_image_path"] = ""
                        prepared_shot["runner_kwargs"]["continuity_ref_image_path"] = str(previous_transition_path)
                        anchor_source = "continuity_ref"
                    else:
                        prepared_shot["runner_kwargs"]["input_anchor_image_path"] = ""
                        prepared_shot["runner_kwargs"]["continuity_ref_image_path"] = str(
                            prepared_shot["runner_kwargs"].get("continuity_ref_image_path") or ""
                        )
                        anchor_source = "generated"

                    if anchor_source in {"transition", "continuity_ref"} and previous_transition_summary:
                        storyboard.previous_frame_summary = previous_transition_summary
                        prepared_shot["runner_kwargs"]["continuity_note"] = previous_transition_summary
                    elif explicit_anchor_image_path:
                        storyboard.previous_frame_summary = "使用外部提供的首帧图作为当前镜头输入。"
                    else:
                        storyboard.previous_frame_summary = ""
                    session.add(storyboard)
                    session.commit()

                    run_result = self.scene_shot_runner(**prepared_shot["runner_kwargs"])
                except Exception as exc:
                    failure_status = f"failed:shot_{shot_index}"
                    storyboard.status = failure_status
                    storyboard.current_stage = "failed"
                    storyboard.error_message = str(exc)
                    storyboard.finished_at = _utc_now()
                    video_record.status = failure_status
                    video_record.current_stage = "failed"
                    video_record.error_message = str(exc)
                    video_record.finished_at = _utc_now()
                    task_run.status = failure_status
                    task_run.current_stage = "failed"
                    task_run.error_message = str(exc)
                    task_run.finished_at = _utc_now()
                    session.add(task_run)
                    session.add(storyboard)
                    session.add(video_record)
                    session.commit()
                    raise

                output_path = str(run_result.get("output_path") or "")
                anchor_image_path = str(run_result.get("anchor_image_path") or prepared_shot["runner_kwargs"].get("input_anchor_image_path") or "")
                next_transition_path: Path | None = None
                next_transition_summary = ""

                self._replace_prompt_cache_record(
                    session=session,
                    cache_key=storyboard.storyboard_key,
                    prompt_text=str(run_result.get("video_prompt") or ""),
                    reference_asset_ids=json.dumps(
                        ["@ProvidedAnchorImage"] if anchor_source == "provided"
                        else ["@TransitionFrame"] if anchor_source == "transition"
                        else ["@ContinuityReference", "@SceneAnchorImage"] if anchor_source == "continuity_ref"
                        else ["@SceneAnchorImage"],
                        ensure_ascii=False,
                    ),
                )

                if shot_index < len(shots):
                    next_shot = shots[shot_index]
                    transition_result = self.video_analyzer.analyze_one_shot(
                        output_path,
                        current_shot_summary=str(shot.get("storyboard_text") or ""),
                        next_shot_summary=str(next_shot.get("storyboard_text") or ""),
                    )
                    if transition_result.best_frame is None:
                        raise RuntimeError(f"未能为 {storyboard.storyboard_key} 选出可用承接帧")
                    next_transition_path = (
                        self.project_root
                        / "outputs"
                        / "frames"
                        / storyboard.storyboard_key
                        / f"{storyboard.storyboard_key}_transition.png"
                    )
                    next_transition_path.parent.mkdir(parents=True, exist_ok=True)
                    self.transition_frame_extractor(
                        Path(output_path),
                        transition_result.best_frame.timestamp_seconds,
                        next_transition_path,
                    )
                    next_transition_summary = (
                        f"上一镜头最佳承接帧位于 {transition_result.best_frame.timestamp_seconds:.2f}s，"
                        f"原因：{transition_result.best_frame.reason}"
                    )

                storyboard.transition_frame_path = str(next_transition_path) if next_transition_path else None
                storyboard.transition_frame_summary = next_transition_summary
                storyboard.status = "completed"
                storyboard.current_stage = "completed"
                storyboard.finished_at = _utc_now()
                storyboard.error_message = None
                video_record.provider_job_id = storyboard.storyboard_key
                video_record.video_path = output_path
                video_record.status = "completed"
                video_record.current_stage = "completed"
                video_record.finished_at = _utc_now()
                video_record.error_message = None
                session.add(storyboard)
                session.add(video_record)
                session.commit()

                shot_results.append(
                    {
                        "shot_id": storyboard.storyboard_key,
                        "status": "completed",
                        "video_path": output_path,
                        "anchor_image_path": anchor_image_path,
                        "anchor_source": anchor_source,
                        "continuity_ref_image_path": str(
                            prepared_shot["runner_kwargs"].get("continuity_ref_image_path") or ""
                        ),
                        "audit_report_path": str(run_result.get("audit_report_path") or ""),
                        "transition_frame_path": str(next_transition_path) if next_transition_path else "",
                        "character_ref": prepared_shot["character_asset_id"],
                        "scene_ref": prepared_shot["scene_asset_id"],
                        "character_reference_image": prepared_shot["character_image_path"],
                        "scene_reference_image": prepared_shot["scene_image_path"],
                    }
                )
                previous_transition_path = next_transition_path
                previous_transition_summary = next_transition_summary
                previous_character_asset_id = prepared_shot["character_asset_id"]
                previous_scene_asset_id = prepared_shot["scene_asset_id"]
                previous_pet_refs_key = prepared_shot["pet_refs_key"]

            task_run.status = "success"
            task_run.current_stage = "completed"
            task_run.finished_at = _utc_now()
            task_run.error_message = None
            session.add(task_run)
            session.commit()

            return {
                "status": "success",
                "workflow_mode": "manju_scene_batch",
                "task_run_id": task_run.id,
                "shot_count": len(shots),
                "resumed": resumed,
                "resumed_from_shot_index": start_shot_index if resumed else None,
                "steps": {
                    "scene_batch": {
                        "status": "completed",
                        "shots": shot_results,
                    }
                },
            }

    def _run_single_shot(
        self,
        *,
        session: Session,
        task_run: TaskRun,
        storyboard: StoryboardRecord,
        video_record: VideoGenerationRecord,
        shot: dict[str, Any],
        shot_index: int,
        shots: list[dict[str, Any]],
        catalog_assets: list[object],
        previous_transition_path: Path | None,
        previous_frame_summary: str,
    ) -> tuple[dict[str, object], Path | None, str]:
        task_run.current_stage = f"running:shot_{shot_index}.planning"
        session.add(task_run)
        session.commit()

        planner_request = self._build_asset_planner_request(shot=shot, catalog_assets=catalog_assets)
        planner_response = self.openclaw.run_asset_planner(planner_request)
        selected_assets = list(planner_response.selected_assets)
        storyboard.status = "assets_selected"
        storyboard.current_stage = "assets_selected"
        session.add(storyboard)
        session.commit()

        task_run.current_stage = f"running:shot_{shot_index}.prompt"
        session.add(task_run)
        session.commit()

        composer_response = self.openclaw.run_prompt_composer(
            PromptComposerRequest(
                storyboard_id=shot["storyboard_id"],
                shot_id=shot["storyboard_id"],
                storyboard_text=shot.get("storyboard_text", ""),
                style_summary=shot.get("style_summary", ""),
                selected_assets=selected_assets,
                previous_frame_summary=previous_frame_summary if previous_transition_path else "",
                continuity_requirements=shot.get("continuity_requirements", ""),
            )
        )
        storyboard.status = "prompt_ready"
        storyboard.current_stage = "prompt_ready"
        session.add(storyboard)
        self._replace_prompt_cache_record(
            session=session,
            cache_key=shot["storyboard_id"],
            prompt_text=composer_response.prompt_main,
            reference_asset_ids=self._serialize_reference_ids(
                composer_response.ref_assets_in_order,
                include_transition=previous_transition_path is not None,
            ),
        )
        session.commit()

        task_run.current_stage = f"running:shot_{shot_index}.generate"
        storyboard.status = "generating"
        storyboard.current_stage = "generating"
        video_record.status = "generating"
        video_record.current_stage = "generating"
        session.add(task_run)
        session.add(storyboard)
        session.add(video_record)
        session.commit()

        reference_file_paths = self._resolve_reference_file_paths(
            selected_assets=selected_assets,
            previous_transition_path=previous_transition_path,
        )
        output_path = self.project_root / "outputs" / "videos" / f"{shot['storyboard_id']}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        operator = self.jimeng_operator_factory()
        try:
            one_shot_result = operator.run_one_shot(
                JimengOneShotRequest(
                    shot_id=shot["storyboard_id"],
                    prompt_main=composer_response.prompt_main,
                    prompt_negative=composer_response.prompt_negative,
                    ref_assets_in_order=self._request_reference_ids(
                        composer_response.ref_assets_in_order,
                        include_transition=previous_transition_path is not None,
                    ),
                    reference_file_paths=reference_file_paths,
                    storyboard_text=shot.get("storyboard_text", ""),
                    output_path=output_path,
                )
            )
            if hasattr(operator, "download_latest_video"):
                operator.download_latest_video(output_path)
        finally:
            if hasattr(operator, "close"):
                operator.close()

        video_record.provider_job_id = getattr(one_shot_result, "shot_id", shot["storyboard_id"])
        video_record.video_path = str(output_path)
        video_record.status = "generated"
        video_record.current_stage = "generated"
        session.add(video_record)
        session.commit()

        task_run.current_stage = f"running:shot_{shot_index}.audit"
        storyboard.status = "auditing"
        storyboard.current_stage = "auditing"
        session.add(task_run)
        session.add(storyboard)
        session.commit()

        report_path = self.project_root / "outputs" / "reviews" / shot["storyboard_id"] / f"{shot['storyboard_id']}_audit.html"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        audit_decision = self.video_audit_runner(
            shot_id=shot["storyboard_id"],
            storyboard_text=shot.get("storyboard_text", ""),
            prompt_main=composer_response.prompt_main,
            prompt_negative=composer_response.prompt_negative,
            ref_assets_in_order=self._request_reference_ids(
                composer_response.ref_assets_in_order,
                include_transition=previous_transition_path is not None,
            ),
            video_path=output_path,
            report_path=report_path,
        )
        if audit_decision.action != "approve":
            raise RuntimeError(f"自动审查未通过：{audit_decision.action}")

        storyboard.status = "audit_approved"
        storyboard.current_stage = "audit_approved"
        video_record.status = "completed"
        video_record.current_stage = "completed"
        video_record.finished_at = _utc_now()
        session.add(storyboard)
        session.add(video_record)
        session.commit()

        next_transition_path: Path | None = None
        next_frame_summary = ""
        if shot_index < len(shots):
            next_shot = shots[shot_index]
            task_run.current_stage = f"running:shot_{shot_index}.transition"
            storyboard.status = "transition_picking"
            storyboard.current_stage = "transition_picking"
            session.add(task_run)
            session.add(storyboard)
            session.commit()

            transition_result = self.video_analyzer.analyze_one_shot(
                str(output_path),
                next_shot_summary=next_shot.get("storyboard_text", ""),
                current_shot_summary=shot.get("current_shot_summary", ""),
            )
            next_transition_path = self.project_root / "outputs" / "frames" / shot["storyboard_id"] / f"{shot['storyboard_id']}_transition.png"
            next_transition_path.parent.mkdir(parents=True, exist_ok=True)
            self.transition_frame_extractor(
                Path(output_path),
                transition_result.best_frame.timestamp_seconds,
                next_transition_path,
            )
            next_frame_summary = (
                f"上一镜头最佳承接帧位于 {transition_result.best_frame.timestamp_seconds:.2f}s，"
                f"原因：{transition_result.best_frame.reason}"
            )
            storyboard.transition_frame_path = str(next_transition_path)
            storyboard.transition_frame_summary = next_frame_summary
            storyboard.status = "transition_ready"
            storyboard.current_stage = "transition_ready"
            session.add(storyboard)
            session.commit()

        storyboard.status = "completed"
        storyboard.current_stage = "completed"
        storyboard.finished_at = _utc_now()
        session.add(storyboard)
        session.commit()

        return (
            {
                "shot_id": shot["storyboard_id"],
                "status": "completed",
                "video_path": str(output_path),
                "transition_frame_path": str(next_transition_path) if next_transition_path else "",
            },
            next_transition_path,
            next_frame_summary,
        )

    def _run_placeholder(self, script_path: str | None) -> dict[str, Any]:
        logger.info("启动占位版工作流，script_path=%s", script_path)
        return {
            "status": "placeholder",
            "script_path": script_path,
            "steps": {
                "feishu_sync": {"status": "placeholder"},
                "asset_catalog": {"status": "placeholder"},
                "openclaw": {"status": "placeholder"},
                "jimeng": {"status": "placeholder"},
                "video_analyzer": {"status": "placeholder"},
            },
        }

    def _build_asset_planner_request(self, *, shot: dict[str, Any], catalog_assets: list[object]) -> object:
        summaries = [
            CatalogAssetSummary(
                asset_id=asset.asset_id,
                type=asset.type,
                display_name=asset.display_name,
                jimeng_ref_name=asset.jimeng_ref_name,
                tags=list(asset.tags),
            )
            for asset in catalog_assets
        ]
        if hasattr(self.openclaw, "build_asset_planner_request_from_catalog"):
            return self.openclaw.build_asset_planner_request_from_catalog(
                storyboard_id=shot["storyboard_id"],
                storyboard_text=shot.get("storyboard_text", ""),
                style_summary=shot.get("style_summary", ""),
                catalog_assets=summaries,
            )
        from app.openclaw import AssetPlannerRequest

        return AssetPlannerRequest(
            storyboard_id=shot["storyboard_id"],
            storyboard_text=shot.get("storyboard_text", ""),
            style_summary=shot.get("style_summary", ""),
            catalog_assets=summaries,
        )

    def _load_catalog(self) -> tuple[object, str]:
        candidates = [
            self.project_root / "assets" / "catalog.json",
            self.project_root / "data" / "assets" / "catalog.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return load_asset_catalog(candidate), "loaded"
        assets_dir_candidates = [
            self.project_root / "assets",
            self.project_root / "data" / "assets",
        ]
        for assets_dir in assets_dir_candidates:
            if assets_dir.exists():
                build_result = self.asset_catalog.build_catalog(assets_dir)
                return build_result.catalog, "rebuilt"
        raise FileNotFoundError("未找到可用 catalog.json 或 assets 目录。")

    def _load_task_payload(self, script_path: Path) -> dict[str, Any]:
        payload = json.loads(script_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("输入任务文件必须是 JSON 对象。")
        return payload

    def _detect_workflow_mode(self, payload: dict[str, Any]) -> str:
        explicit_mode = str(payload.get("workflow_mode") or "").strip()
        if explicit_mode:
            return explicit_mode
        shots = payload.get("shots") or payload.get("storyboards") or []
        if shots:
            if payload.get("character_ref") and payload.get("scene_ref"):
                return "manju_scene_batch"
            first_shot = shots[0] if isinstance(shots[0], dict) else {}
            if first_shot.get("character_ref") and first_shot.get("scene_ref"):
                return "manju_scene_batch"
        if payload.get("character_ref") and payload.get("scene_ref") and payload.get("storyboard_text"):
            return "manju_scene_shot"
        return "real_multi_shot"

    def _load_shots(self, script_path: Path) -> list[dict[str, Any]]:
        payload = json.loads(script_path.read_text(encoding="utf-8"))
        shots = payload.get("shots") or payload.get("storyboards") or []
        if not shots:
            raise ValueError("输入脚本中没有 shots。")
        normalized: list[dict[str, Any]] = []
        for index, shot in enumerate(shots, start=1):
            shot_id = shot.get("storyboard_id") or shot.get("id") or f"shot_{index:03d}"
            normalized.append(
                {
                    "storyboard_id": shot_id,
                    "storyboard_text": shot.get("storyboard_text") or shot.get("summary") or "",
                    "style_summary": shot.get("style_summary", ""),
                    "current_shot_summary": shot.get("current_shot_summary", ""),
                    "continuity_requirements": shot.get("continuity_requirements", ""),
                }
            )
        return normalized

    def _load_manju_scene_batch_shots(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        shots = payload.get("shots") or payload.get("storyboards") or []
        if not shots:
            raise ValueError("manju_scene_batch payload is missing shots")

        defaults = {
            "character_ref": payload.get("character_ref"),
            "scene_ref": payload.get("scene_ref"),
            "pet_refs": list(payload.get("pet_refs") or []),
            "continuity_ref_image_path": payload.get("continuity_ref_image_path", ""),
            "continuity_note": payload.get("continuity_note", ""),
            "shot_size": payload.get("shot_size", ""),
            "camera_angle": payload.get("camera_angle", ""),
            "camera_focus": payload.get("camera_focus", ""),
            "cut_reason": payload.get("cut_reason", ""),
            "anchor_strategy": payload.get("anchor_strategy", "auto"),
            "anchor_prompt": payload.get("anchor_prompt", ""),
            "video_prompt": payload.get("video_prompt", ""),
            "input_anchor_image_path": payload.get("input_anchor_image_path") or payload.get("anchor_image_path", ""),
            "force_regenerate_anchor": bool(payload.get("force_regenerate_anchor", False)),
            "aspect_ratio": payload.get("aspect_ratio", "16:9"),
            "model_name": payload.get("model_name", "nano-banana-2"),
            "duration_seconds": int(payload.get("duration_seconds") or 0),
            "manju_mode": payload.get("manju_mode", "普通模式"),
            "manju_resolution": payload.get("manju_resolution", "1080p"),
            "manju_model_name": payload.get("manju_model_name", "Seedance1.5-pro"),
            "manju_profile_dir": payload.get("manju_profile_dir", ""),
            "manju_project_url": payload.get("manju_project_url", ""),
            "manju_headless": bool(payload.get("manju_headless", True)),
        }

        normalized: list[dict[str, Any]] = []
        for index, shot in enumerate(shots, start=1):
            shot_id = shot.get("storyboard_id") or shot.get("id") or f"manju_scene_{index:03d}"
            storyboard_text = str(shot.get("storyboard_text") or shot.get("summary") or "").strip()
            character_ref = shot.get("character_ref") or defaults["character_ref"]
            scene_ref = shot.get("scene_ref") or defaults["scene_ref"]
            if "pet_refs" in shot:
                pet_refs = [str(item).strip() for item in list(shot.get("pet_refs") or []) if str(item).strip()]
            else:
                pet_refs = [str(item).strip() for item in list(defaults["pet_refs"] or []) if str(item).strip()]
            if "input_anchor_image_path" in shot:
                shot_input_anchor_image_path = str(shot.get("input_anchor_image_path") or "")
            elif "anchor_image_path" in shot:
                shot_input_anchor_image_path = str(shot.get("anchor_image_path") or "")
            else:
                shot_input_anchor_image_path = str(defaults["input_anchor_image_path"] or "")
            if not character_ref or not scene_ref:
                raise ValueError(f"manju_scene_batch shot {index} is missing character_ref or scene_ref")
            if not storyboard_text:
                raise ValueError(f"manju_scene_batch shot {index} is missing storyboard_text")

            normalized.append(
                {
                    "storyboard_id": shot_id,
                    "storyboard_text": storyboard_text,
                    "character_ref": str(character_ref),
                    "scene_ref": str(scene_ref),
                    "pet_refs": pet_refs,
                    "continuity_ref_image_path": str(
                        shot.get("continuity_ref_image_path", defaults["continuity_ref_image_path"]) or ""
                    ),
                    "continuity_note": str(shot.get("continuity_note", defaults["continuity_note"]) or ""),
                    "shot_size": str(shot.get("shot_size", defaults["shot_size"]) or ""),
                    "camera_angle": str(shot.get("camera_angle", defaults["camera_angle"]) or ""),
                    "camera_focus": str(shot.get("camera_focus", defaults["camera_focus"]) or ""),
                    "cut_reason": str(shot.get("cut_reason", defaults["cut_reason"]) or ""),
                    "anchor_strategy": str(shot.get("anchor_strategy", defaults["anchor_strategy"]) or "auto"),
                    "anchor_prompt": str(shot.get("anchor_prompt", defaults["anchor_prompt"]) or ""),
                    "video_prompt": str(shot.get("video_prompt", defaults["video_prompt"]) or ""),
                    "input_anchor_image_path": shot_input_anchor_image_path,
                    "force_regenerate_anchor": bool(
                        shot.get("force_regenerate_anchor", defaults["force_regenerate_anchor"])
                    ),
                    "aspect_ratio": str(shot.get("aspect_ratio", defaults["aspect_ratio"]) or "16:9"),
                    "model_name": str(shot.get("model_name", defaults["model_name"]) or "nano-banana-2"),
                    "duration_seconds": int(shot.get("duration_seconds") or defaults["duration_seconds"] or 0),
                    "manju_mode": str(shot.get("manju_mode", defaults["manju_mode"]) or "普通模式"),
                    "manju_resolution": str(shot.get("manju_resolution", defaults["manju_resolution"]) or "1080p"),
                    "manju_model_name": str(
                        shot.get("manju_model_name", defaults["manju_model_name"]) or "Seedance1.5-pro"
                    ),
                    "manju_profile_dir": str(shot.get("manju_profile_dir", defaults["manju_profile_dir"]) or ""),
                    "manju_project_url": str(shot.get("manju_project_url", defaults["manju_project_url"]) or ""),
                    "manju_headless": bool(shot.get("manju_headless", defaults["manju_headless"])),
                    "anchor_output_path": str(
                        shot.get("anchor_output_path") or (self.project_root / "outputs" / "images" / f"{shot_id}.png")
                    ),
                    "video_output_path": str(
                        shot.get("video_output_path") or (self.project_root / "outputs" / "videos" / f"{shot_id}.mp4")
                    ),
                }
            )
        return normalized

    def _prepare_manju_scene_shot(self, shot: dict[str, Any]) -> dict[str, Any]:
        catalog_path = self._resolve_catalog_path()
        character_reference = resolve_catalog_asset_reference(catalog_path, shot["character_ref"], "character")
        scene_reference = resolve_catalog_asset_reference(catalog_path, shot["scene_ref"], "scene")
        input_anchor_image_path = str(shot.get("input_anchor_image_path") or "").strip()
        pet_refs = [str(item).strip() for item in list(shot.get("pet_refs") or []) if str(item).strip()]

        return {
            "runner_kwargs": {
                "project_root": self.project_root,
                "storyboard_id": str(shot["storyboard_id"]),
                "character_ref": character_reference.asset.asset_id,
                "scene_ref": scene_reference.asset.asset_id,
                "pet_refs": pet_refs,
                "continuity_ref_image_path": str(shot.get("continuity_ref_image_path") or ""),
                "continuity_note": str(shot.get("continuity_note") or ""),
                "shot_size": str(shot.get("shot_size") or ""),
                "camera_angle": str(shot.get("camera_angle") or ""),
                "camera_focus": str(shot.get("camera_focus") or ""),
                "cut_reason": str(shot.get("cut_reason") or ""),
                "storyboard_text": str(shot.get("storyboard_text") or ""),
                "anchor_prompt": str(shot.get("anchor_prompt") or ""),
                "video_prompt": str(shot.get("video_prompt") or ""),
                "input_anchor_image_path": input_anchor_image_path,
                "force_regenerate_anchor": bool(shot.get("force_regenerate_anchor", False)),
                "aspect_ratio": str(shot.get("aspect_ratio") or "16:9"),
                "model_name": str(shot.get("model_name") or "nano-banana-2"),
                "duration_seconds": int(shot.get("duration_seconds") or 0),
                "manju_mode": str(shot.get("manju_mode") or "普通模式"),
                "manju_resolution": str(shot.get("manju_resolution") or "1080p"),
                "manju_model_name": str(shot.get("manju_model_name") or "Seedance1.5-pro"),
                "manju_profile_dir": str(shot.get("manju_profile_dir") or ""),
                "manju_project_url": str(shot.get("manju_project_url") or ""),
                "manju_headless": bool(shot.get("manju_headless", True)),
                "anchor_output_path": str(shot.get("anchor_output_path") or ""),
                "video_output_path": str(shot.get("video_output_path") or ""),
            },
            "anchor_strategy": str(shot.get("anchor_strategy") or "auto"),
            "character_asset_id": character_reference.asset.asset_id,
            "scene_asset_id": scene_reference.asset.asset_id,
            "pet_refs_key": "|".join(sorted(pet_refs)),
            "character_image_path": str(character_reference.selected_file),
            "scene_image_path": str(scene_reference.selected_file),
        }

    def _get_or_create_storyboard_record(
        self,
        *,
        session: Session,
        task_run: TaskRun,
        shot_index: int,
        shot: dict[str, Any],
    ) -> StoryboardRecord:
        statement = select(StoryboardRecord).where(
            StoryboardRecord.task_run_id == task_run.id,
            StoryboardRecord.shot_index == shot_index,
        )
        record = session.exec(statement).first()
        if record is None:
            record = StoryboardRecord(
                task_run_id=task_run.id,
                storyboard_key=shot["storyboard_id"],
                shot_index=shot_index,
                summary=shot.get("storyboard_text", ""),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
        else:
            record.summary = shot.get("storyboard_text", "")
            record.error_message = None
            record.finished_at = None
        return record

    def _get_or_create_video_record(self, session: Session, storyboard: StoryboardRecord) -> VideoGenerationRecord:
        statement = select(VideoGenerationRecord).where(VideoGenerationRecord.storyboard_id == storyboard.id)
        record = session.exec(statement).first()
        if record is None:
            record = VideoGenerationRecord(storyboard_id=storyboard.id)
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def _lookup_video_path(self, session: Session, storyboard: StoryboardRecord) -> str:
        statement = select(VideoGenerationRecord).where(VideoGenerationRecord.storyboard_id == storyboard.id)
        record = session.exec(statement).first()
        return record.video_path if record and record.video_path else ""

    def _replace_prompt_cache_record(self, *, session: Session, cache_key: str, prompt_text: str, reference_asset_ids: str) -> None:
        existing = session.exec(select(PromptCacheRecord).where(PromptCacheRecord.cache_key == cache_key)).all()
        for record in existing:
            session.delete(record)
        session.add(
            PromptCacheRecord(
                cache_key=cache_key,
                prompt_text=prompt_text,
                reference_asset_ids=reference_asset_ids,
            )
        )

    def _resolve_catalog_path(self) -> Path:
        candidates = [
            self.project_root / "assets" / "catalog.json",
            self.project_root / "data" / "assets" / "catalog.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("catalog.json not found under project assets")

    def _resolve_reference_file_paths(
        self,
        *,
        selected_assets: list[CatalogAssetSummary],
        previous_transition_path: Path | None,
    ) -> list[Path]:
        paths: list[Path] = []
        if previous_transition_path is not None:
            paths.append(previous_transition_path)
        catalog_path = self._resolve_catalog_path()
        for asset in selected_assets:
            try:
                resolved_reference = resolve_catalog_asset_reference(
                    catalog_path,
                    asset.asset_id,
                    asset.type,
                )
            except ValueError:
                continue
            paths.append(resolved_reference.selected_file)
        return paths

    def _serialize_reference_ids(self, refs: list[str], *, include_transition: bool) -> str:
        return json.dumps(self._request_reference_ids(refs, include_transition=include_transition), ensure_ascii=False)

    def _request_reference_ids(self, refs: list[str], *, include_transition: bool) -> list[str]:
        effective = list(refs)
        if include_transition and "@TransitionFrame" not in effective:
            effective.append("@TransitionFrame")
        return effective

    def _resolve_resume_shot_index(self, storyboards: list[StoryboardRecord], *, shot_id: str | None) -> int:
        if shot_id:
            for storyboard in storyboards:
                if storyboard.storyboard_key == shot_id:
                    return storyboard.shot_index
            raise ValueError(f"镜头不存在: {shot_id}")
        for storyboard in storyboards:
            if storyboard.status.startswith("failed:"):
                return storyboard.shot_index
        for storyboard in storyboards:
            if storyboard.status != "completed":
                return storyboard.shot_index
        raise ValueError("任务没有可恢复的失败镜头。")

    def _default_scene_shot_runner(self, **kwargs) -> dict[str, str]:
        command = [
            sys.executable,
            "-m",
            "app.cli",
            "run-manju-scene-shot",
            "--storyboard-id",
            str(kwargs["storyboard_id"]),
            "--character-ref",
            str(kwargs["character_ref"]),
            "--scene-ref",
            str(kwargs["scene_ref"]),
            "--storyboard-text",
            str(kwargs["storyboard_text"]),
            "--continuity-note",
            str(kwargs.get("continuity_note") or ""),
            "--aspect-ratio",
            str(kwargs["aspect_ratio"]),
            "--model",
            str(kwargs["model_name"]),
            "--duration-seconds",
            str(kwargs["duration_seconds"]),
            "--manju-mode",
            str(kwargs["manju_mode"]),
            "--manju-resolution",
            str(kwargs["manju_resolution"]),
            "--manju-model-name",
            str(kwargs["manju_model_name"]),
        ]
        for pet_ref in list(kwargs.get("pet_refs") or []):
            command.extend(["--pet-ref", str(pet_ref)])
        if kwargs.get("continuity_ref_image_path"):
            command.extend(["--continuity-ref-image", str(kwargs["continuity_ref_image_path"])])
        if kwargs.get("shot_size"):
            command.extend(["--shot-size", str(kwargs["shot_size"])])
        if kwargs.get("camera_angle"):
            command.extend(["--camera-angle", str(kwargs["camera_angle"])])
        if kwargs.get("camera_focus"):
            command.extend(["--camera-focus", str(kwargs["camera_focus"])])
        if kwargs.get("cut_reason"):
            command.extend(["--cut-reason", str(kwargs["cut_reason"])])
        if kwargs.get("anchor_prompt"):
            command.extend(["--anchor-prompt", str(kwargs["anchor_prompt"])])
        if kwargs.get("video_prompt"):
            command.extend(["--video-prompt", str(kwargs["video_prompt"])])
        if kwargs.get("input_anchor_image_path"):
            command.extend(["--input-anchor-image", str(kwargs["input_anchor_image_path"])])
        if kwargs.get("force_regenerate_anchor"):
            command.append("--force-regenerate-anchor")
        if kwargs.get("manju_profile_dir"):
            command.extend(["--manju-profile-dir", str(kwargs["manju_profile_dir"])])
        if kwargs.get("manju_project_url"):
            command.extend(["--manju-project-url", str(kwargs["manju_project_url"])])
        if kwargs.get("anchor_output_path"):
            command.extend(["--anchor-output-path", str(kwargs["anchor_output_path"])])
        if kwargs.get("video_output_path"):
            command.extend(["--video-output-path", str(kwargs["video_output_path"])])
        if kwargs.get("manju_headless", True):
            command.append("--manju-headless")
        else:
            command.append("--manju-headed")

        child_env = os.environ.copy()
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env.setdefault("PYTHONUTF8", "1")
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(kwargs["project_root"]),
            env=child_env,
            check=False,
        )
        if process.returncode != 0:
            detail_lines = [line for line in process.stdout.strip().splitlines()[-20:] if line.strip()]
            detail_lines.extend(line for line in process.stderr.strip().splitlines()[-20:] if line.strip())
            detail = "\n".join(detail_lines).strip()
            raise RuntimeError(detail or f"scene shot runner failed with exit code {process.returncode}")

        output_path = str(kwargs.get("video_output_path") or "").strip()
        anchor_image_path = str(kwargs.get("anchor_output_path") or "").strip()
        audit_report_path = ""
        video_prompt = str(kwargs.get("video_prompt") or "")
        for raw_line in process.stdout.splitlines():
            line = raw_line.strip()
            if line.startswith("- output_path:"):
                output_path = line.split(":", 1)[1].strip()
            elif line.startswith("- anchor_image_path:"):
                anchor_image_path = line.split(":", 1)[1].strip()
            elif line.startswith("- audit_report_path:"):
                audit_report_path = line.split(":", 1)[1].strip()
            elif line.startswith("- video_prompt:"):
                video_prompt = line.split(":", 1)[1].strip()

        return {
            "output_path": output_path,
            "anchor_image_path": anchor_image_path,
            "audit_report_path": audit_report_path,
            "video_prompt": video_prompt,
            "stdout": process.stdout,
        }

    def _default_jimeng_operator_factory(self) -> JimengWebOperator:
        return JimengWebOperator(build_default_jimeng_config(self.project_root))

    def _default_audit_runner(self, **kwargs) -> PromptAuditDecision:
        return PromptAuditDecision(
            action="approve",
            review_summary="未配置自动审查，默认通过。",
            report_path=str(kwargs.get("report_path", "")),
        )
