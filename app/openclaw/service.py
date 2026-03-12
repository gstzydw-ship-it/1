"""OpenClaw 服务。"""

from __future__ import annotations

from pathlib import Path

from app.openclaw.client import OpenClawClient
from app.openclaw.models import (
    AssetPlannerRequest,
    CatalogAssetSummary,
    PromptComposerRequest,
    PromptSuggestion,
    SceneAnchorImageRequest,
    SceneAnchorImageResponse,
    SceneFeatureExtractionRequest,
    SceneFeatureExtractionResponse,
    SceneAnchorReviewRequest,
    SceneAnchorReviewResponse,
)


class OpenClawService:
    """为分镜生成参考图选择、提示词和场景锚点图。"""

    def __init__(self, client: OpenClawClient | None = None) -> None:
        self.client = client or OpenClawClient()

    def generate_storyboard_prompt(self, storyboard: object) -> object:
        """兼容旧骨架接口，基于简单分镜对象返回提示词结果。"""

        storyboard_id = "storyboard-001"
        storyboard_text = "请根据分镜生成视频。"
        if isinstance(storyboard, dict):
            storyboard_id = storyboard.get("storyboard_id") or storyboard_id
            storyboard_text = storyboard.get("storyboard_text") or storyboard.get("summary") or storyboard_text

        planner_response = self.client.run_asset_planner(
            AssetPlannerRequest(
                storyboard_id=storyboard_id,
                storyboard_text=storyboard_text,
                catalog_assets=[
                    CatalogAssetSummary(
                        asset_id="CHAR_PLACEHOLDER__v1",
                        type="character",
                        display_name="占位角色",
                        jimeng_ref_name="CHAR_PLACEHOLDER__v1",
                        tags=["character"],
                    )
                ],
            )
        )
        composer_response = self.client.run_prompt_composer(
            PromptComposerRequest(
                storyboard_id=storyboard_id,
                storyboard_text=storyboard_text,
                selected_assets=planner_response.selected_assets,
            )
        )
        return PromptSuggestion(
            storyboard_id=storyboard_id,
            prompt_text=composer_response.prompt_main,
            reference_asset_ids=composer_response.ref_assets_in_order,
        )

    def generate_scene_anchor_image(
        self,
        request_model: SceneAnchorImageRequest,
        *,
        project_root: Path | None = None,
    ) -> SceneAnchorImageResponse:
        """生成换场景时可作为首帧使用的锚点图。"""

        resolved_request = request_model
        if not request_model.output_path:
            base_dir = project_root or Path.cwd()
            output_path = base_dir / "outputs" / "images" / f"{request_model.shot_id}_scene_anchor.png"
            resolved_request = request_model.model_copy(update={"output_path": str(output_path)})
        return self.client.generate_scene_anchor_image(resolved_request)

    def review_scene_anchor_image(self, request_model: SceneAnchorReviewRequest) -> SceneAnchorReviewResponse:
        """审查换场景首帧锚点图是否可直接进入视频生成。"""

        return self.client.review_scene_anchor_image(request_model)

    def extract_scene_features(self, request_model: SceneFeatureExtractionRequest) -> SceneFeatureExtractionResponse:
        """提取同一场景跨视角仍需保留的建筑与空间特征。"""

        return self.client.extract_scene_features(request_model)
