"""OpenClaw 技能模板与本地 mock 技能实现。"""

from __future__ import annotations

from pathlib import Path

from app.openclaw.models import (
    AssetPlannerRequest,
    AssetPlannerResponse,
    CatalogAssetSummary,
    PromptComposerRequest,
    PromptComposerResponse,
    PromptTemplateName,
)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_NEGATIVE = "避免脸部漂移、服装变化、发型变化、手部畸形、错误肢体、背景闪烁、画面抖动过度、构图崩坏、多余人物、主体模糊。"

PROMPT_TEMPLATE_PROFILES: dict[PromptTemplateName, dict[str, str]] = {
    "default": {
        "camera": "中景推进，镜头稳定，主体居中",
        "lighting": "自然层次光影，主体清晰",
        "style": "通用平衡，适合即梦全能参考模式",
        "continuity": "保持角色、服装、场景和视线自然承接",
    },
    "cinematic": {
        "camera": "电影感推镜或跟镜，景别过渡顺滑",
        "lighting": "层次分明的电影光影，强调体积感和氛围",
        "style": "电影感、质感强、画面克制",
        "continuity": "延续上一镜头构图重心和镜头方向，避免跳轴",
    },
    "continuity_first": {
        "camera": "镜头衔接平稳，景别和机位变化克制",
        "lighting": "沿用上一镜头光线方向和亮度关系",
        "style": "连续性优先，允许风格表达适度收敛",
        "continuity": "优先锁定角色身份、服装、发型、视线和场景朝向",
    },
    "action_scene": {
        "camera": "强调动作轨迹和空间关系，镜头节奏明确",
        "lighting": "高对比动势光影，突出动作方向",
        "style": "动作感强，节奏清楚，空间关系稳定",
        "continuity": "动作从上一帧继续，不要突然换位或断开动势",
    },
    "character_focus": {
        "camera": "中近景或近景，突出面部表情和姿态",
        "lighting": "柔和聚焦光影，突出人物五官和情绪",
        "style": "人物表现优先，细节稳定，情绪清晰",
        "continuity": "保持角色面部特征、妆造和姿态延续",
    },
}


def load_prompt_template(template_name: str) -> str:
    """从 prompts 目录加载技能模板。"""

    return (PROMPTS_DIR / template_name).read_text(encoding="utf-8")


def get_asset_planner_template() -> str:
    """返回 AssetPlanner 模板。"""

    return load_prompt_template("asset_planner.txt")


def get_prompt_composer_template() -> str:
    """返回 PromptComposer 模板。"""

    return load_prompt_template("prompt_composer.txt")


def get_prompt_template_names() -> list[str]:
    """返回当前支持的 PromptComposer 模板名。"""

    return list(PROMPT_TEMPLATE_PROFILES.keys())


def run_asset_planner_skill(request: AssetPlannerRequest) -> AssetPlannerResponse:
    """本地 mock 版 AssetPlanner。"""

    _ = get_asset_planner_template()
    matched: list[CatalogAssetSummary] = []
    storyboard_text = request.storyboard_text.casefold()

    for asset in request.catalog_assets:
        haystacks = [asset.display_name.casefold(), asset.asset_id.casefold(), " ".join(asset.tags).casefold()]
        if any(item and item in storyboard_text for item in haystacks):
            matched.append(asset)

    if not matched:
        for preferred_type in ("character", "scene", "monster"):
            for asset in request.catalog_assets:
                if asset.type == preferred_type and asset not in matched:
                    matched.append(asset)
                    break

    deduped: list[CatalogAssetSummary] = []
    seen: set[str] = set()
    for asset in matched:
        if asset.asset_id not in seen:
            deduped.append(asset)
            seen.add(asset.asset_id)
        if len(deduped) >= 3:
            break

    reference_ids = [asset.jimeng_ref_name for asset in deduped]
    return AssetPlannerResponse(
        storyboard_id=request.storyboard_id,
        selected_assets=deduped,
        selection_reason="基于分镜关键词和素材类型优先级选择最少但有效的参考素材。",
        reference_assets=reference_ids,
        reference_strategy="优先保留角色主体、关键对手和核心场景，保证即梦参考图约束有效。",
        must_keep=reference_ids[:1],
        drop_if_needed=reference_ids[1:],
    )


def run_prompt_composer_skill(request: PromptComposerRequest) -> PromptComposerResponse:
    """本地 mock 版 PromptComposer。

    输出结构固定，面向 Seedance 2.0 / 即梦全能参考模式。
    连续性参考默认表示“已根据下一镜头内容，从上一镜头视频中筛选出的最佳承接帧”。
    """

    _ = get_prompt_composer_template()
    profile = PROMPT_TEMPLATE_PROFILES[request.prompt_template]
    ref_names = _build_reference_order(request)
    subject_text = _build_subject_text(request.selected_assets)
    action_text = _build_action_text(request.storyboard_text, request.selected_assets)
    scene_text = _build_scene_text(request.selected_assets)
    continuity_text = _build_continuity_text(request, profile["continuity"])

    prompt_main = "；".join(
        [
            f"主体：{subject_text}",
            f"动作：{action_text}",
            f"场景：{scene_text}",
            f"镜头：{profile['camera']}",
            f"光影：{profile['lighting']}",
            f"风格：{_build_style_text(request.style_summary, profile['style'])}",
            f"连续性：{continuity_text}",
        ]
    ) + "。"

    return PromptComposerResponse(
        storyboard_id=request.storyboard_id,
        shot_id=request.shot_id or request.storyboard_id,
        prompt_main=prompt_main,
        prompt_negative=PROMPT_NEGATIVE,
        ref_assets_in_order=ref_names,
        continuity_notes=_build_continuity_notes(request, profile["continuity"]),
    )


def _build_reference_order(request: PromptComposerRequest) -> list[str]:
    """构建即梦全能参考模式下的参考图顺序。"""

    character_refs = _collect_refs_by_type(request.selected_assets, "character")
    continuity_refs = [request.continuity_anchor] if request.continuity_anchor else []
    monster_refs = _collect_refs_by_type(request.selected_assets, "monster")
    scene_refs = _collect_refs_by_type(request.selected_assets, "scene")

    if request.prompt_template == "continuity_first":
        ordered_groups = [continuity_refs, character_refs, monster_refs, scene_refs]
    else:
        ordered_groups = [character_refs, continuity_refs, monster_refs, scene_refs]

    ordered: list[str] = []
    for group in ordered_groups:
        for ref_name in group:
            if ref_name and ref_name not in ordered:
                ordered.append(ref_name)
    return ordered


def _collect_refs_by_type(selected_assets: list[CatalogAssetSummary], asset_type: str) -> list[str]:
    """按素材类型提取参考图名称。"""

    refs: list[str] = []
    for asset in selected_assets:
        if asset.type == asset_type and asset.jimeng_ref_name not in refs:
            refs.append(asset.jimeng_ref_name)
    return refs


def _build_subject_text(selected_assets: list[CatalogAssetSummary]) -> str:
    """生成主体槽位文本。"""

    character_names = [asset.display_name for asset in selected_assets if asset.type == "character"]
    if character_names:
        return "、".join(character_names)

    fallback_names = [asset.display_name for asset in selected_assets]
    if fallback_names:
        return "、".join(fallback_names[:2])
    return "主体明确"


def _build_action_text(storyboard_text: str, selected_assets: list[CatalogAssetSummary]) -> str:
    """生成动作槽位文本。"""

    action_text = storyboard_text.strip().rstrip("。")
    monster_names = [asset.display_name for asset in selected_assets if asset.type == "monster"]
    if monster_names and all(name not in action_text for name in monster_names):
        action_text = f"{action_text}，与{'、'.join(monster_names)}形成明确互动"
    return action_text or "动作连贯推进"


def _build_scene_text(selected_assets: list[CatalogAssetSummary]) -> str:
    """生成场景槽位文本。"""

    scene_names = [asset.display_name for asset in selected_assets if asset.type == "scene"]
    monster_names = [asset.display_name for asset in selected_assets if asset.type == "monster"]
    scene_parts: list[str] = []

    if scene_names:
        scene_parts.append("、".join(scene_names))
    if monster_names:
        scene_parts.append(f"空间内包含{'、'.join(monster_names)}")
    return "，".join(scene_parts) if scene_parts else "场景关系清晰"


def _build_style_text(style_summary: str, template_style: str) -> str:
    """生成风格槽位文本。"""

    if style_summary:
        return f"{style_summary.strip().rstrip('。')}，{template_style}"
    return template_style


def _build_continuity_text(request: PromptComposerRequest, template_continuity: str) -> str:
    """生成连续性槽位文本。"""

    continuity_parts = []
    if request.continuity_anchor:
        continuity_parts.append(f"首要承接参考使用 {request.continuity_anchor}，它是为当前镜头筛选出的最佳承接帧")
    if request.previous_frame_summary:
        continuity_parts.append(request.previous_frame_summary.strip().rstrip("。"))
    continuity_parts.append(template_continuity)
    if request.continuity_requirements:
        continuity_parts.append(request.continuity_requirements.strip().rstrip("。"))
    return "，".join(part for part in continuity_parts if part)


def _build_continuity_notes(request: PromptComposerRequest, template_continuity: str) -> str:
    """生成给下一镜头使用的承接说明。"""

    notes = []
    if request.continuity_anchor:
        notes.append(f"下一镜头继续优先参考当前为其筛选出的 {request.continuity_anchor} 最佳承接帧")
    notes.append(template_continuity)
    if request.continuity_requirements:
        notes.append(request.continuity_requirements.strip().rstrip("。"))
    return "；".join(notes) + "。"
