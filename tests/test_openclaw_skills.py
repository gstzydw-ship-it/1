import json
from pathlib import Path

from app.openclaw.client import OpenClawClient, OpenClawMockRunner, derive_image_edits_endpoint
from app.openclaw.models import (
    AssetPlannerRequest,
    CatalogAssetSummary,
    PromptComposerRequest,
    SceneAnchorImageRequest,
    SceneAnchorImageResponse,
    SceneFeatureExtractionRequest,
    SceneAnchorReviewRequest,
)
from app.openclaw.service import OpenClawService
from app.openclaw.skills import (
    PROMPT_NEGATIVE,
    get_asset_planner_template,
    get_prompt_composer_template,
    get_prompt_template_names,
)


def _sample_assets() -> list[CatalogAssetSummary]:
    return [
        CatalogAssetSummary(
            asset_id="CHAR_林白__v1",
            type="character",
            display_name="林白",
            jimeng_ref_name="CHAR_林白__v1",
            tags=["character", "林白"],
        ),
        CatalogAssetSummary(
            asset_id="SCENE_古城门__v1",
            type="scene",
            display_name="古城门",
            jimeng_ref_name="SCENE_古城门__v1",
            tags=["scene", "古城门"],
        ),
        CatalogAssetSummary(
            asset_id="MON_赤焰狼__v1",
            type="monster",
            display_name="赤焰狼",
            jimeng_ref_name="MON_赤焰狼__v1",
            tags=["monster", "赤焰狼"],
        ),
    ]


def test_prompt_templates_exist_and_contain_required_fields() -> None:
    asset_template = get_asset_planner_template()
    prompt_template = get_prompt_composer_template()

    assert "catalog.json" in asset_template
    assert "reference_assets" in asset_template
    assert "reference_strategy" in asset_template
    assert "must_keep" in asset_template
    assert "drop_if_needed" in asset_template
    assert "JSON" in asset_template

    assert "Seedance 2.0 / 即梦" in prompt_template
    assert "固定输出字段" in prompt_template
    assert "default" in prompt_template
    assert "cinematic" in prompt_template
    assert "continuity_first" in prompt_template
    assert "action_scene" in prompt_template
    assert "character_focus" in prompt_template
    assert "@参考图" in prompt_template
    assert "@TransitionFrame" not in prompt_template or "最佳承接帧" in prompt_template
    assert "最佳承接帧" in prompt_template


def test_get_prompt_template_names_contains_all_expected_templates() -> None:
    assert get_prompt_template_names() == [
        "default",
        "cinematic",
        "continuity_first",
        "action_scene",
        "character_focus",
    ]


def test_run_asset_planner_selects_relevant_assets() -> None:
    client = OpenClawClient()
    request_model = AssetPlannerRequest(
        storyboard_id="sb-001",
        storyboard_text="林白站在古城门前，准备迎战赤焰狼。",
        style_summary="国风奇幻",
        catalog_assets=_sample_assets(),
    )

    response = client.run_asset_planner(request_model)

    assert response.storyboard_id == "sb-001"
    assert [asset.asset_id for asset in response.selected_assets] == [
        "CHAR_林白__v1",
        "SCENE_古城门__v1",
        "MON_赤焰狼__v1",
    ]
    assert response.reference_assets == [
        "CHAR_林白__v1",
        "SCENE_古城门__v1",
        "MON_赤焰狼__v1",
    ]
    assert response.reference_strategy
    assert response.must_keep == ["CHAR_林白__v1"]


def test_run_prompt_composer_outputs_stable_fields_and_negative_prompt() -> None:
    client = OpenClawClient()
    request_model = PromptComposerRequest(
        storyboard_id="sb-002",
        shot_id="shot-002",
        storyboard_text="林白穿过古城门，迎战逼近的赤焰狼。",
        style_summary="电影感，晨雾氛围",
        selected_assets=_sample_assets(),
        previous_frame_summary="上一镜头结束时林白面向城门，披风向右后方摆动",
        continuity_requirements="保持林白服装、发型、视线方向和古城门空间朝向一致",
    )

    response = client.run_prompt_composer(request_model)

    assert response.storyboard_id == "sb-002"
    assert response.shot_id == "shot-002"
    assert response.prompt_main.startswith("主体：林白")
    assert "动作：林白穿过古城门，迎战逼近的赤焰狼" in response.prompt_main
    assert "场景：古城门，空间内包含赤焰狼" in response.prompt_main
    assert "镜头：" in response.prompt_main
    assert "光影：" in response.prompt_main
    assert "风格：电影感，晨雾氛围" in response.prompt_main
    assert "连续性：首要承接参考使用 @TransitionFrame，它是为当前镜头筛选出的最佳承接帧" in response.prompt_main
    assert response.prompt_negative == PROMPT_NEGATIVE
    assert response.ref_assets_in_order == ["CHAR_林白__v1", "@TransitionFrame", "MON_赤焰狼__v1", "SCENE_古城门__v1"]
    assert "下一镜头继续优先参考当前为其筛选出的 @TransitionFrame 最佳承接帧" in response.continuity_notes


def test_prompt_composer_template_switch_changes_reference_priority_and_text() -> None:
    client = OpenClawClient()
    common = {
        "storyboard_id": "sb-003",
        "shot_id": "shot-003",
        "storyboard_text": "林白拔剑转身，准备迎战。",
        "style_summary": "国风奇幻",
        "selected_assets": _sample_assets(),
        "previous_frame_summary": "上一镜头里林白刚完成转身",
        "continuity_requirements": "保持角色朝向和拔剑动作衔接",
    }

    default_response = client.run_prompt_composer(PromptComposerRequest(**common, prompt_template="default"))
    cinematic_response = client.run_prompt_composer(PromptComposerRequest(**common, prompt_template="cinematic"))
    continuity_response = client.run_prompt_composer(PromptComposerRequest(**common, prompt_template="continuity_first"))

    assert default_response.ref_assets_in_order == [
        "CHAR_林白__v1",
        "@TransitionFrame",
        "MON_赤焰狼__v1",
        "SCENE_古城门__v1",
    ]
    assert continuity_response.ref_assets_in_order == [
        "@TransitionFrame",
        "CHAR_林白__v1",
        "MON_赤焰狼__v1",
        "SCENE_古城门__v1",
    ]
    assert "电影感推镜或跟镜" in cinematic_response.prompt_main
    assert "优先锁定角色身份、服装、发型、视线和场景朝向" in continuity_response.prompt_main
    assert default_response.prompt_main != cinematic_response.prompt_main
    assert default_response.prompt_main != continuity_response.prompt_main


def test_openclaw_client_uses_cache() -> None:
    class CountingRunner(OpenClawMockRunner):
        def __init__(self) -> None:
            self.asset_calls = 0

        def asset_planner(self, request_model):
            self.asset_calls += 1
            return super().asset_planner(request_model)

    runner = CountingRunner()
    client = OpenClawClient(runner=runner)
    request_model = AssetPlannerRequest(
        storyboard_id="sb-004",
        storyboard_text="林白看向古城门。",
        catalog_assets=_sample_assets(),
    )

    first = client.run_asset_planner(request_model)
    second = client.run_asset_planner(request_model)

    assert first.model_dump() == second.model_dump()
    assert runner.asset_calls == 1
    assert client.cache_size == 1


def test_build_asset_planner_request_from_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "total_assets": 2,
                "assets": [
                    {
                        "asset_id": "CHAR_林白__v1",
                        "type": "character",
                        "display_name": "林白",
                        "jimeng_ref_name": "CHAR_林白__v1",
                        "files": ["assets/characters/林白_1.png"],
                        "tags": ["character", "林白"],
                    },
                    {
                        "asset_id": "SCENE_古城门__v1",
                        "type": "scene",
                        "display_name": "古城门",
                        "jimeng_ref_name": "SCENE_古城门__v1",
                        "files": ["assets/scenes/古城门1.jpg"],
                        "tags": ["scene", "古城门"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    client = OpenClawClient()
    request_model = client.build_asset_planner_request_from_catalog(
        storyboard_id="sb-005",
        storyboard_text="林白站在古城门前。",
        style_summary="国风奇幻",
        catalog_path=catalog_path,
    )

    assert request_model.storyboard_id == "sb-005"
    assert request_model.storyboard_text == "林白站在古城门前。"
    assert request_model.style_summary == "国风奇幻"
    assert len(request_model.catalog_assets) == 2
    assert request_model.catalog_assets[0].asset_id == "CHAR_林白__v1"


def test_derive_image_edits_endpoint_supports_openai_compatible_proxy() -> None:
    assert (
        derive_image_edits_endpoint("https://ai.comfly.chat/v1/chat/completions")
        == "https://ai.comfly.chat/v1/images/edits"
    )
    assert derive_image_edits_endpoint("https://ai.comfly.chat/v1") == "https://ai.comfly.chat/v1/images/edits"


def test_openclaw_service_generates_default_scene_anchor_output_path(tmp_path: Path) -> None:
    request_model = SceneAnchorImageRequest(
        shot_id="scene-anchor-demo",
        prompt="主体：周浩天；场景：时光屋报名处；镜头：固定中景。",
        character_reference_paths=[str(tmp_path / "character.png")],
        scene_reference_paths=[str(tmp_path / "scene.jpg")],
    )

    class StubClient(OpenClawClient):
        def generate_scene_anchor_image(self, request_model: SceneAnchorImageRequest) -> SceneAnchorImageResponse:
            return SceneAnchorImageResponse(
                shot_id=request_model.shot_id,
                prompt=request_model.prompt,
                model_name=request_model.model_name,
                aspect_ratio=request_model.aspect_ratio,
                output_path=request_model.output_path or "",
                source_images=[*request_model.character_reference_paths, *request_model.scene_reference_paths],
            )

    service = OpenClawService(client=StubClient())
    response = service.generate_scene_anchor_image(request_model, project_root=tmp_path)

    assert response.output_path == str(tmp_path / "outputs" / "images" / "scene-anchor-demo_scene_anchor.png")


def test_openclaw_client_review_scene_anchor_image_parses_response(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "anchor.png"
    image_path.write_bytes(b"fake-image")

    client = OpenClawClient()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://ai.comfly.chat/v1/chat/completions")

    def _fake_review(*args, **kwargs):
        return json.dumps(
            {
                "action": "revise",
                "selected_issue_ids": ["scene_mismatch"],
                "review_summary": "场景大体可用，但还有轻微跑偏。",
                "prompt_patch": "保持普通学院教室场景，不要混入额外建筑元素。",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(client, "_is_openai_compatible_base_url", lambda base_url: True)
    monkeypatch.setattr(client, "_review_scene_anchor_openai_compatible", _fake_review)

    response = client.review_scene_anchor_image(
        SceneAnchorReviewRequest(
            shot_id="anchor-review-001",
            prompt="主体：林白；场景：教室；镜头：固定中景。",
            image_path=str(image_path),
            character_name="林白",
            scene_name="教室",
            source_images=[str(image_path)],
        )
    )

    assert response.action == "revise"
    assert response.selected_issue_ids == ["scene_mismatch"]
    assert response.prompt_patch == "保持普通学院教室场景，不要混入额外建筑元素。"
    assert "补充约束" in response.revised_prompt


def test_openclaw_client_extract_scene_features_parses_response(monkeypatch, tmp_path: Path) -> None:
    scene_path = tmp_path / "scene.jpg"
    continuity_path = tmp_path / "transition.jpg"
    scene_path.write_bytes(b"fake-scene")
    continuity_path.write_bytes(b"fake-transition")

    client = OpenClawClient()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_BASE_URL", "https://ai.comfly.chat/v1/chat/completions")

    def _fake_extract(*args, **kwargs):
        return json.dumps(
            {
                "architecture_style": "现代校园宿舍区，浅色教学楼与宿舍立面统一。",
                "layout_summary": "道路沿建筑立面前方延伸，建筑群左右展开，透视朝道路前方收束。",
                "anchor_landmarks": ["浅色宿舍楼立面", "连续路灯", "道路边缘绿化带"],
                "preserved_elements": ["浅色立面", "路灯间距", "道路朝向"],
                "forbidden_elements": ["古典牌楼", "霓虹招牌", "欧式尖顶"],
                "camera_guidance": "可以切到侧面中景，沿道路方向平行观察。",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(client, "_is_openai_compatible_base_url", lambda base_url: True)
    monkeypatch.setattr(client, "_extract_scene_features_openai_compatible", _fake_extract)

    response = client.extract_scene_features(
        SceneFeatureExtractionRequest(
            scene_name="学校宿舍外道路",
            image_paths=[str(scene_path), str(continuity_path)],
            continuity_note="上一镜头人物沿道路向前。",
        )
    )

    assert response.scene_name == "学校宿舍外道路"
    assert "现代校园宿舍区" in response.architecture_style
    assert response.anchor_landmarks == ["浅色宿舍楼立面", "连续路灯", "道路边缘绿化带"]
    assert response.forbidden_elements == ["古典牌楼", "霓虹招牌", "欧式尖顶"]
    assert "建筑风格" in response.scene_signature_text
