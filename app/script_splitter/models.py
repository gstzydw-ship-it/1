"""Data models for script-to-shots splitting."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScriptSplitRequest:
    script_text: str
    character_ref: str = ""
    scene_ref: str = ""
    shot_prefix: str = "scene"
    max_chars_per_shot: int = 80
    max_units_per_shot: int = 2
    workflow_mode: str = "manju_scene_batch"
    aspect_ratio: str = "16:9"
    model_name: str = "nano-banana-2"
    manju_mode: str = "普通模式"
    manju_resolution: str = "1080p"
    manju_model_name: str = "Seedance1.5-pro"
    manju_headless: bool = True


@dataclass(slots=True)
class SplitShot:
    storyboard_id: str
    storyboard_text: str
    character_ref: str = ""
    scene_ref: str = ""
    style_summary: str = ""
    current_shot_summary: str = ""
    continuity_requirements: str = ""
    source_text: str = ""


@dataclass(slots=True)
class ScriptSplitResult:
    workflow_mode: str
    character_ref: str
    scene_ref: str
    shots: list[SplitShot] = field(default_factory=list)
    splitting_notes: list[str] = field(default_factory=list)
    aspect_ratio: str = "16:9"
    model_name: str = "nano-banana-2"
    manju_mode: str = "普通模式"
    manju_resolution: str = "1080p"
    manju_model_name: str = "Seedance1.5-pro"
    manju_headless: bool = True
