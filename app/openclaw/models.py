"""OpenClaw 技能数据模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PromptTemplateName = Literal[
    "default",
    "cinematic",
    "continuity_first",
    "action_scene",
    "character_focus",
]
ImageReviewStatus = Literal["pending", "approve", "revise", "reject"]


class CatalogAssetSummary(BaseModel):
    """供技能使用的素材摘要。"""

    asset_id: str
    type: str
    display_name: str
    jimeng_ref_name: str
    tags: list[str] = Field(default_factory=list)


class AssetPlannerRequest(BaseModel):
    """AssetPlanner 输入。"""

    storyboard_id: str
    storyboard_text: str
    style_summary: str = ""
    catalog_assets: list[CatalogAssetSummary] = Field(default_factory=list)


class AssetPlannerResponse(BaseModel):
    """AssetPlanner 输出。"""

    storyboard_id: str
    selected_assets: list[CatalogAssetSummary] = Field(default_factory=list)
    selection_reason: str = ""
    reference_assets: list[str] = Field(default_factory=list)
    reference_strategy: str = ""
    must_keep: list[str] = Field(default_factory=list)
    drop_if_needed: list[str] = Field(default_factory=list)


class PromptComposerRequest(BaseModel):
    """PromptComposer 输入。"""

    storyboard_id: str
    shot_id: str | None = None
    storyboard_text: str
    style_summary: str = ""
    selected_assets: list[CatalogAssetSummary] = Field(default_factory=list)
    prompt_template: PromptTemplateName = "default"
    continuity_anchor: str = "@TransitionFrame"
    previous_frame_summary: str = ""
    continuity_requirements: str = ""


class PromptComposerResponse(BaseModel):
    """PromptComposer 输出。"""

    storyboard_id: str
    shot_id: str
    prompt_main: str
    prompt_negative: str
    ref_assets_in_order: list[str] = Field(default_factory=list)
    continuity_notes: str


class PromptSuggestion(BaseModel):
    """兼容旧骨架的提示词结果。"""

    storyboard_id: str
    prompt_text: str
    reference_asset_ids: list[str] = Field(default_factory=list)


class SceneAnchorImageRequest(BaseModel):
    """换场景时的首帧锚点图生成请求。"""

    shot_id: str
    storyboard_text: str = ""
    prompt: str
    character_reference_paths: list[str] = Field(default_factory=list)
    scene_reference_paths: list[str] = Field(default_factory=list)
    model_name: str = "nano-banana-2"
    aspect_ratio: str = "16:9"
    output_path: str | None = None


class SceneAnchorImageResponse(BaseModel):
    """换场景首帧锚点图生成结果。"""

    shot_id: str
    prompt: str
    model_name: str
    aspect_ratio: str
    output_path: str
    source_images: list[str] = Field(default_factory=list)
    provider: str = "third_party_image_edits"
    review_status: ImageReviewStatus = "pending"
    image_url: str = ""


class SceneAnchorReviewRequest(BaseModel):
    """场景锚点图审查请求。"""

    shot_id: str
    storyboard_text: str = ""
    prompt: str
    image_path: str
    character_name: str
    scene_name: str
    source_images: list[str] = Field(default_factory=list)


class SceneAnchorReviewResponse(BaseModel):
    """场景锚点图审查结果。"""

    shot_id: str
    action: ImageReviewStatus
    review_summary: str
    selected_issue_ids: list[str] = Field(default_factory=list)
    prompt_patch: str = ""
    revised_prompt: str = ""
    model_name: str = ""
