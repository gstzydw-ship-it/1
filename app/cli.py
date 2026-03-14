"""Typer CLI 入口。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer

from app.asset_catalog import AssetCatalogService, find_catalog_asset, load_asset_catalog, resolve_catalog_asset_reference
from app.config import get_config
from app.db.engine import build_engine
from app.feishu_sync import FeishuSyncConfig, sync_assets
from app.feishu_sync.client import FeishuApiError
from app.feishu_sync.service import inspect_feishu_link_source, parse_feishu_link
from app.jimeng_operator.models import (
    AuditIssueOption,
    GeminiAuditConfig,
    JimengDryRunRequest,
    JimengOneShotRequest,
    PromptAuditDecision,
)
from app.jimeng_operator.gemini_audit import GeminiAuditError, GeminiVideoAuditClient
from app.jimeng_operator.web_operator import JimengWebOperator, build_default_jimeng_config
from app.openclaw import (
    CatalogAssetSummary,
    OpenClawClient,
    OpenClawService,
    PromptComposerRequest,
    SceneAnchorImageError,
    SceneAnchorImageRequest,
    SceneFeatureExtractionError,
    SceneFeatureExtractionRequest,
    SceneAnchorReviewError,
    SceneAnchorReviewRequest,
)
from app.openclaw.skills import get_prompt_template_names
from app.orchestrator import Orchestrator
from app.script_splitter import ScriptSplitRequest, ScriptSplitterService
from app.video_analyzer.analyze import extract_review_frames, extract_transition_frame
from app.video_analyzer import VideoAnalyzerService

app = typer.Typer(help="视频 Agent 系统命令行入口。")

_AUDIT_ISSUES = [
    AuditIssueOption("script_mismatch", "画面内容不符合当前剧本", "严格按当前镜头剧本执行，不要遗漏关键动作、表情或台词语境。"),
    AuditIssueOption("character_drift", "人物脸部/服装/发型出现漂移", "保持主角外观、服装、发型和脸部稳定，不要换脸、串角或服装突变。"),
    AuditIssueOption("face_identity_drift", "主角近景脸部形象不稳定或与参考不符", "主角脸部必须稳定且与参考图一致，五官比例、眼型、发际线和年龄感不要变化。"),
    AuditIssueOption("face_visibility_unusable", "主角脸部过远、过糊或不可辨认", "镜头必须让主角脸部清晰可辨，避免远景小人、脸部过小、过糊或被遮挡。"),
    AuditIssueOption("motion_mismatch", "动作阶段不对或承接不顺", "动作必须从当前镜头要求的阶段开始，不要回退到上一动作，也不要跳到后续动作。"),
    AuditIssueOption("scene_drift", "场景或空间关系不对", "保持场景空间关系、镜头方位和人物站位一致，不要切到无关背景。"),
    AuditIssueOption("lighting_jump", "光线突变或氛围不连续", "保持光线方向、亮度层次和整体氛围稳定，不要出现突兀的光影跳变。"),
    AuditIssueOption("action_blur", "动作过度模糊或中间态太乱", "避免动作中段过度模糊，优先选择动作收束、主体姿态清晰可读的画面。"),
    AuditIssueOption("pose_unreadable", "主体姿态不可读", "保持主体姿态、朝向和视线清晰可读，避免关键肢体被遮挡或难以辨认。"),
    AuditIssueOption("background_flicker", "背景闪烁或环境跳变", "避免背景闪烁、环境元素跳变和局部场景忽隐忽现。"),
    AuditIssueOption("camera_instability", "镜头异常抖动或主体不稳定", "保持镜头运动平稳，避免异常抖动、主体漂移和构图失衡。"),
    AuditIssueOption("transition_unusable", "@TransitionFrame 不适合作为下一镜头承接帧", "确保当前视频里能抽到适合下一镜头的最佳承接帧，主体、场景和动作收束都要便于衔接。"),
    AuditIssueOption("deform_glitch", "人物畸形/穿模/多人错乱", "避免手部畸形、肢体错误、穿模、多余人物和主体错乱。"),
    AuditIssueOption("text_overlay", "出现字幕/文字/logo/水印", "画面中禁止出现字幕、文字、logo、水印和多余界面元素。"),
]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _load_dotenv() -> None:
    """从项目根目录加载最小 .env 配置。"""

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _notify_local(title: str, message: str) -> None:
    """在本地尽力发送一个完成提醒。"""

    try:
        import winsound

        winsound.MessageBeep()
    except Exception:
        pass

    if os.name != "nt":
        return

    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = '{title.replace("'", "''")}'
$notify.BalloonTipText = '{message.replace("'", "''")}'
$notify.Visible = $true
$notify.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$notify.Dispose()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _issue_map() -> dict[str, AuditIssueOption]:
    return {issue.issue_id: issue for issue in _AUDIT_ISSUES}


def _build_prompt_patch(selected_issue_ids: list[str], extra_notes: str) -> str:
    patches: list[str] = []
    issue_map = _issue_map()
    for issue_id in selected_issue_ids:
        issue = issue_map.get(issue_id)
        if issue and issue.patch_hint not in patches:
            patches.append(issue.patch_hint)

    cleaned_extra_notes = extra_notes.strip()
    if cleaned_extra_notes:
        patches.append(cleaned_extra_notes.rstrip("。") + "。")
    return "；".join(patches)


def _apply_prompt_patch(prompt_main: str, prompt_patch: str) -> str:
    if not prompt_patch.strip():
        return prompt_main
    if prompt_patch in prompt_main:
        return prompt_main
    return f"{prompt_main}；补充约束：{prompt_patch}"


def _build_audit_report_html(
    *,
    shot_id: str,
    storyboard_text: str,
    prompt_main: str,
    prompt_negative: str,
    ref_assets_in_order: list[str],
    issues: list[AuditIssueOption],
    action: str = "",
    review_summary: str = "",
    selected_issue_ids: list[str] | None = None,
    prompt_patch: str = "",
    revised_prompt_main: str = "",
) -> str:
    selected_issue_ids = selected_issue_ids or []
    issue_items = "\n".join(
        (
            f"<li><strong>{issue.label}</strong><br>"
            f"<code>{issue.issue_id}</code><br>"
            f"<span>{issue.patch_hint}</span></li>"
        )
        for issue in issues
    )
    selected_items = ", ".join(selected_issue_ids) if selected_issue_ids else "无"
    ref_assets_text = ", ".join(ref_assets_in_order) if ref_assets_in_order else "无"
    action_text = action or "待人工审计"
    summary_text = review_summary or "无"
    prompt_patch_text = prompt_patch or "无"
    revised_prompt_text = revised_prompt_main or "无"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{shot_id} 下载前审计</title>
  <style>
    body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 24px; line-height: 1.6; background: #f6f7fb; color: #1f2430; }}
    .card {{ background: white; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 8px 24px rgba(16, 24, 40, 0.08); }}
    code, pre {{ background: #f3f4f8; padding: 2px 6px; border-radius: 6px; }}
    pre {{ white-space: pre-wrap; padding: 12px; }}
    h1, h2 {{ margin-top: 0; }}
    ul {{ padding-left: 20px; }}
    .warning {{ color: #8a2d0b; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>下载前审计</h1>
    <p><strong>shot_id：</strong>{shot_id}</p>
    <p class="warning">请在即梦页面直接预览当前视频，再结合本页检查项决定是否下载。</p>
  </div>
  <div class="card">
    <h2>剧本与提示词</h2>
    <p><strong>剧本摘要</strong></p>
    <pre>{storyboard_text}</pre>
    <p><strong>prompt_main</strong></p>
    <pre>{prompt_main}</pre>
    <p><strong>prompt_negative</strong></p>
    <pre>{prompt_negative or "无"}</pre>
    <p><strong>@参考图顺序</strong> {ref_assets_text}</p>
  </div>
  <div class="card">
    <h2>审计检查项</h2>
    <ul>{issue_items}</ul>
  </div>
  <div class="card">
    <h2>当前审计结果</h2>
    <p><strong>动作：</strong>{action_text}</p>
    <p><strong>审计摘要：</strong></p>
    <pre>{summary_text}</pre>
    <p><strong>问题项：</strong>{selected_items}</p>
    <p><strong>小幅修正补丁：</strong></p>
    <pre>{prompt_patch_text}</pre>
    <p><strong>修正后 prompt_main：</strong></p>
    <pre>{revised_prompt_text}</pre>
  </div>
</body>
</html>
"""


def _write_audit_report(
    *,
    report_path: Path,
    shot_id: str,
    storyboard_text: str,
    prompt_main: str,
    prompt_negative: str,
    ref_assets_in_order: list[str],
    action: str = "",
    review_summary: str = "",
    selected_issue_ids: list[str] | None = None,
    prompt_patch: str = "",
    revised_prompt_main: str = "",
) -> Path:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    html = _build_audit_report_html(
        shot_id=shot_id,
        storyboard_text=storyboard_text,
        prompt_main=prompt_main,
        prompt_negative=prompt_negative,
        ref_assets_in_order=ref_assets_in_order,
        issues=_AUDIT_ISSUES,
        action=action,
        review_summary=review_summary,
        selected_issue_ids=selected_issue_ids,
        prompt_patch=prompt_patch,
        revised_prompt_main=revised_prompt_main,
    )
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _open_audit_report(report_path: Path) -> None:
    try:
        webbrowser.open(report_path.resolve().as_uri())
    except Exception:
        pass


def _run_download_audit(
    *,
    shot_id: str,
    storyboard_text: str,
    prompt_main: str,
    prompt_negative: str,
    ref_assets_in_order: list[str],
    report_path: Path,
) -> PromptAuditDecision:
    _write_audit_report(
        report_path=report_path,
        shot_id=shot_id,
        storyboard_text=storyboard_text,
        prompt_main=prompt_main,
        prompt_negative=prompt_negative,
        ref_assets_in_order=ref_assets_in_order,
    )
    _open_audit_report(report_path)

    typer.echo("下载前视频审计")
    typer.echo(f"- 审计报告: {report_path}")
    typer.echo("- 请先在即梦页面预览当前生成视频，再参考本地审计报告。")
    typer.echo("- 可用操作: approve / revise / reject")
    typer.echo("- 可选问题项:")
    for issue in _AUDIT_ISSUES:
        typer.echo(f"  - {issue.issue_id}: {issue.label}")

    action = typer.prompt("请输入审计结果", default="approve").strip().lower()
    while action not in {"approve", "revise", "reject"}:
        typer.echo("请输入 approve / revise / reject 其中之一。")
        action = typer.prompt("请输入审计结果", default="approve").strip().lower()

    decision = PromptAuditDecision(action=action, report_path=str(report_path))
    if action != "revise":
        _write_audit_report(
            report_path=report_path,
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            action=action,
            review_summary="人工审计已完成。",
        )
        return decision

    issue_input = typer.prompt("请输入问题项 issue_id，多个用英文逗号分隔", default="script_mismatch")
    selected_issue_ids = [item.strip() for item in issue_input.split(",") if item.strip() and item.strip() in _issue_map()]
    extra_notes = typer.prompt("可选：补充一句小幅修正说明", default="").strip()
    prompt_patch = _build_prompt_patch(selected_issue_ids, extra_notes)
    revised_prompt_main = _apply_prompt_patch(prompt_main, prompt_patch)
    decision.selected_issue_ids = selected_issue_ids
    decision.extra_notes = extra_notes
    decision.review_summary = "人工审计判定当前视频需要小幅修正后再重生。"
    decision.prompt_patch = prompt_patch
    decision.revised_prompt_main = revised_prompt_main

    _write_audit_report(
        report_path=report_path,
        shot_id=shot_id,
        storyboard_text=storyboard_text,
        prompt_main=prompt_main,
        prompt_negative=prompt_negative,
        ref_assets_in_order=ref_assets_in_order,
        action=action,
        review_summary=decision.review_summary,
        selected_issue_ids=selected_issue_ids,
        prompt_patch=prompt_patch,
        revised_prompt_main=revised_prompt_main,
    )
    return decision


def _build_gemini_audit_config() -> GeminiAuditConfig:
    api_key = os.getenv("GEMINI_AUDIT_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise typer.BadParameter(
            "缺少环境变量 GEMINI_AUDIT_API_KEY；如需兼容旧配置，也可继续使用 GEMINI_API_KEY。"
        )

    model_name = (
        os.getenv("GEMINI_AUDIT_MODEL", "").strip()
        or os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        or "gemini-2.5-flash"
    )
    base_url = (
        os.getenv("GEMINI_AUDIT_BASE_URL", "").strip()
        or os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").strip()
    )
    return GeminiAuditConfig(
        api_key=api_key,
        model_name=model_name,
        base_url=base_url,
    )


def _run_gemini_auto_audit(
    *,
    shot_id: str,
    storyboard_text: str,
    prompt_main: str,
    prompt_negative: str,
    ref_assets_in_order: list[str],
    report_path: Path,
    review_video_path: Path,
    review_frames_dir: Path,
) -> PromptAuditDecision:
    gemini_config = _build_gemini_audit_config()
    review_frames = extract_review_frames(review_video_path, review_frames_dir, frame_count=gemini_config.frame_count)
    client = GeminiVideoAuditClient(gemini_config)
    audit_result = client.audit_frames(
        shot_id=shot_id,
        storyboard_text=storyboard_text,
        prompt_main=prompt_main,
        prompt_negative=prompt_negative,
        ref_assets_in_order=ref_assets_in_order,
        frame_paths=review_frames,
        temp_video_path=review_video_path,
        issue_options=_AUDIT_ISSUES,
    )
    revised_prompt_main = _apply_prompt_patch(prompt_main, audit_result.prompt_patch)
    decision = PromptAuditDecision(
        action=audit_result.action,
        selected_issue_ids=audit_result.selected_issue_ids,
        review_summary=audit_result.review_summary,
        prompt_patch=audit_result.prompt_patch,
        revised_prompt_main=revised_prompt_main if audit_result.prompt_patch else "",
        report_path=str(report_path),
    )
    _write_audit_report(
        report_path=report_path,
        shot_id=shot_id,
        storyboard_text=storyboard_text,
        prompt_main=prompt_main,
        prompt_negative=prompt_negative,
        ref_assets_in_order=ref_assets_in_order,
        action=f"auto:{decision.action}",
        review_summary=decision.review_summary,
        selected_issue_ids=decision.selected_issue_ids,
        prompt_patch=decision.prompt_patch,
        revised_prompt_main=decision.revised_prompt_main,
    )
    return decision


def _default_openclaw_sample() -> dict[str, str]:
    return {
        "storyboard_id": "demo-storyboard-001",
        "storyboard_text": "林白站在古城门前，镜头缓慢推进，准备迎战来袭妖兽。",
        "style_summary": "国风奇幻，电影感，主体清晰，镜头语言稳定。",
        "previous_frame_summary": "上一镜头结束时林白面向城门，披风向右后方摆动",
        "continuity_requirements": "保持林白服装、发型、视线方向和古城门空间朝向一致",
    }


def _load_openclaw_sample(sample_path: Path | None) -> dict[str, str]:
    if sample_path is None:
        return _default_openclaw_sample()
    if not sample_path.exists():
        raise typer.BadParameter(f"找不到分镜输入文件: {sample_path}")

    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    return {
        "storyboard_id": payload.get("storyboard_id", "custom-storyboard"),
        "storyboard_text": payload.get("storyboard_text", ""),
        "style_summary": payload.get("style_summary", ""),
        "previous_frame_summary": payload.get("previous_frame_summary", ""),
        "continuity_requirements": payload.get("continuity_requirements", ""),
    }


def _resolve_catalog_path(project_root: Path) -> Path:
    candidates = [
        project_root / "catalog.json",
        project_root / "assets" / "catalog.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise typer.BadParameter(
        "未找到 catalog.json。请先运行 `python -m app.cli build-asset-catalog`，默认输出位置是项目下的 assets/catalog.json。"
    )


def _catalog_asset_to_summary(asset: object) -> CatalogAssetSummary:
    """将 catalog 资产对象转成 OpenClaw 需要的摘要结构。"""

    return CatalogAssetSummary(
        asset_id=asset.asset_id,
        type=asset.type,
        display_name=asset.display_name,
        jimeng_ref_name=asset.jimeng_ref_name,
        tags=list(asset.tags),
    )


def _resolve_reference_files_from_catalog(catalog_path: Path, selected_assets: list[CatalogAssetSummary]) -> list[Path]:
    """从 catalog 中为已选素材解析本地参考图路径。"""

    catalog = load_asset_catalog(catalog_path)
    asset_map = {asset.asset_id: asset for asset in catalog.assets}
    file_paths: list[Path] = []
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov"}

    for selected_asset in selected_assets:
        catalog_asset = asset_map.get(selected_asset.asset_id)
        if catalog_asset is None or not catalog_asset.files:
            continue
        for raw_file in catalog_asset.files:
            candidate = Path(raw_file)
            if not candidate.is_absolute():
                candidate = catalog_path.parent.parent / candidate
            if candidate.exists() and candidate.suffix.lower() in allowed_suffixes and candidate not in file_paths:
                file_paths.append(candidate)
                break
    return file_paths


def _find_catalog_asset(catalog_path: Path, query: str, expected_type: str) -> object:
    """按 asset_id、jimeng_ref_name 或 display_name 查找指定类型素材。"""

    catalog = load_asset_catalog(catalog_path)
    normalized_query = query.strip().casefold()
    for asset in catalog.assets:
        if asset.type != expected_type:
            continue
        haystacks = [asset.asset_id, asset.jimeng_ref_name, asset.display_name]
        if any(item.casefold() == normalized_query for item in haystacks):
            return asset
    type_labels = {"character": "角色", "scene": "场景", "monster": "妖兽"}
    raise typer.BadParameter(f"未找到匹配的{type_labels.get(expected_type, expected_type)}素材: {query}")


def _resolve_catalog_asset_image(catalog_path: Path, query: str, expected_type: str) -> tuple[object, Path]:
    """从 catalog 中解析指定素材的第一张可用图片。"""

    asset = _find_catalog_asset(catalog_path, query, expected_type)
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for raw_file in asset.files:
        candidate = Path(raw_file)
        if not candidate.is_absolute():
            candidate = catalog_path.parent.parent / candidate
        if candidate.exists() and candidate.suffix.lower() in allowed_suffixes:
            return asset, candidate
    type_labels = {"character": "角色", "scene": "场景", "monster": "妖兽"}
    raise typer.BadParameter(f"{type_labels.get(expected_type, expected_type)}素材缺少可用图片文件: {query}")


def _resolve_reference_files_from_catalog(catalog_path: Path, selected_assets: list[CatalogAssetSummary]) -> list[Path]:
    file_paths: list[Path] = []
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".mp4", ".mov"}

    for selected_asset in selected_assets:
        try:
            resolved_reference = resolve_catalog_asset_reference(
                catalog_path,
                selected_asset.asset_id,
                selected_asset.type,
                allowed_suffixes=tuple(allowed_suffixes),
            )
        except ValueError:
            continue
        if resolved_reference.selected_file not in file_paths:
            file_paths.append(resolved_reference.selected_file)
    return file_paths


def _find_catalog_asset(catalog_path: Path, query: str, expected_type: str) -> object:
    try:
        return find_catalog_asset(catalog_path, query, expected_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _resolve_catalog_asset_image(
    catalog_path: Path,
    query: str,
    expected_type: str,
    *,
    preferred_index: int = 0,
) -> tuple[object, Path]:
    try:
        resolved_reference = resolve_catalog_asset_reference(
            catalog_path,
            query,
            expected_type,
            preferred_index=preferred_index,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return resolved_reference.asset, resolved_reference.selected_file


def _resolve_extra_reference_images(project_root: Path, queries: Sequence[str]) -> list[tuple[str, Path]]:
    extra_root = project_root / "assets" / "extras"
    allowed_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    resolved: list[tuple[str, Path]] = []
    seen_paths: set[Path] = set()

    def _display_name_for_path(path: Path) -> str:
        stem = path.stem
        if "_" in stem:
            prefix, suffix = stem.rsplit("_", 1)
            if suffix.isdigit():
                return prefix
        return stem

    for raw_query in queries:
        query = str(raw_query).strip()
        if not query:
            continue

        candidate_path = Path(query)
        if not candidate_path.is_absolute():
            candidate_path = (project_root / query).resolve()
        if candidate_path.exists() and candidate_path.suffix.lower() in allowed_suffixes:
            if candidate_path not in seen_paths:
                resolved.append((_display_name_for_path(candidate_path), candidate_path))
                seen_paths.add(candidate_path)
            continue

        if not extra_root.exists():
            raise typer.BadParameter(f"找不到额外参考图目录: {extra_root}")

        normalized_query = query.casefold()
        matches: list[Path] = []
        for path in extra_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            haystacks = {
                path.stem.casefold(),
                path.name.casefold(),
                path.as_posix().casefold(),
            }
            if any(normalized_query in haystack for haystack in haystacks):
                matches.append(path.resolve())

        if not matches:
            raise typer.BadParameter(f"找不到额外参考图: {query}")

        selected = sorted(matches)[0]
        if selected not in seen_paths:
            resolved.append((_display_name_for_path(selected), selected))
            seen_paths.add(selected)

    return resolved


def _resolve_existing_image_paths(raw_paths: Sequence[Path | str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        if not raw_path:
            continue
        candidate = Path(raw_path).resolve()
        if not candidate.exists():
            raise typer.BadParameter(f"找不到图片文件：{candidate}")
        if candidate not in seen:
            resolved.append(candidate)
            seen.add(candidate)
    return resolved


def _strip_parenthetical_text(text: str) -> str:
    cleaned = re.sub(r"（[^）]*）", "", text)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_visual_story_action_text(
    *,
    storyboard_text: str,
    character_name: str,
    extra_subject_names: Sequence[str] = (),
) -> str:
    text = _strip_parenthetical_text(storyboard_text.strip())
    if not text:
        if extra_subject_names:
            return f"{character_name}与{', '.join(extra_subject_names)}稳定入镜"
        return f"{character_name}稳定入镜"

    segments = [segment.strip("，, ") for segment in re.split(r"[。！？；]", text) if segment.strip("，, ")]
    visual_segments: list[str] = []
    for segment in segments:
        lowered = segment.casefold()
        if "内心os" in lowered or lowered.endswith("os"):
            continue
        if "：" in segment or ":" in segment:
            speaker, _, _ = segment.replace(":", "：").partition("：")
            speaker = speaker.strip()
            if "内心OS" in speaker or "内心os" in speaker.casefold() or speaker.casefold().endswith("os"):
                continue
            if speaker:
                visual_segments.append(f"{speaker}口型轻动，但画面中不出现任何文字")
            continue
        visual_segments.append(segment)

    if not visual_segments:
        if extra_subject_names:
            return f"{character_name}与{', '.join(extra_subject_names)}稳定入镜"
        return f"{character_name}稳定入镜"

    return "，".join(visual_segments)


def _compose_shot_design_text(
    *,
    shot_size: str = "",
    camera_angle: str = "",
    camera_focus: str = "",
    cut_reason: str = "",
) -> str:
    parts: list[str] = []
    if shot_size:
        parts.append(shot_size)
    if camera_angle:
        parts.append(camera_angle)
    if camera_focus:
        parts.append(f"重点表现{camera_focus}")
    if cut_reason:
        parts.append(f"切换目的：{cut_reason}")
    return "，".join(parts)


def _build_continuity_reference_text(
    *,
    continuity_reference_enabled: bool,
    continuity_note: str = "",
) -> str:
    if not continuity_reference_enabled:
        return ""
    if continuity_note:
        return f"以连续性母图为参考，延续上一镜头的身份、服装、动作收势和空间朝向；上一镜头摘要：{continuity_note}；"
    return "以连续性母图为参考，延续上一镜头的身份、服装、动作收势、行进方向和空间朝向；"


def _build_scene_variant_reference_prompt(
    *,
    scene_name: str,
    variant_intent: str,
    shot_size: str,
    camera_angle: str,
    scene_signature_text: str,
) -> str:
    composition_hint = "使用明显偏轴构图，不要正中对称的一消点画面。"
    normalized_angle = (camera_angle or "").strip()
    if any(keyword in normalized_angle for keyword in ("侧", "斜", "后")):
        composition_hint = (
            "机位必须真实切到异侧视角，使用偏轴构图，让道路透视从画面一侧向远处延伸，"
            "不要回到正中对称的一消点画面。"
        )
    return (
        f"主体：纯场景参考图，不出现人物、动物和车辆特写；"
        f"场景：{scene_name}；"
        f"目标：{variant_intent}；"
        f"镜头：固定机位，{shot_size or '中景'}，{camera_angle or '斜侧面'}；"
        f"建筑与空间特征：{scene_signature_text or f'严格保持{scene_name}的建筑风格、道路布局和空间朝向一致'}；"
        f"构图要求：{composition_hint}"
        "约束：这是同一地点的异视角场景参考图，只允许改变观察方向与构图，不允许改地点、不允许改建筑风格、"
        "不允许混入现代与古典两套建筑；"
        "建筑立面、道路走向、透视层次和稳定地标必须一致，禁止任何文字、字母、数字、logo、水印、招牌字、"
        "路牌字、门牌号、灯杆刻字、墙面标语、悬挂横幅、对话框和多余装饰图案；"
        "所有路灯、路牌、建筑立柱、墙面和地面都必须保持纯净，不出现可读标识，画面干净，适合作为后续人物镜头的场景参考。"
    )


def _build_continuity_requirements(
    sample: dict[str, str],
    *,
    character_asset: CatalogAssetSummary | None,
    scene_asset: CatalogAssetSummary | None,
) -> str:
    """为显式参考输入生成更贴近当前样例的连续性要求。"""

    if character_asset or scene_asset:
        parts: list[str] = []
        if character_asset:
            parts.append(f"保持{character_asset.display_name}服装、发型和视线方向一致")
        if scene_asset:
            parts.append(f"保持{scene_asset.display_name}空间朝向和镜头方位一致")
        return "，".join(parts)
    return sample["continuity_requirements"]


def _build_scene_anchor_prompt(
    *,
    character_name: str,
    scene_name: str,
    storyboard_text: str,
    extra_subject_names: Sequence[str] = (),
    shot_size: str = "",
    camera_angle: str = "",
    camera_focus: str = "",
    cut_reason: str = "",
    continuity_reference_enabled: bool = False,
    continuity_note: str = "",
) -> str:
    """为换场景首帧锚点图生成一版稳定、简洁的提示词。"""

    action_text = _build_visual_story_action_text(
        storyboard_text=storyboard_text,
        character_name=character_name,
        extra_subject_names=extra_subject_names,
    )
    shot_design_text = _compose_shot_design_text(
        shot_size=shot_size,
        camera_angle=camera_angle,
        camera_focus=camera_focus,
        cut_reason=cut_reason,
    )
    continuity_text = _build_continuity_reference_text(
        continuity_reference_enabled=continuity_reference_enabled,
        continuity_note=continuity_note,
    )
    extra_subject_text = f"附加主体：{', '.join(extra_subject_names)}；" if extra_subject_names else ""
    stability_text = (
        f"同时保持{', '.join(extra_subject_names)}的外形、数量和空间关系稳定，"
        if extra_subject_names
        else ""
    )
    companion_framing_text = (
        f"让{character_name}与{', '.join(extra_subject_names)}同时清楚入镜，主角脸部清晰可辨，附加主体完整可见，"
        if extra_subject_names
        else f"让{character_name}脸部清晰可辨，"
    )
    return (
        f"主体：{character_name}；"
        f"{extra_subject_text}"
        f"场景：{scene_name}；"
        f"动作：{action_text}；"
        f"镜头：固定机位，{shot_design_text or '中近景，以主角上半身到膝上构图为主'}，避免远景；"
        f"约束：保持{character_name}的脸部、发型、服装一致，场景切换为{scene_name}，"
        f"必须以场景参考图中的道路布局、建筑朝向、透视关系和空间层次为绝对基准，"
        f"{continuity_text}"
        f"{stability_text}"
        f"{companion_framing_text}"
        "构图稳定，主体清晰，不新增人物，背景人物如必须出现只能远景弱化且不可抢画面，适合作为视频首帧，"
        "画面中禁止出现任何文字、字幕、logo、水印、路牌字、招牌字、对话框、气泡、内心独白气泡和多余界面元素，"
        "背景墙面干净，不要出现文字或装饰图案。"
    )


def _build_scene_anchor_review_summary(
    *,
    action: str,
    review_summary: str,
    prompt_patch: str,
) -> str:
    """组织图审后的中文摘要。"""

    parts = [f"图审结果：{action}"]
    if review_summary:
        parts.append(review_summary)
    if prompt_patch:
        parts.append(f"建议补丁：{prompt_patch}")
    return "；".join(parts)


def _build_manju_video_prompt(
    *,
    character_name: str,
    scene_name: str,
    storyboard_text: str,
    extra_subject_names: Sequence[str] = (),
    shot_size: str = "",
    camera_angle: str = "",
    camera_focus: str = "",
    cut_reason: str = "",
    continuity_reference_enabled: bool = False,
    continuity_note: str = "",
) -> str:
    """为 Manju 首帧模式生成一版简短、固定镜头的视频提示词。"""

    action_text = _build_visual_story_action_text(
        storyboard_text=storyboard_text,
        character_name=character_name,
        extra_subject_names=extra_subject_names,
    )
    shot_design_text = _compose_shot_design_text(
        shot_size=shot_size,
        camera_angle=camera_angle,
        camera_focus=camera_focus,
        cut_reason=cut_reason,
    )
    continuity_text = _build_continuity_reference_text(
        continuity_reference_enabled=continuity_reference_enabled,
        continuity_note=continuity_note,
    )
    extra_subject_text = f"附加主体：{', '.join(extra_subject_names)}；" if extra_subject_names else ""
    stability_text = (
        f"保持{', '.join(extra_subject_names)}外形稳定、数量正确、位置关系连续，不畸形、不穿模、不中途消失；"
        if extra_subject_names
        else ""
    )
    framing_text = (
        f"使用固定机位的{shot_size or '中近景'}，让{character_name}脸部在画面中清晰可辨，{', '.join(extra_subject_names)}完整入镜并与主角保持稳定距离；"
        if extra_subject_names
        else f"使用固定机位的{shot_size or '中近景'}，让{character_name}脸部在画面中清晰可辨；"
    )
    return (
        f"主体：{character_name}；"
        f"{extra_subject_text}"
        f"场景：{scene_name}；"
        f"动作：{action_text}；"
        f"镜头：固定机位，{shot_design_text or '中近景'}，避免远景小人；"
        "约束：保持固定机位，构图稳定，不运镜，不切换景别，保持人物脸部、发型、服装和场景结构一致，不新增人物，"
        f"{continuity_text}"
        f"{framing_text}"
        f"{stability_text}"
        "如果背景路人出现，只能作为远景弱化轮廓，不允许中景清晰露脸但面部崩坏；"
        "画面中禁止出现任何文字、字幕、logo、水印、路牌字、招牌字、对话框、气泡、内心独白气泡和多余界面元素，背景墙面干净。"
    )


def _estimate_manju_duration_seconds(storyboard_text: str) -> int:
    """根据分镜摘要估算更稳的 Manju 时长。"""

    text = storyboard_text.strip()
    if not text:
        return 4

    locked_reaction_tokens = (
        "固定中景",
        "固定机位",
        "近景",
        "特写",
        "表情",
        "眼神",
        "对白",
        "内心OS",
        "内心os",
        "看向",
        "盯着",
        "站定",
        "停住",
        "停下",
        "点头",
        "摇头",
        "嘴角",
        "歪了歪头",
    )
    continuity_tokens = (
        "继续",
        "承接",
        "接上",
        "延续",
        "收势",
        "收回拳头",
        "缓缓",
        "轻轻",
        "抬眼",
        "凑了过去",
        "伸出一根手指",
    )
    entry_or_establish_tokens = (
        "进入",
        "来到",
        "走进",
        "走出",
        "进门",
        "出门",
        "路上",
        "街角",
        "眼前的一幕",
        "场景",
        "街道",
        "教室",
        "走廊",
        "操场",
        "广场",
        "报名处",
        "时光屋",
        "车里",
        "车外",
    )
    crowd_tokens = ("众人", "多人", "几个", "同学们", "围观", "路人", "群像", "两人", "双人", "少女")
    dynamic_tokens = (
        "奔跑",
        "跑去",
        "冲",
        "砸下",
        "挥拳",
        "抬手",
        "指向",
        "指着",
        "拉了出来",
        "拉出来",
        "转身",
        "起身",
        "走出去",
        "拍打车窗",
        "脱下",
        "伸手",
    )
    medium_action_tokens = ("收回拳头", "指向", "指着", "抬手", "伸出一根手指")
    disaster_tokens = ("爆炸", "浓烟", "大火", "救人", "碎裂", "撞得面目全非", "火焰", "燃起")

    score = 4

    if any(token in text for token in locked_reaction_tokens):
        score -= 1
    if any(token in text for token in continuity_tokens):
        score -= 1

    if any(token in text for token in entry_or_establish_tokens):
        score += 1
    if any(token in text for token in crowd_tokens):
        score += 1
    if any(token in text for token in dynamic_tokens):
        score += 1
    if any(token in text for token in medium_action_tokens):
        score += 1
    if any(token in text for token in disaster_tokens):
        score += 2

    if any(token in text for token in continuity_tokens) and any(token in text for token in dynamic_tokens):
        score += 1

    has_dialogue = "：" in text or ":" in text
    if has_dialogue and not any(token in text for token in dynamic_tokens + disaster_tokens):
        score -= 1

    punctuation_density = sum(text.count(marker) for marker in ("，", "。", "！", "？", "；"))
    if punctuation_density >= 5 and score >= 6 and not any(token in text for token in disaster_tokens):
        score -= 1

    return max(4, min(score, 7))


def _run_manju_one_shot_script(
    *,
    project_root: Path,
    image_path: Path,
    prompt: str,
    output_path: Path,
    mode: str,
    resolution: str,
    duration_seconds: int,
    aspect_ratio: str,
    model_name: str,
    project_url: str = "",
    profile_dir: Path | None = None,
    headless: bool = True,
) -> subprocess.CompletedProcess[str]:
    """调用已经跑稳的 Manju 单任务脚本。"""

    script_path = project_root / "scripts" / "manju_one_shot.py"
    if not script_path.exists():
        raise typer.BadParameter(f"找不到 Manju 脚本: {script_path}")

    prompt_file = project_root / ".runtime" / "manju-prompts" / "latest_prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    script_mode = {"普通模式": "normal", "草稿模式": "draft"}.get(mode, mode)

    command = [
        sys.executable,
        str(script_path),
        "--image-path",
        str(image_path),
        "--prompt-file",
        str(prompt_file),
        "--output-path",
        str(output_path),
        "--mode",
        script_mode,
        "--resolution",
        resolution,
        "--duration-seconds",
        str(duration_seconds),
        "--aspect-ratio",
        aspect_ratio,
        "--model-name",
        model_name,
    ]
    if project_url:
        command.extend(["--project-url", project_url])
    if profile_dir is not None:
        command.extend(["--profile-dir", str(profile_dir)])
    if not headless:
        command.append("--headed")
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(project_root),
        env=child_env,
        check=False,
    )


def _build_default_openclaw_outputs(project_root: Path, *, template: str = "continuity_first"):
    """基于默认样例构建单镜头所需的 planner / composer 结果。"""

    sample = _default_openclaw_sample()
    catalog_path = _resolve_catalog_path(project_root)
    client = OpenClawClient()

    planner_request = client.build_asset_planner_request_from_catalog(
        storyboard_id=sample["storyboard_id"],
        storyboard_text=sample["storyboard_text"],
        style_summary=sample["style_summary"],
        catalog_path=catalog_path,
    )
    planner_response = client.run_asset_planner(planner_request)
    composer_response = client.run_prompt_composer(
        PromptComposerRequest(
            storyboard_id=sample["storyboard_id"],
            storyboard_text=sample["storyboard_text"],
            style_summary=sample["style_summary"],
            selected_assets=planner_response.selected_assets,
            prompt_template=template,
            previous_frame_summary=sample["previous_frame_summary"],
            continuity_requirements=sample["continuity_requirements"],
        )
    )
    reference_file_paths = _resolve_reference_files_from_catalog(catalog_path, planner_response.selected_assets)
    return sample, planner_response, composer_response, reference_file_paths


def _default_two_shot_samples() -> tuple[dict[str, str], dict[str, str]]:
    """返回两镜头最小闭环的默认分镜样例。"""

    shot_1 = {
        "storyboard_id": "demo_shot_001",
        "storyboard_text": "周浩天暴怒拍桌，扬起拳头就要砸向林白，教室气氛紧绷。",
        "style_summary": "国风校园玄幻，电影感，人物关系清晰，画面稳定。",
        "current_shot_summary": "周浩天暴怒逼近林白，拳头扬起，冲突即将爆发。",
        "next_shot_summary": (
            "广播突然响起打断冲突，陈夏娜拉住周浩天的胳膊。"
            "周浩天收回拳头，恶狠狠地指着林白放狠话。"
        ),
    }
    shot_2 = {
        "storyboard_id": "demo_shot_002",
        "storyboard_text": (
            "周浩天收回拳头，恶狠狠地指着林白放狠话。"
            "随后他张狂大笑，跟班哄笑，陈夏娜鄙夷地补刀。"
        ),
        "style_summary": "国风校园玄幻，连续性优先，人物状态稳定，对白镜头清晰。",
        "continuity_requirements": (
            "优先承接上一镜头筛选出的最佳承接帧，保持周浩天、林白、陈夏娜的人物关系、"
            "教室空间朝向和冲突后的收势状态一致。"
        ),
    }
    return shot_1, shot_2


def _build_shot_openclaw_outputs(
    *,
    project_root: Path,
    shot_sample: dict[str, str],
    template: str,
    previous_frame_summary: str = "",
    continuity_requirements: str = "",
    continuity_anchor: str = "@TransitionFrame",
):
    """为单个镜头构建 planner / composer 输出与本地参考图。"""

    catalog_path = _resolve_catalog_path(project_root)
    client = OpenClawClient()
    planner_request = client.build_asset_planner_request_from_catalog(
        storyboard_id=shot_sample["storyboard_id"],
        storyboard_text=shot_sample["storyboard_text"],
        style_summary=shot_sample.get("style_summary", ""),
        catalog_path=catalog_path,
    )
    planner_response = client.run_asset_planner(planner_request)
    composer_response = client.run_prompt_composer(
        PromptComposerRequest(
            storyboard_id=shot_sample["storyboard_id"],
            shot_id=shot_sample["storyboard_id"],
            storyboard_text=shot_sample["storyboard_text"],
            style_summary=shot_sample.get("style_summary", ""),
            selected_assets=planner_response.selected_assets,
            prompt_template=template,
            continuity_anchor=continuity_anchor,
            previous_frame_summary=previous_frame_summary,
            continuity_requirements=continuity_requirements,
        )
    )
    reference_file_paths = _resolve_reference_files_from_catalog(catalog_path, planner_response.selected_assets)
    return planner_response, composer_response, reference_file_paths


def _prepend_transition_reference(
    reference_file_paths: Sequence[Path],
    transition_frame_path: Path,
) -> list[Path]:
    """把最佳承接帧放到参考图最前面，供 shot_2 优先上传。"""

    ordered_paths: list[Path] = [transition_frame_path]
    for path in reference_file_paths:
        if path not in ordered_paths:
            ordered_paths.append(path)
    return ordered_paths


def _finalize_download_with_optional_audit(
    *,
    operator: JimengWebOperator,
    result: object,
    shot_id: str,
    storyboard_text: str,
    prompt_main: str,
    prompt_negative: str,
    ref_assets_in_order: list[str],
    output_path: Path,
    report_dir: Path,
    audit_before_download: bool,
    auto_audit: bool,
) -> None:
    if not (audit_before_download or auto_audit) or not getattr(result, "ready_for_download", False):
        operator.close()
        return

    audit_report_path = report_dir / f"{shot_id}_audit.html"
    if auto_audit:
        review_video_dir = report_dir / shot_id
        review_video_dir.mkdir(parents=True, exist_ok=True)
        review_video_path = review_video_dir / f"{shot_id}_auto_audit.mp4"
        review_frames_dir = review_video_dir / "frames"
        if not operator.download_latest_video(review_video_path):
            result.failed_stage = "自动审查准备"
            result.messages.append("Gemini 自动审查前，临时下载待审视频失败。")
            operator.close()
            return

        try:
            decision = _run_gemini_auto_audit(
                shot_id=shot_id,
                storyboard_text=storyboard_text,
                prompt_main=prompt_main,
                prompt_negative=prompt_negative,
                ref_assets_in_order=ref_assets_in_order,
                report_path=audit_report_path,
                review_video_path=review_video_path,
                review_frames_dir=review_frames_dir,
            )
        except (GeminiAuditError, RuntimeError, typer.BadParameter) as exc:
            result.failed_stage = "Gemini 自动审查"
            result.messages.append(f"Gemini 自动审查失败: {exc}")
            operator.close()
            return
    else:
        decision = _run_download_audit(
            shot_id=shot_id,
            storyboard_text=storyboard_text,
            prompt_main=prompt_main,
            prompt_negative=prompt_negative,
            ref_assets_in_order=ref_assets_in_order,
            report_path=audit_report_path,
        )

    result.audit_report_path = str(audit_report_path)
    result.audit_action = decision.action
    result.audit_summary = decision.review_summary
    result.prompt_patch = decision.prompt_patch
    result.revised_prompt_main = decision.revised_prompt_main

    if decision.action == "approve":
        if auto_audit:
            review_video_path = report_dir / shot_id / f"{shot_id}_auto_audit.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(review_video_path, output_path)
            result.download_succeeded = output_path.exists()
        else:
            result.download_succeeded = operator.download_latest_video(output_path)
        result.download_path = str(output_path.resolve()) if result.download_succeeded else ""
        if not result.download_succeeded:
            result.failed_stage = "下载视频"
            result.messages.append("审计已通过，但下载视频失败。")
    elif decision.action == "revise":
        result.failed_stage = "审计未通过"
        if auto_audit:
            result.messages.append("Gemini 自动审查判定需要小幅修正，已生成补丁，未执行下载。")
        else:
            result.messages.append("人工审计未通过，已生成小幅修正提示词，未执行下载。")
    else:
        result.failed_stage = "自动拒绝下载" if auto_audit else "人工拒绝下载"
        if auto_audit:
            result.messages.append("Gemini 自动审查拒绝下载，当前结果保留待人工复核。")
        else:
            result.messages.append("人工审计拒绝下载，当前任务已停止。")
    operator.close()


@app.command()
def doctor() -> None:
    """检查基础配置、模块导入与数据库 URL 解析。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    engine = build_engine(config.database_url)
    report = {
        "project_root": str(config.project_root),
        "database_url": config.database_url,
        "engine": str(engine.url),
        "status": "ok",
    }
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))


@app.command()
def run(script_path: Optional[Path] = typer.Option(None, help="待处理剧本路径，占位参数。")) -> None:
    """运行占位版 Orchestrator。"""

    _configure_logging()
    _load_dotenv()
    orchestrator = Orchestrator()
    result = orchestrator.run(str(script_path) if script_path else None)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))


@app.command("split-script")
def split_script_command(
    script_path: Optional[Path] = typer.Option(None, "--script-path", help="剧本文本文件路径。"),
    script_text: str = typer.Option("", "--script-text", help="直接传入剧本文本。"),
    character_ref: str = typer.Option("", "--character-ref", help="可选：默认角色参考。"),
    scene_ref: str = typer.Option("", "--scene-ref", help="可选：默认场景参考。"),
    shot_prefix: str = typer.Option("scene", "--shot-prefix", help="输出镜头 ID 前缀。"),
    max_chars_per_shot: int = typer.Option(80, "--max-chars-per-shot", min=30, help="单镜头最大字数。"),
    max_units_per_shot: int = typer.Option(2, "--max-units-per-shot", min=1, max=4, help="单镜头最多合并几个动作单元。"),
    output_path: Optional[Path] = typer.Option(None, "--output-path", help="输出 JSON 路径。"),
) -> None:
    _configure_logging()
    _load_dotenv()
    config = get_config()
    raw_text = script_text.strip()
    if script_path:
        raw_text = script_path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise typer.BadParameter("请提供 --script-path 或 --script-text。")

    service = ScriptSplitterService()
    result = service.split_script(
        ScriptSplitRequest(
            script_text=raw_text,
            character_ref=character_ref,
            scene_ref=scene_ref,
            shot_prefix=shot_prefix,
            max_chars_per_shot=max_chars_per_shot,
            max_units_per_shot=max_units_per_shot,
        )
    )
    payload = service.to_payload(result)
    resolved_output_path = output_path or (config.project_root / "tmp" / f"{shot_prefix}_shots.json")
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    typer.echo("剧本切分结果")
    typer.echo(f"- shot_count: {len(result.shots)}")
    typer.echo(f"- output_path: {resolved_output_path}")
    typer.echo(f"- character_ref: {character_ref or '未指定'}")
    typer.echo(f"- scene_ref: {scene_ref or '未指定'}")
    typer.echo(f"- max_chars_per_shot: {max_chars_per_shot}")
    typer.echo(f"- max_units_per_shot: {max_units_per_shot}")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("build-asset-catalog")
def build_asset_catalog_command(assets_dir: Optional[Path] = typer.Option(None, help="素材目录，默认使用项目 assets 目录。")) -> None:
    """扫描 assets 目录并构建 catalog.json。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    resolved_assets_dir = assets_dir or (config.project_root / "assets")
    service = AssetCatalogService()
    result = service.build_catalog(resolved_assets_dir)
    summary = {
        "total_assets": result.total_assets,
        "type_counts": result.type_counts,
        "catalog_path": result.catalog_path,
    }
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command("analyze-one-shot")
def analyze_one_shot(
    video: Path = typer.Option(..., "--video", help="待分析的视频路径。"),
    next_shot: str = typer.Option(..., "--next-shot", help="下一镜头摘要，会作为承接评分的核心输入。"),
    current_shot: str = typer.Option("", "--current-shot", help="当前镜头摘要，可选。"),
) -> None:
    """分析单镜头视频，选出最适合作为下一镜头承接的最佳帧。"""

    _configure_logging()
    _load_dotenv()
    if not video.exists():
        raise typer.BadParameter(f"找不到视频文件: {video}")

    service = VideoAnalyzerService()
    result = service.analyze_one_shot(
        str(video),
        current_shot_summary=current_shot,
        next_shot_summary=next_shot,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))


@app.command("test-asset-planner")
def test_asset_planner(sample_path: Optional[Path] = typer.Option(None, help="可选的分镜输入 JSON 文件路径。")) -> None:
    """本地测试 AssetPlanner。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    sample = _load_openclaw_sample(sample_path)
    catalog_path = _resolve_catalog_path(config.project_root)
    client = OpenClawClient()

    request = client.build_asset_planner_request_from_catalog(
        storyboard_id=sample["storyboard_id"],
        storyboard_text=sample["storyboard_text"],
        style_summary=sample["style_summary"],
        catalog_path=catalog_path,
    )
    response = client.run_asset_planner(request)

    typer.echo("AssetPlanner 本地测试结果")
    typer.echo(f"- 分镜 ID: {response.storyboard_id}")
    typer.echo(f"- reference_assets: {', '.join(response.reference_assets) if response.reference_assets else '无'}")
    typer.echo(f"- reference_strategy: {response.reference_strategy or '无'}")
    typer.echo(f"- must_keep: {', '.join(response.must_keep) if response.must_keep else '无'}")
    typer.echo(f"- drop_if_needed: {', '.join(response.drop_if_needed) if response.drop_if_needed else '无'}")


@app.command("test-prompt-composer")
def test_prompt_composer(
    sample_path: Optional[Path] = typer.Option(None, help="可选的分镜输入 JSON 文件路径。"),
    template: str = typer.Option(
        "default",
        "--template",
        help="PromptComposer 模板，可选值：default、cinematic、continuity_first、action_scene、character_focus。",
    ),
    previous_frame_summary: Optional[str] = typer.Option(
        None,
        "--previous-frame-summary",
        help="显式覆盖上一镜头静帧摘要，用于更像真实续接场景地验词。",
    ),
    character_ref: Optional[str] = typer.Option(
        None,
        "--character-ref",
        help="显式指定角色主参考，支持 asset_id、jimeng_ref_name 或 display_name。",
    ),
    scene_ref: Optional[str] = typer.Option(
        None,
        "--scene-ref",
        help="显式指定场景参考，支持 asset_id、jimeng_ref_name 或 display_name。",
    ),
) -> None:
    """本地测试 PromptComposer。"""

    _configure_logging()
    _load_dotenv()
    if template not in get_prompt_template_names():
        raise typer.BadParameter(f"不支持的模板: {template}。可选值：{', '.join(get_prompt_template_names())}")

    config = get_config()
    sample = _load_openclaw_sample(sample_path)
    catalog_path = _resolve_catalog_path(config.project_root)
    client = OpenClawClient()

    planner_request = client.build_asset_planner_request_from_catalog(
        storyboard_id=sample["storyboard_id"],
        storyboard_text=sample["storyboard_text"],
        style_summary=sample["style_summary"],
        catalog_path=catalog_path,
    )
    planner_response = client.run_asset_planner(planner_request)
    selected_assets: list[CatalogAssetSummary] = list(planner_response.selected_assets)
    resolved_character: CatalogAssetSummary | None = None
    resolved_scene: CatalogAssetSummary | None = None

    if character_ref:
        resolved_character = _catalog_asset_to_summary(_find_catalog_asset(catalog_path, character_ref, "character"))
        selected_assets = [asset for asset in selected_assets if asset.type != "character"]
        selected_assets.insert(0, resolved_character)

    if scene_ref:
        resolved_scene = _catalog_asset_to_summary(_find_catalog_asset(catalog_path, scene_ref, "scene"))
        selected_assets = [asset for asset in selected_assets if asset.type != "scene"]
        selected_assets.append(resolved_scene)

    effective_previous_frame_summary = previous_frame_summary or sample["previous_frame_summary"]
    effective_continuity_requirements = _build_continuity_requirements(
        sample,
        character_asset=resolved_character,
        scene_asset=resolved_scene,
    )
    composer_response = client.run_prompt_composer(
        PromptComposerRequest(
            storyboard_id=sample["storyboard_id"],
            storyboard_text=sample["storyboard_text"],
            style_summary=sample["style_summary"],
            selected_assets=selected_assets,
            prompt_template=template,
            previous_frame_summary=effective_previous_frame_summary,
            continuity_requirements=effective_continuity_requirements,
        )
    )

    typer.echo("PromptComposer 本地测试结果")
    typer.echo(f"- 分镜 ID: {composer_response.storyboard_id}")
    typer.echo(f"- template: {template}")
    typer.echo(f"- previous_frame_summary: {effective_previous_frame_summary or '无'}")
    typer.echo(f"- character_ref: {character_ref or '自动选择'}")
    typer.echo(f"- scene_ref: {scene_ref or '自动选择'}")
    typer.echo(f"- continuity_requirements: {effective_continuity_requirements or '无'}")
    typer.echo(f"- shot_id: {composer_response.shot_id}")
    typer.echo(f"- prompt_main: {composer_response.prompt_main}")
    typer.echo(f"- prompt_negative: {composer_response.prompt_negative}")
    typer.echo(
        f"- ref_assets_in_order: {', '.join(composer_response.ref_assets_in_order) if composer_response.ref_assets_in_order else '无'}"
    )
    typer.echo(f"- continuity_notes: {composer_response.continuity_notes}")


@app.command("generate-scene-anchor")
def generate_scene_anchor(
    character_ref: str = typer.Option(..., "--character-ref", help="角色参考，可传 display_name、asset_id 或 jimeng_ref_name。"),
    scene_ref: str = typer.Option(..., "--scene-ref", help="场景参考，可传 display_name、asset_id 或 jimeng_ref_name。"),
    scene_variant_ref_image: list[Path] = typer.Option(
        [],
        "--scene-variant-ref-image",
        help="可选：同场景异视角参考图，可多次传入，会与主场景参考一起用于出图。",
    ),
    storyboard_text: str = typer.Option("", "--storyboard-text", help="换场景镜头摘要，可选。"),
    continuity_ref_image: Optional[Path] = typer.Option(
        None,
        "--continuity-ref-image",
        help="可选：上一镜头母图/承接帧，只用于连续性参考，会重新生成新视角首帧图。",
    ),
    continuity_note: str = typer.Option("", "--continuity-note", help="可选：上一镜头连续性说明。"),
    shot_size: str = typer.Option("", "--shot-size", help="可选：景别设计，例如 中景/中近景/近景。"),
    camera_angle: str = typer.Option("", "--camera-angle", help="可选：机位角度，例如 三分之二前侧/正侧面。"),
    camera_focus: str = typer.Option("", "--camera-focus", help="可选：镜头重点，例如 人物与空间关系同时可读。"),
    cut_reason: str = typer.Option("", "--cut-reason", help="可选：切换目的，例如 动作延续换角度。"),
    prompt: str = typer.Option("", "--prompt", help="自定义出图提示词；不传时自动生成一版稳定提示词。"),
    aspect_ratio: str = typer.Option("16:9", "--aspect-ratio", help="图片宽高比，默认 16:9。"),
    model_name: str = typer.Option("nano-banana-2", "--model", help="第三方图片模型名称。"),
    auto_review: bool = typer.Option(
        False,
        "--auto-review/--no-auto-review",
        help="生成锚点图后自动执行图审；只有 approve 才建议进入视频生成。",
    ),
    output_path: Optional[Path] = typer.Option(None, "--output-path", help="输出图片路径，默认保存到 outputs/images/。"),
) -> None:
    """用人物图 + 场景图生成换场景首帧锚点图。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    catalog_path = _resolve_catalog_path(config.project_root)

    character_asset, character_image_path = _resolve_catalog_asset_image(catalog_path, character_ref, "character")
    scene_asset, scene_image_path = _resolve_catalog_asset_image(catalog_path, scene_ref, "scene")
    resolved_scene_variant_refs = _resolve_existing_image_paths(scene_variant_ref_image)
    resolved_continuity_ref = continuity_ref_image.resolve() if continuity_ref_image else None
    if resolved_continuity_ref is not None and not resolved_continuity_ref.exists():
        raise typer.BadParameter(f"找不到连续性参考图：{resolved_continuity_ref}")
    effective_prompt = prompt or _build_scene_anchor_prompt(
        character_name=character_asset.display_name,
        scene_name=scene_asset.display_name,
        storyboard_text=storyboard_text,
        shot_size=shot_size,
        camera_angle=camera_angle,
        camera_focus=camera_focus,
        cut_reason=cut_reason,
        continuity_reference_enabled=resolved_continuity_ref is not None,
        continuity_note=continuity_note,
    )
    service = OpenClawService()
    try:
        response = service.generate_scene_anchor_image(
            SceneAnchorImageRequest(
                shot_id=f"scene-anchor-{character_asset.display_name}-{scene_asset.display_name}",
                storyboard_text=storyboard_text,
                prompt=effective_prompt,
                character_reference_paths=[str(character_image_path)],
                scene_reference_paths=[str(scene_image_path), *[str(path) for path in resolved_scene_variant_refs]],
                continuity_reference_paths=[str(resolved_continuity_ref)] if resolved_continuity_ref else [],
                model_name=model_name,
                aspect_ratio=aspect_ratio,
                output_path=str(output_path) if output_path else None,
            ),
            project_root=config.project_root,
        )
    except SceneAnchorImageError as exc:
        typer.echo("scene_anchor_image 生成失败：", err=True)
        typer.echo(str(exc), err=True)
        if exc.url:
            typer.echo(f"接口地址: {exc.url}", err=True)
        if exc.status_code is not None:
            typer.echo(f"HTTP 状态码: {exc.status_code}", err=True)
        if exc.response_body:
            typer.echo(exc.response_body, err=True)
        raise typer.Exit(code=1)

    typer.echo("Scene Anchor Image 生成结果")
    typer.echo(f"- shot_id: {response.shot_id}")
    typer.echo(f"- character_ref: {character_asset.display_name}")
    typer.echo(f"- scene_ref: {scene_asset.display_name}")
    typer.echo(f"- prompt: {response.prompt}")
    typer.echo(f"- model: {response.model_name}")
    typer.echo(f"- aspect_ratio: {response.aspect_ratio}")
    typer.echo(f"- review_status: {response.review_status}")
    typer.echo(f"- output_path: {response.output_path}")
    typer.echo(
        f"- source_images: {', '.join(response.source_images) if response.source_images else '无'}"
    )
    if not auto_review:
        return

    try:
        review_response = service.review_scene_anchor_image(
            SceneAnchorReviewRequest(
                shot_id=response.shot_id,
                storyboard_text=storyboard_text,
                prompt=response.prompt,
                image_path=response.output_path,
                character_name=character_asset.display_name,
                scene_name=scene_asset.display_name,
                source_images=response.source_images,
            )
        )
    except SceneAnchorReviewError as exc:
        typer.echo("scene_anchor_image 图审失败：", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    typer.echo("- auto_review:")
    typer.echo(f"  - action: {review_response.action}")
    typer.echo(f"  - review_summary: {_build_scene_anchor_review_summary(action=review_response.action, review_summary=review_response.review_summary, prompt_patch=review_response.prompt_patch)}")
    typer.echo(
        f"  - selected_issue_ids: {', '.join(review_response.selected_issue_ids) if review_response.selected_issue_ids else '无'}"
    )
    typer.echo(f"  - prompt_patch: {review_response.prompt_patch or '无'}")
    typer.echo(f"  - revised_prompt: {review_response.revised_prompt or '无'}")


@app.command("generate-scene-variant-reference")
def generate_scene_variant_reference(
    scene_ref: str = typer.Option(..., "--scene-ref", help="场景参考，可传 display_name、asset_id 或 jimeng_ref_name。"),
    continuity_ref_image: Optional[Path] = typer.Option(
        None,
        "--continuity-ref-image",
        help="可选：上一镜头母图/承接帧，用于补充同场景其他视角的空间线索。",
    ),
    continuity_note: str = typer.Option("", "--continuity-note", help="可选：上一镜头连续性说明。"),
    variant_intent: str = typer.Option(
        "生成同一场景的另一视角纯场景参考图",
        "--variant-intent",
        help="说明这张参考图要服务什么视角切换。",
    ),
    shot_size: str = typer.Option("中景", "--shot-size", help="目标参考图景别。"),
    camera_angle: str = typer.Option("斜侧面", "--camera-angle", help="目标参考图角度。"),
    prompt: str = typer.Option("", "--prompt", help="自定义提示词；不传则基于提取的建筑特征自动生成。"),
    aspect_ratio: str = typer.Option("16:9", "--aspect-ratio", help="图片宽高比，默认 16:9。"),
    model_name: str = typer.Option("nano-banana-2", "--model", help="第三方图片模型名称。"),
    output_path: Optional[Path] = typer.Option(None, "--output-path", help="输出图片路径。"),
) -> None:
    """从场景参考图/母图提取建筑特征，生成同一场景异视角的纯场景参考图。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    catalog_path = _resolve_catalog_path(config.project_root)
    scene_asset, scene_image_path = _resolve_catalog_asset_image(catalog_path, scene_ref, "scene")
    resolved_continuity_ref = continuity_ref_image.resolve() if continuity_ref_image else None
    if resolved_continuity_ref is not None and not resolved_continuity_ref.exists():
        raise typer.BadParameter(f"找不到连续性参考图：{resolved_continuity_ref}")

    service = OpenClawService()
    try:
        feature_response = service.extract_scene_features(
            SceneFeatureExtractionRequest(
                scene_name=scene_asset.display_name,
                image_paths=[
                    str(scene_image_path),
                    *([str(resolved_continuity_ref)] if resolved_continuity_ref else []),
                ],
                continuity_note=continuity_note,
            )
        )
    except SceneFeatureExtractionError as exc:
        typer.echo("scene feature extraction 失败：", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    effective_prompt = prompt or _build_scene_variant_reference_prompt(
        scene_name=scene_asset.display_name,
        variant_intent=variant_intent,
        shot_size=shot_size,
        camera_angle=camera_angle,
        scene_signature_text=feature_response.scene_signature_text,
    )
    try:
        response = service.generate_scene_anchor_image(
            SceneAnchorImageRequest(
                shot_id=f"scene-variant-{scene_asset.display_name}",
                storyboard_text=variant_intent,
                prompt=effective_prompt,
                scene_reference_paths=[str(scene_image_path)],
                continuity_reference_paths=[str(resolved_continuity_ref)] if resolved_continuity_ref else [],
                model_name=model_name,
                aspect_ratio=aspect_ratio,
                output_path=str(output_path) if output_path else None,
            ),
            project_root=config.project_root,
        )
    except SceneAnchorImageError as exc:
        typer.echo("scene variant reference 生成失败：", err=True)
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    typer.echo("Scene Variant Reference 生成结果")
    typer.echo(f"- scene_ref: {scene_asset.display_name}")
    typer.echo(f"- architecture_style: {feature_response.architecture_style or '无'}")
    typer.echo(f"- layout_summary: {feature_response.layout_summary or '无'}")
    typer.echo(f"- anchor_landmarks: {', '.join(feature_response.anchor_landmarks) if feature_response.anchor_landmarks else '无'}")
    typer.echo(f"- preserved_elements: {', '.join(feature_response.preserved_elements) if feature_response.preserved_elements else '无'}")
    typer.echo(f"- forbidden_elements: {', '.join(feature_response.forbidden_elements) if feature_response.forbidden_elements else '无'}")
    typer.echo(f"- camera_guidance: {feature_response.camera_guidance or '无'}")
    typer.echo(f"- scene_signature_text: {feature_response.scene_signature_text or '无'}")
    typer.echo(f"- prompt: {response.prompt}")
    typer.echo(f"- output_path: {response.output_path}")


@app.command("run-manju-scene-shot")
def run_manju_scene_shot(
    storyboard_id: str = typer.Option("", "--storyboard-id", help="可选：当前镜头 ID；不传时按角色和场景自动生成。"),
    character_ref: str = typer.Option(..., "--character-ref", help="角色参考，可传 display_name、asset_id 或 jimeng_ref_name。"),
    scene_ref: str = typer.Option(..., "--scene-ref", help="场景参考，可传 display_name、asset_id 或 jimeng_ref_name。"),
    scene_variant_ref_image: list[Path] = typer.Option(
        [],
        "--scene-variant-ref-image",
        help="可选：同场景异视角参考图，可多次传入，会与主场景参考一起用于当前镜头首帧生成。",
    ),
    pet_ref: list[str] = typer.Option([], "--pet-ref", help="可选：额外主体/宠物参考；可多次传入，支持本地图片路径或 assets/extras 下名称匹配。"),
    storyboard_text: str = typer.Option(..., "--storyboard-text", help="当前镜头摘要，会同时用于出图和视频提示词。"),
    continuity_ref_image: Optional[Path] = typer.Option(
        None,
        "--continuity-ref-image",
        help="可选：上一镜头母图/承接帧，只用于连续性参考，会重新生成当前镜头首帧图。",
    ),
    continuity_note: str = typer.Option("", "--continuity-note", help="可选：上一镜头连续性说明。"),
    shot_size: str = typer.Option("", "--shot-size", help="可选：景别设计，例如 中景/中近景/近景。"),
    camera_angle: str = typer.Option("", "--camera-angle", help="可选：机位角度，例如 三分之二前侧/正侧面。"),
    camera_focus: str = typer.Option("", "--camera-focus", help="可选：镜头重点，例如 人物与空间关系同时可读。"),
    cut_reason: str = typer.Option("", "--cut-reason", help="可选：切换目的，例如 动作延续换角度。"),
    anchor_prompt: str = typer.Option("", "--anchor-prompt", help="自定义锚点图提示词；不传时自动生成。"),
    video_prompt: str = typer.Option("", "--video-prompt", help="自定义 Manju 视频提示词；不传时自动生成。"),
    aspect_ratio: str = typer.Option("16:9", "--aspect-ratio", help="锚点图宽高比，默认 16:9。"),
    model_name: str = typer.Option("nano-banana-2", "--model", help="第三方图片模型名称。"),
    duration_seconds: int = typer.Option(
        0,
        "--duration-seconds",
        help="Manju 视频时长；传 0 表示根据分镜摘要自动估算 4s/5s/6s/7s。",
    ),
    manju_mode: str = typer.Option("普通模式", "--manju-mode", help="Manju 模式，默认 普通模式。"),
    manju_resolution: str = typer.Option("1080p", "--manju-resolution", help="Manju 清晰度，默认 1080p。"),
    manju_model_name: str = typer.Option("Seedance1.5-pro", "--manju-model-name", help="Manju 模型名。"),
    manju_profile_dir: Optional[Path] = typer.Option(None, "--manju-profile-dir", help="可选：指向已登录的 Manju Chrome profile 目录。"),
    manju_project_url: str = typer.Option("", "--manju-project-url", help="可选：要操作的 Manju 项目 URL。"),
    manju_headless: bool = typer.Option(True, "--manju-headless/--manju-headed", help="是否使用无头浏览器，需要人工登录时可用 --manju-headed。"),
    input_anchor_image_path: Optional[Path] = typer.Option(
        None,
        "--input-anchor-image",
        help="可选：直接复用已有首帧图；传入后会跳过锚点图生成和图审，直接进入 Manju 图生视频。",
    ),
    force_regenerate_anchor: bool = typer.Option(
        False,
        "--force-regenerate-anchor/--allow-existing-anchor",
        help="即使传入了 input_anchor_image，也强制重新生成并图审首帧图。",
    ),
    anchor_output_path: Optional[Path] = typer.Option(None, "--anchor-output-path", help="锚点图输出路径。"),
    video_output_path: Optional[Path] = typer.Option(None, "--video-output-path", help="视频输出路径。"),
) -> None:
    """生成场景锚点图并通过图审后，调用 Manju 生成单镜头视频。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    catalog_path = _resolve_catalog_path(config.project_root)
    character_asset, character_image_path = _resolve_catalog_asset_image(catalog_path, character_ref, "character")
    scene_asset, scene_image_path = _resolve_catalog_asset_image(catalog_path, scene_ref, "scene")
    resolved_scene_variant_refs = _resolve_existing_image_paths(scene_variant_ref_image)
    extra_reference_pairs = _resolve_extra_reference_images(config.project_root, pet_ref)
    extra_subject_names = [name for name, _ in extra_reference_pairs]
    extra_reference_paths = [str(path) for _, path in extra_reference_pairs]
    resolved_continuity_ref = continuity_ref_image.resolve() if continuity_ref_image else None
    if resolved_continuity_ref is not None and not resolved_continuity_ref.exists():
        raise typer.BadParameter(f"找不到连续性参考图：{resolved_continuity_ref}")
    shot_id = storyboard_id.strip() or f"manju-scene-{character_asset.display_name}-{scene_asset.display_name}"

    service = OpenClawService()
    anchor_response = None
    review_response = None
    if input_anchor_image_path is not None and not force_regenerate_anchor:
        resolved_input_anchor = input_anchor_image_path.resolve()
        if not resolved_input_anchor.exists():
            raise typer.BadParameter(f"找不到输入首帧图：{resolved_input_anchor}")
        anchor_response = SimpleNamespace(
            shot_id=shot_id,
            prompt="",
            output_path=str(resolved_input_anchor),
            source_images=[str(resolved_input_anchor)],
        )
        review_response = SimpleNamespace(
            action="skipped_existing_anchor",
            review_summary="已复用现有首帧图，跳过锚点图生成与图审。",
            selected_issue_ids=[],
            prompt_patch="",
            revised_prompt="",
        )
        typer.echo("Manju 场景首帧结果")
        typer.echo("- anchor_attempt: 0")
        typer.echo(f"- anchor_image_path: {resolved_input_anchor}")
        typer.echo("- anchor_source: existing")
        typer.echo("- anchor_review_action: skipped_existing_anchor")
        typer.echo("- anchor_review_summary: 已复用现有首帧图，跳过锚点图生成与图审。")
        typer.echo("- anchor_review_issues: 无")
        typer.echo("- anchor_prompt_patch: 无")
        typer.echo("- anchor_revised_prompt: 无")
    else:
        effective_anchor_prompt = anchor_prompt or _build_scene_anchor_prompt(
            character_name=character_asset.display_name,
            scene_name=scene_asset.display_name,
            storyboard_text=storyboard_text,
            extra_subject_names=extra_subject_names,
            shot_size=shot_size,
            camera_angle=camera_angle,
            camera_focus=camera_focus,
            cut_reason=cut_reason,
            continuity_reference_enabled=resolved_continuity_ref is not None,
            continuity_note=continuity_note,
        )
        max_anchor_attempts = 3
        for anchor_attempt in range(1, max_anchor_attempts + 1):
            try:
                anchor_response = service.generate_scene_anchor_image(
                    SceneAnchorImageRequest(
                        shot_id=shot_id,
                        storyboard_text=storyboard_text,
                        prompt=effective_anchor_prompt,
                        character_reference_paths=[str(character_image_path)],
                        scene_reference_paths=[str(scene_image_path), *[str(path) for path in resolved_scene_variant_refs]],
                        extra_reference_paths=extra_reference_paths,
                        continuity_reference_paths=[str(resolved_continuity_ref)] if resolved_continuity_ref else [],
                        model_name=model_name,
                        aspect_ratio=aspect_ratio,
                        output_path=str(anchor_output_path) if anchor_output_path else None,
                    ),
                    project_root=config.project_root,
                )
                review_response = service.review_scene_anchor_image(
                    SceneAnchorReviewRequest(
                        shot_id=anchor_response.shot_id,
                        storyboard_text=storyboard_text,
                        prompt=anchor_response.prompt,
                        image_path=anchor_response.output_path,
                        character_name=character_asset.display_name,
                        scene_name=scene_asset.display_name,
                        source_images=anchor_response.source_images,
                    )
                )
            except (SceneAnchorImageError, SceneAnchorReviewError) as exc:
                typer.echo("Manju 场景首帧准备失败：", err=True)
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1)

            typer.echo("Manju 场景首帧结果")
            typer.echo(f"- anchor_attempt: {anchor_attempt}")
            typer.echo(f"- anchor_image_path: {anchor_response.output_path}")
            typer.echo("- anchor_source: generated")
            typer.echo(f"- anchor_review_action: {review_response.action}")
            typer.echo(f"- anchor_review_summary: {review_response.review_summary or '无'}")
            typer.echo(
                f"- anchor_review_issues: {', '.join(review_response.selected_issue_ids) if review_response.selected_issue_ids else '无'}"
            )
            typer.echo(f"- anchor_prompt_patch: {review_response.prompt_patch or '无'}")
            typer.echo(f"- anchor_revised_prompt: {review_response.revised_prompt or '无'}")

            if review_response.action == "approve":
                break
            if anchor_attempt < max_anchor_attempts:
                if review_response.action == "revise":
                    effective_anchor_prompt = review_response.revised_prompt or _apply_prompt_patch(
                        anchor_response.prompt,
                        review_response.prompt_patch,
                    )
                else:
                    effective_anchor_prompt = review_response.revised_prompt or anchor_response.prompt
                typer.echo("- anchor_retry: yes")
                typer.echo(f"- anchor_retry_prompt: {effective_anchor_prompt}")
                continue

            typer.echo("图审未通过，本次不会进入 Manju 视频生成。", err=True)
            raise typer.Exit(code=1)

    assert anchor_response is not None

    effective_video_prompt = video_prompt or _build_manju_video_prompt(
        character_name=character_asset.display_name,
        scene_name=scene_asset.display_name,
        storyboard_text=storyboard_text,
        extra_subject_names=extra_subject_names,
        shot_size=shot_size,
        camera_angle=camera_angle,
        camera_focus=camera_focus,
        cut_reason=cut_reason,
        continuity_reference_enabled=resolved_continuity_ref is not None,
        continuity_note=continuity_note,
    )
    effective_duration_seconds = duration_seconds or _estimate_manju_duration_seconds(storyboard_text)
    resolved_video_output_path = video_output_path or (
        config.project_root / "outputs" / "videos" / f"{anchor_response.shot_id}.mp4"
    )
    review_dir = config.project_root / "outputs" / "reviews" / anchor_response.shot_id
    review_dir.mkdir(parents=True, exist_ok=True)
    report_path = config.project_root / "outputs" / "reviews" / f"{anchor_response.shot_id}_audit.html"
    max_video_attempts = 3

    for video_attempt in range(1, max_video_attempts + 1):
        temp_video_output_path = review_dir / f"{anchor_response.shot_id}_manju_raw_attempt_{video_attempt}.mp4"
        review_frames_dir = review_dir / f"frames_attempt_{video_attempt}"
        process = _run_manju_one_shot_script(
            project_root=config.project_root,
            image_path=Path(anchor_response.output_path),
            prompt=effective_video_prompt,
            output_path=temp_video_output_path,
            mode=manju_mode,
            resolution=manju_resolution,
            duration_seconds=effective_duration_seconds,
            aspect_ratio=aspect_ratio,
            model_name=manju_model_name,
            project_url=manju_project_url,
            profile_dir=manju_profile_dir,
            headless=manju_headless,
        )

        typer.echo("Manju 视频生成结果")
        typer.echo(f"- video_attempt: {video_attempt}")
        typer.echo(f"- video_prompt: {effective_video_prompt}")
        typer.echo(f"- manju_mode: {manju_mode}")
        typer.echo(f"- manju_resolution: {manju_resolution}")
        typer.echo(f"- manju_duration_seconds: {effective_duration_seconds}")
        typer.echo(f"- manju_aspect_ratio: {aspect_ratio}")
        typer.echo(f"- manju_model_name: {manju_model_name}")
        typer.echo(f"- manju_project_url: {manju_project_url or 'default'}")
        typer.echo(f"- manju_profile_dir: {str(manju_profile_dir) if manju_profile_dir else 'default'}")
        typer.echo(f"- manju_headless: {'yes' if manju_headless else 'no'}")
        typer.echo(f"- raw_video_path: {temp_video_output_path}")
        typer.echo(f"- final_video_path: {resolved_video_output_path}")
        typer.echo(f"- script_exit_code: {process.returncode}")
        if process.returncode != 0:
            typer.echo("- status: failed", err=True)
            if process.stdout.strip():
                typer.echo("- script_stdout:", err=True)
                for line in process.stdout.strip().splitlines()[-10:]:
                    typer.echo(f"  - {line}", err=True)
            if process.stderr.strip():
                typer.echo("- script_stderr:", err=True)
                for line in process.stderr.strip().splitlines()[-10:]:
                    typer.echo(f"  - {line}", err=True)
            raise typer.Exit(code=1)

        if process.stdout.strip():
            typer.echo("- script_stdout:")
            for line in process.stdout.strip().splitlines():
                typer.echo(f"  - {line}")
        if process.stderr.strip():
            typer.echo("- script_stderr:")
            for line in process.stderr.strip().splitlines()[-10:]:
                typer.echo(f"  - {line}")

        try:
            decision = _run_gemini_auto_audit(
                shot_id=anchor_response.shot_id,
                storyboard_text=storyboard_text,
                prompt_main=effective_video_prompt,
                prompt_negative="",
                ref_assets_in_order=["@SceneAnchorImage"],
                report_path=report_path,
                review_video_path=temp_video_output_path,
                review_frames_dir=review_frames_dir,
            )
        except (GeminiAuditError, RuntimeError, typer.BadParameter) as exc:
            typer.echo("- video_review_action: failed", err=True)
            typer.echo(f"- video_review_error: {exc}", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"- video_review_action: {decision.action}")
        typer.echo(f"- video_review_summary: {decision.review_summary or '无'}")
        typer.echo(
            f"- video_review_issues: {', '.join(decision.selected_issue_ids) if decision.selected_issue_ids else '无'}"
        )
        typer.echo(f"- video_prompt_patch: {decision.prompt_patch or '无'}")
        typer.echo(f"- video_revised_prompt: {decision.revised_prompt_main or '无'}")
        typer.echo(f"- audit_report_path: {report_path}")

        if decision.action == "approve":
            resolved_video_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(temp_video_output_path, resolved_video_output_path)
            typer.echo("- status: success")
            typer.echo("- download_allowed: yes")
            typer.echo(f"- output_path: {resolved_video_output_path}")
            return

        if decision.action == "revise" and video_attempt < max_video_attempts:
            effective_video_prompt = decision.revised_prompt_main or _apply_prompt_patch(
                effective_video_prompt,
                decision.prompt_patch,
            )
            typer.echo("- video_retry: yes")
            typer.echo(f"- video_retry_prompt: {effective_video_prompt}")
            continue

        typer.echo("- status: blocked_by_video_review", err=True)
        raise typer.Exit(code=1)


@app.command("jimeng-dry-run")
def jimeng_dry_run() -> None:
    """执行即梦网页最小 dry run，不真正提交生成。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    _sample, planner_response, composer_response, reference_file_paths = _build_default_openclaw_outputs(
        config.project_root,
        template="continuity_first",
    )

    operator = JimengWebOperator(build_default_jimeng_config(config.project_root))
    result = operator.run_dry_run(
        JimengDryRunRequest(
            prompt_main=composer_response.prompt_main,
            ref_assets_in_order=composer_response.ref_assets_in_order,
            reference_file_paths=reference_file_paths,
        )
    )

    typer.echo("Jimeng Dry Run 结果")
    typer.echo(f"- 页面是否打开成功: {'是' if result.page_opened else '否'}")
    typer.echo(f"- 是否进入全能参考模式: {'是' if result.reference_mode_ready else '否'}")
    typer.echo(f"- prompt 是否填写成功: {'是' if result.prompt_filled else '否'}")
    typer.echo(f"- 参考图是否按顺序选中成功: {'是' if result.references_selected else '否'}")
    typer.echo(f"- 参考图校验是否成功: {'是' if result.validation_passed else '否'}")
    typer.echo(
        f"- 上传后的参考图引用名: {', '.join(result.uploaded_reference_names) if result.uploaded_reference_names else '无'}"
    )
    typer.echo(
        f"- 实际选中的参考图: {', '.join(result.selected_reference_names) if result.selected_reference_names else '无'}"
    )


@app.command("run-one-shot")
def run_one_shot(
    output_path: Optional[Path] = typer.Option(
        None,
        help="视频下载输出路径，默认保存到 outputs/videos/demo_shot_001.mp4。",
    ),
    audit_before_download: bool = typer.Option(
        False,
        "--audit-before-download/--no-audit-before-download",
        help="生成完成后、下载前先进入人工审计；不通过时只输出小幅修正提示词。",
    ),
    auto_audit: bool = typer.Option(
        False,
        "--auto-audit/--no-auto-audit",
        help="生成完成后使用 Gemini 自动审查；通过才正式落盘，不通过则输出小幅修正提示词。",
    ),
) -> None:
    """执行单镜头最小真实生成闭环。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    sample, planner_response, composer_response, reference_file_paths = _build_default_openclaw_outputs(
        config.project_root,
        template="continuity_first",
    )
    transition_reference = "@TransitionFrame"
    resolved_output_path = output_path or (
        config.project_root / "outputs" / "videos" / f"{composer_response.shot_id}.mp4"
    )

    jimeng_config = build_default_jimeng_config(config.project_root)
    jimeng_config.dry_run = False
    operator = JimengWebOperator(jimeng_config)
    result = operator.run_one_shot(
        JimengOneShotRequest(
            shot_id=composer_response.shot_id,
            prompt_main=composer_response.prompt_main,
            model_name="Seedance 2.0 Fast",
            prompt_negative=composer_response.prompt_negative,
            ref_assets_in_order=composer_response.ref_assets_in_order,
            reference_file_paths=reference_file_paths,
            transition_reference=transition_reference,
            storyboard_text=sample["storyboard_text"],
            hold_for_audit=audit_before_download or auto_audit,
            output_path=resolved_output_path,
        )
    )
    _finalize_download_with_optional_audit(
        operator=operator,
        result=result,
        shot_id=composer_response.shot_id,
        storyboard_text=sample["storyboard_text"],
        prompt_main=composer_response.prompt_main,
        prompt_negative=composer_response.prompt_negative,
        ref_assets_in_order=composer_response.ref_assets_in_order,
        output_path=resolved_output_path,
        report_dir=config.project_root / "outputs" / "reviews",
        audit_before_download=audit_before_download,
        auto_audit=auto_audit,
    )

    typer.echo("run-one-shot 结果")
    typer.echo(f"- shot_id: {result.shot_id}")
    typer.echo(f"- prompt_main: {result.prompt_main}")
    typer.echo(f"- prompt_negative: {composer_response.prompt_negative}")
    typer.echo(
        f"- ref_assets_in_order: {', '.join(result.ref_assets_in_order) if result.ref_assets_in_order else '无'}"
    )
    typer.echo(f"- transition_reference: {result.transition_reference}")
    typer.echo(
        f"- 实际上传参考图: {', '.join(result.uploaded_reference_names) if result.uploaded_reference_names else '无'}"
    )
    typer.echo(
        f"- 实际选中的参考图: {', '.join(result.selected_reference_names) if result.selected_reference_names else '无'}"
    )
    typer.echo(f"- 是否成功提交生成: {'是' if result.submitted else '否'}")
    typer.echo(f"- 是否成功下载: {'是' if result.download_succeeded else '否'}")
    typer.echo(f"- 下载文件路径: {result.download_path or str(resolved_output_path)}")
    if result.audit_report_path:
        typer.echo(f"- 审计报告路径: {result.audit_report_path}")
    if result.audit_action:
        typer.echo(f"- 审计动作: {result.audit_action}")
    if result.audit_summary:
        typer.echo(f"- 审计摘要: {result.audit_summary}")
    if result.prompt_patch:
        typer.echo(f"- 小幅修正补丁: {result.prompt_patch}")
    if result.revised_prompt_main:
        typer.echo(f"- 修正后 prompt_main: {result.revised_prompt_main}")
    if result.failed_stage:
        typer.echo(f"- 失败阶段: {result.failed_stage}")
    if result.messages:
        typer.echo("- 执行日志:")
        for message in result.messages:
            typer.echo(f"  - {message}")


@app.command("run-two-shots")
def run_two_shots(
    output_dir: Optional[Path] = typer.Option(
        None,
        help="两镜头输出目录，默认使用 outputs/videos 与 outputs/transition_frames。",
    ),
    audit_before_download: bool = typer.Option(
        False,
        "--audit-before-download/--no-audit-before-download",
        help="每个镜头生成完成后、下载前先进入人工审计；不通过时只输出小幅修正提示词。",
    ),
    auto_audit: bool = typer.Option(
        False,
        "--auto-audit/--no-auto-audit",
        help="每个镜头生成完成后使用 Gemini 自动审查；通过才正式落盘，不通过则输出小幅修正提示词。",
    ),
) -> None:
    """执行“两镜头最小闭环”：镜头1生成 -> 承接帧分析 -> 镜头2承接生成。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    base_output_dir = output_dir or (config.project_root / "outputs")
    videos_dir = base_output_dir / "videos"
    transition_dir = base_output_dir / "transition_frames"
    videos_dir.mkdir(parents=True, exist_ok=True)
    transition_dir.mkdir(parents=True, exist_ok=True)

    shot_1_sample, shot_2_sample = _default_two_shot_samples()
    jimeng_config = build_default_jimeng_config(config.project_root)
    jimeng_config.dry_run = False

    shot_1_planner, shot_1_composer, shot_1_reference_files = _build_shot_openclaw_outputs(
        project_root=config.project_root,
        shot_sample=shot_1_sample,
        template="continuity_first",
        continuity_anchor="",
    )
    shot_1_video_path = videos_dir / "demo_shot_001.mp4"
    shot_1_operator = JimengWebOperator(jimeng_config)
    shot_1_result = shot_1_operator.run_one_shot(
        JimengOneShotRequest(
            shot_id=shot_1_composer.shot_id,
            prompt_main=shot_1_composer.prompt_main,
            model_name="Seedance 2.0 Fast",
            prompt_negative=shot_1_composer.prompt_negative,
            ref_assets_in_order=shot_1_composer.ref_assets_in_order,
            reference_file_paths=shot_1_reference_files,
            transition_reference="",
            storyboard_text=shot_1_sample["storyboard_text"],
            hold_for_audit=audit_before_download or auto_audit,
            output_path=shot_1_video_path,
        )
    )
    _finalize_download_with_optional_audit(
        operator=shot_1_operator,
        result=shot_1_result,
        shot_id=shot_1_composer.shot_id,
        storyboard_text=shot_1_sample["storyboard_text"],
        prompt_main=shot_1_composer.prompt_main,
        prompt_negative=shot_1_composer.prompt_negative,
        ref_assets_in_order=shot_1_composer.ref_assets_in_order,
        output_path=shot_1_video_path,
        report_dir=base_output_dir / "reviews",
        audit_before_download=audit_before_download,
        auto_audit=auto_audit,
    )

    if not shot_1_result.download_succeeded:
        typer.echo("run-two-shots 结果")
        typer.echo(f"- shot_1 生成成功: {'是' if shot_1_result.submitted else '否'}")
        typer.echo(f"- shot_1 下载成功: {'是' if shot_1_result.download_succeeded else '否'}")
        if shot_1_result.audit_report_path:
            typer.echo(f"- shot_1 审计报告路径: {shot_1_result.audit_report_path}")
        if shot_1_result.audit_action:
            typer.echo(f"- shot_1 审计动作: {shot_1_result.audit_action}")
        if shot_1_result.audit_summary:
            typer.echo(f"- shot_1 审计摘要: {shot_1_result.audit_summary}")
        if shot_1_result.prompt_patch:
            typer.echo(f"- shot_1 小幅修正补丁: {shot_1_result.prompt_patch}")
        if shot_1_result.revised_prompt_main:
            typer.echo(f"- shot_1 修正后 prompt_main: {shot_1_result.revised_prompt_main}")
        typer.echo(f"- shot_1 失败阶段: {shot_1_result.failed_stage or '未知'}")
        if shot_1_result.messages:
            typer.echo("- shot_1 执行日志:")
            for message in shot_1_result.messages:
                typer.echo(f"  - {message}")
        raise typer.Exit(code=1)

    analyzer = VideoAnalyzerService()
    transition_analysis = analyzer.analyze_one_shot(
        str(shot_1_video_path),
        current_shot_summary=shot_1_sample["current_shot_summary"],
        next_shot_summary=shot_1_sample["next_shot_summary"],
    )
    if transition_analysis.best_frame is None:
        typer.echo("run-two-shots 结果")
        typer.echo("- shot_1 生成成功: 是")
        typer.echo("- shot_1 承接帧分析失败: 未找到 best_frame")
        raise typer.Exit(code=1)

    transition_frame_path = extract_transition_frame(
        shot_1_video_path,
        transition_analysis.best_frame.timestamp_seconds,
        transition_dir / "demo_shot_001_transition.jpg",
    )

    shot_2_previous_frame_summary = (
        f"上一镜头最佳承接帧位于 {transition_analysis.best_frame.timestamp_seconds:.2f}s；"
        f"{transition_analysis.best_frame.reason}"
    )
    shot_2_planner, shot_2_composer, shot_2_reference_files = _build_shot_openclaw_outputs(
        project_root=config.project_root,
        shot_sample=shot_2_sample,
        template="continuity_first",
        previous_frame_summary=shot_2_previous_frame_summary,
        continuity_requirements=shot_2_sample["continuity_requirements"],
        continuity_anchor="@TransitionFrame",
    )
    shot_2_all_reference_files = _prepend_transition_reference(shot_2_reference_files, transition_frame_path)
    shot_2_video_path = videos_dir / "demo_shot_002.mp4"
    shot_2_operator = JimengWebOperator(jimeng_config)
    shot_2_result = shot_2_operator.run_one_shot(
        JimengOneShotRequest(
            shot_id=shot_2_composer.shot_id,
            prompt_main=shot_2_composer.prompt_main,
            model_name="Seedance 2.0 Fast",
            prompt_negative=shot_2_composer.prompt_negative,
            ref_assets_in_order=shot_2_composer.ref_assets_in_order,
            reference_file_paths=shot_2_all_reference_files,
            transition_reference="@TransitionFrame",
            storyboard_text=shot_2_sample["storyboard_text"],
            hold_for_audit=audit_before_download or auto_audit,
            output_path=shot_2_video_path,
        )
    )
    _finalize_download_with_optional_audit(
        operator=shot_2_operator,
        result=shot_2_result,
        shot_id=shot_2_composer.shot_id,
        storyboard_text=shot_2_sample["storyboard_text"],
        prompt_main=shot_2_composer.prompt_main,
        prompt_negative=shot_2_composer.prompt_negative,
        ref_assets_in_order=shot_2_composer.ref_assets_in_order,
        output_path=shot_2_video_path,
        report_dir=base_output_dir / "reviews",
        audit_before_download=audit_before_download,
        auto_audit=auto_audit,
    )

    typer.echo("run-two-shots 结果")
    typer.echo(f"- shot_1 生成成功: {'是' if shot_1_result.download_succeeded else '否'}")
    typer.echo(f"- shot_1_video_path: {shot_1_result.download_path or str(shot_1_video_path)}")
    typer.echo(f"- shot_1_transition_frame_path: {transition_frame_path}")
    typer.echo(f"- shot_2 是否已使用承接帧: {'是' if '@TransitionFrame' in shot_2_composer.ref_assets_in_order else '否'}")
    typer.echo(f"- shot_2_prompt_main: {shot_2_composer.prompt_main}")
    typer.echo(
        f"- shot_2_ref_assets_in_order: {', '.join(shot_2_composer.ref_assets_in_order) if shot_2_composer.ref_assets_in_order else '无'}"
    )
    typer.echo(f"- shot_2 生成成功: {'是' if shot_2_result.submitted else '否'}")
    typer.echo(f"- shot_2 下载成功: {'是' if shot_2_result.download_succeeded else '否'}")
    typer.echo(f"- shot_2_video_path: {shot_2_result.download_path or str(shot_2_video_path)}")
    if shot_2_result.audit_report_path:
        typer.echo(f"- shot_2 审计报告路径: {shot_2_result.audit_report_path}")
    if shot_2_result.audit_action:
        typer.echo(f"- shot_2 审计动作: {shot_2_result.audit_action}")
    if shot_2_result.audit_summary:
        typer.echo(f"- shot_2 审计摘要: {shot_2_result.audit_summary}")
    if shot_2_result.prompt_patch:
        typer.echo(f"- shot_2 小幅修正补丁: {shot_2_result.prompt_patch}")
    if shot_2_result.revised_prompt_main:
        typer.echo(f"- shot_2 修正后 prompt_main: {shot_2_result.revised_prompt_main}")
    if shot_2_result.failed_stage:
        typer.echo(f"- shot_2 失败阶段: {shot_2_result.failed_stage}")
    if shot_2_result.messages:
        typer.echo("- shot_2 执行日志:")
        for message in shot_2_result.messages:
            typer.echo(f"  - {message}")


@app.command("watch-jimeng-job")
def watch_jimeng_job(
    output_path: Optional[Path] = typer.Option(
        None,
        help="视频下载输出路径，默认保存到 outputs/videos/watched_latest.mp4。",
    ),
    poll_interval_seconds: int = typer.Option(
        30,
        help="轮询间隔秒数，默认每 30 秒检查一次。",
    ),
    timeout_seconds: int = typer.Option(
        14400,
        help="最长等待秒数，默认 4 小时；传 0 表示持续监视直到完成。",
    ),
) -> None:
    """监视即梦最新任务，完成后通知并自动下载。"""

    _configure_logging()
    _load_dotenv()
    config = get_config()
    resolved_output_path = output_path or (
        config.project_root / "outputs" / "videos" / "watched_latest.mp4"
    )

    jimeng_config = build_default_jimeng_config(config.project_root)
    jimeng_config.dry_run = False
    operator = JimengWebOperator(jimeng_config)
    result = operator.watch_and_download(
        output_path=resolved_output_path,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    typer.echo("watch-jimeng-job 结果")
    typer.echo(f"- 页面是否打开成功: {'是' if result.page_opened else '否'}")
    typer.echo(f"- 是否进入全能参考模式: {'是' if result.reference_mode_ready else '否'}")
    typer.echo(f"- 是否等到任务完成: {'是' if result.generation_completed else '否'}")
    typer.echo(f"- 轮询状态: {result.poll_status or '无'}")
    typer.echo(f"- 是否成功下载: {'是' if result.download_succeeded else '否'}")
    typer.echo(f"- 下载文件路径: {result.download_path or str(resolved_output_path)}")
    if result.failed_stage:
        typer.echo(f"- 失败阶段: {result.failed_stage}")
    if result.messages:
        typer.echo("- 执行日志:")
        for message in result.messages:
            typer.echo(f"  - {message}")

    if result.download_succeeded:
        _notify_local(
            "即梦任务已完成",
            f"视频已下载到 {result.download_path}",
        )


@app.command("parse-feishu-link")
def parse_feishu_link_command(url: str) -> None:
    """识别并解析飞书 Base 或 Wiki 链接。"""

    result = parse_feishu_link(url)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result["link_type"] == "wiki":
        typer.echo("警告: 该链接不是直接的 base API 链接。", err=True)
        typer.echo("警告: 不能直接用 wiki_token 调 bitable records API。", err=True)
        typer.echo("警告: 需要进一步找到真实 base/app_token，或增加从 wiki 页面继续解析真实数据源的方案。", err=True)


@app.command("inspect-feishu-link-source")
def inspect_feishu_link_source_command(url: str) -> None:
    """抓取飞书链接源码并尝试提取真实 bitable 线索。"""

    _configure_logging()
    _load_dotenv()
    try:
        result = inspect_feishu_link_source(url)
    except FeishuApiError as exc:
        typer.echo("抓取飞书页面源码失败：", err=True)
        typer.echo(f"URL: {exc.url}", err=True)
        if exc.status_code is not None:
            typer.echo(f"HTTP 状态码: {exc.status_code}", err=True)
        typer.echo(exc.response_body or "<empty>", err=True)
        raise typer.Exit(code=1)

    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("feishu-sync-test")
def feishu_sync_test(
    range_value: list[str] = typer.Option(["Sheet1!A:C"], "--range", help="飞书表格读取区间，可重复传入。"),
    output_dir: Optional[Path] = typer.Option(None, help="素材下载输出目录，默认使用项目 assets 目录。"),
) -> None:
    """运行飞书素材同步最小闭环测试命令。"""

    _configure_logging()
    _load_dotenv()
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    spreadsheet_token = os.getenv("SPREADSHEET_TOKEN")
    app_token = os.getenv("FEISHU_APP_TOKEN")
    table_id = os.getenv("FEISHU_TABLE_ID")
    view_id = os.getenv("FEISHU_VIEW_ID", "")
    base_url = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn")

    missing = [
        name
        for name, value in (
            ("FEISHU_APP_ID", app_id),
            ("FEISHU_APP_SECRET", app_secret),
        )
        if not value
    ]
    if missing:
        raise typer.BadParameter(f"缺少环境变量: {', '.join(missing)}")

    if not (app_token and table_id) and not spreadsheet_token:
        raise typer.BadParameter(
            "未检测到可用的飞书数据源配置。请提供 FEISHU_APP_TOKEN + FEISHU_TABLE_ID，或提供 SPREADSHEET_TOKEN。"
        )

    if app_token:
        typer.echo(
            "提示: 当前 bitable 使用的是 FEISHU_APP_TOKEN。若它来自旧模板链接，或你现在拿到的是 wiki 链接，这个 app_token 很可能不对应现有数据源。",
            err=True,
        )
        typer.echo(
            f"当前区分如下: bitable app_token={app_token}, table_id={table_id or ''}, view_id={view_id or ''}",
            err=True,
        )

    config = get_config()
    sync_config = FeishuSyncConfig(
        app_id=app_id or "",
        app_secret=app_secret or "",
        spreadsheet_token=spreadsheet_token or "",
        app_token=app_token or "",
        table_id=table_id or "",
        view_id=view_id,
        base_url=base_url,
        ranges=range_value,
        output_dir=output_dir or config.project_root / "assets",
    )
    try:
        result = sync_assets(sync_config)
    except FeishuApiError as exc:
        typer.echo("飞书请求失败，以下是调试信息：", err=True)
        typer.echo(f"请求方法: {exc.method}", err=True)
        typer.echo(f"最终请求 URL: {exc.url}", err=True)
        typer.echo(f"是否带 view_id: {'view_id' in exc.query_params}", err=True)
        typer.echo(f"请求参数: {json.dumps(exc.query_params, ensure_ascii=False, indent=2)}", err=True)
        if exc.status_code is not None:
            typer.echo(f"HTTP 状态码: {exc.status_code}", err=True)
        typer.echo("错误响应体:", err=True)
        typer.echo(exc.response_body or "<empty>", err=True)
        raise typer.Exit(code=1)

    summary = {
        "mode": "bitable" if sync_config.use_bitable else "spreadsheet",
        "total_rows": result.total_rows,
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "output_dir": str(sync_config.output_dir),
        "manifest_path": result.manifest_path,
        "assets": result.assets,
    }
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
