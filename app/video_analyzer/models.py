"""视频分析结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FrameQualityMetrics:
    """基础画面质量层评分。"""

    sharpness: float = 0.0
    exposure: float = 0.0
    subject_visibility: float = 0.0


@dataclass(slots=True)
class FrameContinuityMetrics:
    """承接合理度层评分。"""

    scene_match: float = 0.0
    character_state_match: float = 0.0
    pose_match: float = 0.0
    action_settle: float = 0.0
    start_stability: float = 0.0


@dataclass(slots=True)
class CandidateFrame:
    """候选帧及其评分信息。"""

    frame_index: int
    timestamp_seconds: float
    relative_position: float
    frame_path: str
    scene_tags: list[str] = field(default_factory=list)
    character_state_tags: list[str] = field(default_factory=list)
    pose_tags: list[str] = field(default_factory=list)
    composition_tags: list[str] = field(default_factory=list)
    action_phase: str = "settled"
    blur_level: float = 0.2
    exposure_score: float = 0.8
    subject_visibility: float = 0.8
    continuity_score: float = 0.0
    quality_score: float = 0.0
    total_score: float = 0.0
    reason: str = ""
    quality_metrics: FrameQualityMetrics = field(default_factory=FrameQualityMetrics)
    continuity_metrics: FrameContinuityMetrics = field(default_factory=FrameContinuityMetrics)


@dataclass(slots=True)
class BestTransitionFrame:
    """最佳承接帧结果。"""

    frame_index: int
    timestamp_seconds: float
    frame_path: str
    continuity_score: float
    quality_score: float
    total_score: float
    reason: str
    best_dimensions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransitionFrameResult:
    """表示最佳承接帧分析结果。"""

    video_path: str
    current_shot_summary: str = ""
    next_shot_summary: str = ""
    candidate_frames: list[CandidateFrame] = field(default_factory=list)
    best_frame: BestTransitionFrame | None = None
