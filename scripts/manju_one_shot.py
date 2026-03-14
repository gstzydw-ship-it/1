"""Manju single-shot automation.

This script focuses on one stable flow:
1. Open a logged-in Manju browser profile.
2. Add a new task under a storyboard.
3. Fill the prompt.
4. Upload the first frame image.
5. Configure generation parameters.
6. Submit generation.
7. Download only the newly created task output.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Locator, Page, TimeoutError, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
TMP_DIR = PROJECT_ROOT / "tmp" / "manju_one_shot"

DEFAULT_URL = "https://manju.gamenow.club/projects/db664d15-8cfb-4ee7-8bb4-955cd432a769?step=4"
DEFAULT_PROMPT = (
    "教室内多人对峙场景，白发少年坐在桌前，黑发男子俯身逼近，"
    "前景男生震惊回头。人物外观稳定，固定中景，背景保持同一教室空间。"
)
NORMAL_MODE_LABEL = "普通模式"
DRAFT_MODE_LABEL = "草稿模式"
DEBUG_SCREENSHOTS = os.getenv("MANJU_DEBUG_SCREENSHOTS", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class RunResult:
    success: bool
    task_title: str
    output_path: Path
    video_src: str
    message: str


def _log(message: str) -> None:
    print(message, flush=True)


def _page_text_snippet(page: Page, *, limit: int = 240) -> str:
    try:
        text = page.locator("body").inner_text(timeout=5_000).strip()
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


def _is_login_page(page: Page) -> bool:
    try:
        if "/auth/login" in page.url.lower():
            return True
    except Exception:
        return False

    try:
        if page.locator("input[type='password']").count() > 0:
            return True
    except Exception:
        return False

    snippet = _page_text_snippet(page).lower()
    return "welcome to yuanjing" in snippet or "开始使用" in snippet


def _build_login_required_message(profile: Path, current_url: str) -> str:
    return (
        "Manju 登录态无效或已过期，请先完成一次登录后再重试。"
        f" 当前页面: {current_url}。"
        f" 当前 profile: {profile}。"
        " 可使用 --headed 打开有头浏览器配合人工登录，"
        "或传入 --profile-dir 指向已登录的 Manju profile。"
    )


def _latest_profile_dir() -> Path:
    candidates = sorted(
        (path for path in RUNTIME_DIR.glob("manju-browser*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("未找到 manju 浏览器 profile，请先手动登录一次。")
    return candidates[0]


def _storyboard_cards(page: Page) -> Locator:
    return page.locator("div.border.border-gray-200.rounded-xl.overflow-hidden.bg-white.shadow-sm")


def _task_rows(card: Locator) -> Locator:
    return card.locator("div.py-5.px-5.transition-colors")


def _task_title(row: Locator) -> str:
    return row.locator("span.text-sm.font-semibold.text-gray-800").first.inner_text(timeout=5_000).strip()


def _find_storyboard_card(page: Page, storyboard_index: int) -> Locator:
    cards = _storyboard_cards(page)
    count = cards.count()
    if storyboard_index < 0 or storyboard_index >= count:
        raise IndexError(f"故事板索引越界: {storyboard_index}，当前只有 {count} 个故事板。")
    return cards.nth(storyboard_index)


def _wait_for_project_page(page: Page, *, profile: Path, timeout_seconds: int = 60) -> None:
    for _ in range(timeout_seconds * 2):
        if _is_login_page(page):
            raise RuntimeError(_build_login_required_message(profile, page.url))
        if _storyboard_cards(page).count() > 0:
            return
        page.wait_for_timeout(500)

    raise RuntimeError(
        "Manju 项目页未进入可操作状态。"
        f" 当前页面: {page.url}。"
        f" 已识别故事板数量: {_storyboard_cards(page).count()}。"
        f" 页面片段: {_page_text_snippet(page)}"
    )


def _click_add_task(card: Locator) -> None:
    selectors = (
        "button.inline-flex.items-center.gap-1",
        "button.text-green-700",
    )
    for selector in selectors:
        button = card.locator(selector).first
        if button.count() == 0:
            continue
        try:
            button.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        try:
            button.click(timeout=5_000)
            return
        except Exception:
            continue
    raise RuntimeError("新增视频任务失败: 未找到可点击的新增按钮。")


def _new_task_row(card: Locator, before_count: int) -> Locator:
    page = card.page
    for attempt in range(3):
        _click_add_task(card)
        for _ in range(16):
            rows = _task_rows(card)
            after_count = rows.count()
            if after_count > before_count:
                return rows.nth(after_count - 1)
            page.wait_for_timeout(500)
        page.wait_for_timeout(500 * (attempt + 1))
    raise RuntimeError("新增视频任务失败: 任务数量没有增加。")


def _expand_row(row: Locator) -> None:
    row.scroll_into_view_if_needed(timeout=5_000)
    strategies = (
        "div[title='双击折叠/展开任务内容']",
        "div.cursor-pointer.select-none[title]",
        "div.cursor-pointer.select-none",
        "span.text-sm.font-semibold.text-gray-800",
    )
    for selector in strategies:
        locator = row.locator(selector).first
        if locator.count() == 0:
            continue
        try:
            locator.dblclick(timeout=5_000)
            for _ in range(8):
                if row.locator("textarea:visible").count() > 0:
                    return
                row.page.wait_for_timeout(250)
        except Exception:
            continue
    raise RuntimeError("新视频任务展开失败。")


def _fill_prompt(row: Locator, prompt: str) -> None:
    textarea = row.locator("textarea:visible").first
    page = row.page
    expected_value = prompt.strip()
    for attempt in range(3):
        textarea.click(timeout=5_000)
        textarea.fill("", timeout=5_000)
        textarea.fill(prompt, timeout=10_000)
        page.wait_for_timeout(300)
        current_value = textarea.input_value(timeout=2_000).strip()
        if current_value == expected_value:
            textarea.blur()
            return

        textarea.click(timeout=5_000)
        textarea.press("Control+A", timeout=2_000)
        textarea.type(prompt, delay=5, timeout=20_000)
        page.wait_for_timeout(300)
        current_value = textarea.input_value(timeout=2_000).strip()
        if current_value == expected_value:
            textarea.blur()
            return

        textarea.evaluate(
            """(element, value) => {
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype,
                    "value"
                )?.set;
                if (setter) {
                    setter.call(element, value);
                } else {
                    element.value = value;
                }
                element.dispatchEvent(new Event("input", { bubbles: true }));
                element.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            prompt,
        )
        page.wait_for_timeout(300)
        current_value = textarea.input_value(timeout=2_000).strip()
        if current_value == expected_value:
            textarea.blur()
            return

        page.wait_for_timeout(500 * (attempt + 1))

    raise RuntimeError("提示词填写失败: 输入框内容未正确写入。")


def _ensure_prompt_persisted(row: Locator, prompt: str) -> None:
    textarea = row.locator("textarea:visible").first
    expected_value = prompt.strip()
    for _ in range(2):
        current_value = textarea.input_value(timeout=2_000).strip()
        if current_value == expected_value:
            return
        _fill_prompt(row, prompt)
        row.page.wait_for_timeout(500)
    raise RuntimeError("提示词未成功保存在当前任务中。")


def _open_first_frame_modal(row: Locator) -> None:
    row.locator("div.w-20.h-20.border-2.border-dashed").first.click(timeout=5_000)


def _switch_upload_tab(page: Page) -> Locator:
    modal = page.locator("div.fixed.inset-0.z-50").first
    modal.locator("div.flex.border-b.border-gray-200.bg-gray-50 > button").nth(2).click(timeout=5_000)
    return modal


def _upload_first_frame(page: Page, image_path: Path) -> None:
    input_locator = page.locator("input[type='file'][accept*='image']").first
    input_locator.set_input_files(str(image_path), timeout=10_000)


def _wait_for_first_frame_ready(row: Locator, baseline_img_count: int) -> None:
    page = row.page
    for _ in range(40):
        page.wait_for_timeout(500)
        if row.locator("img").count() > baseline_img_count:
            return
        if page.locator("div.fixed.inset-0.z-50").count() == 0:
            return
    raise RuntimeError("首帧图片上传后未回填到当前任务。")


def _normalize_ui_text(text: str) -> str:
    return "".join(text.split()).replace("-", "").lower()


def _set_select_value(select: Locator, *, target_value: str) -> bool:
    for _ in range(12):
        try:
            if select.is_disabled(timeout=500):
                select.page.wait_for_timeout(250)
                continue
        except Exception:
            pass

        options = select.locator("option")
        option_values: list[str] = []
        option_texts: list[str] = []
        for option_index in range(options.count()):
            option = options.nth(option_index)
            try:
                option_values.append((option.get_attribute("value") or "").strip())
                option_texts.append(option.inner_text(timeout=200).strip())
            except Exception:
                continue

        normalized_target = _normalize_ui_text(target_value)
        for option_text in option_texts:
            if _normalize_ui_text(option_text) == normalized_target:
                select.select_option(label=option_text, timeout=3_000)
                return True

        if target_value.endswith("s") and target_value[:-1].isdigit() and target_value[:-1] in option_values:
            select.select_option(value=target_value[:-1], timeout=3_000)
            return True

        if target_value in option_values:
            select.select_option(value=target_value, timeout=3_000)
            return True

        if target_value == NORMAL_MODE_LABEL and {"true", "false"}.issubset(set(option_values)):
            select.select_option(value="false", timeout=3_000)
            return True
        if target_value == DRAFT_MODE_LABEL and {"true", "false"}.issubset(set(option_values)):
            select.select_option(value="true", timeout=3_000)
            return True

        return False
    return False


def _visible_selects(row: Locator) -> list[Locator]:
    selects = row.locator("select")
    result: list[Locator] = []
    for index in range(selects.count()):
        candidate = selects.nth(index)
        try:
            box = candidate.bounding_box()
        except Exception:
            box = None
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            continue
        result.append(candidate)
    return result


def _select_option_texts(select: Locator) -> list[str]:
    options = select.locator("option")
    result: list[str] = []
    for option_index in range(options.count()):
        option = options.nth(option_index)
        try:
            result.append(option.inner_text(timeout=200).strip())
        except Exception:
            continue
    return result


def _select_option_values(select: Locator) -> list[str]:
    options = select.locator("option")
    result: list[str] = []
    for option_index in range(options.count()):
        option = options.nth(option_index)
        try:
            result.append((option.get_attribute("value") or "").strip())
        except Exception:
            continue
    return result


def _classify_select_kind(select: Locator) -> str | None:
    option_texts = _select_option_texts(select)
    option_values = _select_option_values(select)
    normalized_texts = {_normalize_ui_text(text) for text in option_texts}
    normalized_values = {_normalize_ui_text(value) for value in option_values}

    if {"true", "false"}.issubset(normalized_values):
        return "mode"
    if option_texts and all(text.endswith("s") and text[:-1].isdigit() for text in option_texts):
        return "duration"
    if option_texts and all(text.endswith("p") and text[:-1].isdigit() for text in option_texts):
        return "resolution"
    if option_texts and all(":" in text for text in option_texts):
        return "aspect_ratio"

    model_markers = ("seedance", "sora", "veo", "wan")
    if any(any(marker in token for marker in model_markers) for token in normalized_texts | normalized_values):
        return "model"

    return None


def _find_select_by_kind(row: Locator, kind: str) -> Locator | None:
    for select in _visible_selects(row):
        if _classify_select_kind(select) == kind:
            return select
    return None


def _set_row_select_value(row: Locator, *, kind: str, target_value: str) -> bool:
    select = _find_select_by_kind(row, kind)
    if select is None:
        return False
    if not _set_select_value(select, target_value=target_value):
        return False
    row.page.wait_for_timeout(500)
    return True


def _read_row_setting_values(row: Locator) -> dict[str, str]:
    values: dict[str, str] = {}
    for select in _visible_selects(row):
        kind = _classify_select_kind(select)
        if not kind:
            continue
        try:
            values[kind] = select.input_value(timeout=2_000).strip()
        except Exception:
            continue
    return values


def _wait_for_select_target(row: Locator, *, kind: str, target_value: str, timeout_ms: int = 10_000) -> bool:
    waited = 0
    normalized_target = _normalize_ui_text(target_value)
    while waited < timeout_ms:
        select = _find_select_by_kind(row, kind)
        if select is not None:
            tokens = {_normalize_ui_text(text) for text in _select_option_texts(select)}
            tokens.update(_normalize_ui_text(value) for value in _select_option_values(select))
            if normalized_target in tokens:
                return True
        row.page.wait_for_timeout(250)
        waited += 250
    return False


def _ensure_generation_settings(
    row: Locator,
    *,
    mode: str,
    resolution: str,
    duration_seconds: int,
    aspect_ratio: str,
    model_name: str,
) -> None:
    last_error: RuntimeError | None = None
    for attempt in range(3):
        try:
            # Some models rebuild the parameter bar after model switch. Always re-query
            # by semantic kind instead of fixed select indexes.
            if not _set_row_select_value(row, kind="model", target_value=model_name):
                raise RuntimeError(f"未找到 model 的目标参数选项: {model_name}")

            _wait_for_select_target(row, kind="duration", target_value=f"{duration_seconds}s", timeout_ms=12_000)
            _wait_for_select_target(row, kind="resolution", target_value=resolution, timeout_ms=12_000)

            required_targets = {
                "duration": f"{duration_seconds}s",
                "resolution": resolution,
            }
            optional_targets = {
                "aspect_ratio": aspect_ratio,
                "mode": mode,
            }

            for kind, target in required_targets.items():
                if not _set_row_select_value(row, kind=kind, target_value=target):
                    raise RuntimeError(f"未找到 {kind} 的目标参数选项: {target}")

            for kind, target in optional_targets.items():
                _set_row_select_value(row, kind=kind, target_value=target)

            actual_values = _read_row_setting_values(row)
            expected_values = {
                "model": model_name,
                "duration": str(duration_seconds),
                "resolution": resolution,
            }
            if "aspect_ratio" in actual_values:
                expected_values["aspect_ratio"] = aspect_ratio

            for label, expected in expected_values.items():
                actual = actual_values.get(label, "")
                if _normalize_ui_text(actual) != _normalize_ui_text(expected):
                    raise RuntimeError(f"Manju 参数未成功切换: {label} 期望 {expected}，实际 {actual}")
            return
        except RuntimeError as exc:
            last_error = exc
            row.page.wait_for_timeout(1_000 * (attempt + 1))

    assert last_error is not None
    raise last_error


def _wait_generate_button_enabled(row: Locator) -> Locator:
    button = row.locator("button.ml-auto.bg-blue-500").first
    for _ in range(40):
        try:
            if not button.is_disabled(timeout=500):
                return button
        except TimeoutError:
            pass
        row.page.wait_for_timeout(500)
    raise RuntimeError("生成按钮等待超时，仍不可点击。")


def _wait_for_generated_video(row: Locator, baseline_video_count: int) -> str:
    page = row.page
    for _ in range(60):
        page.wait_for_timeout(5_000)
        body = row.inner_text(timeout=5_000)
        video_count = row.locator("video").count()
        if "已生成" in body and video_count > baseline_video_count:
            src = row.locator("video").first.get_attribute("src", timeout=5_000)
            if src:
                return src
    raise RuntimeError("等待当前任务生成完成超时。")


def _download_video(video_src: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(video_src, str(output_path))


def _snapshot(page: Page, name: str) -> None:
    if not DEBUG_SCREENSHOTS:
        return
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(TMP_DIR / name), full_page=True)


def _normalize_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    mapping = {
        "normal": NORMAL_MODE_LABEL,
        "draft": DRAFT_MODE_LABEL,
        NORMAL_MODE_LABEL: NORMAL_MODE_LABEL,
        DRAFT_MODE_LABEL: DRAFT_MODE_LABEL,
    }
    return mapping.get(normalized, mode)


def run_manju_one_shot(
    *,
    image_path: Path,
    prompt: str,
    output_path: Path,
    project_url: str,
    storyboard_index: int,
    profile_dir: Path | None = None,
    mode: str = NORMAL_MODE_LABEL,
    resolution: str = "1080p",
    duration_seconds: int = 4,
    aspect_ratio: str = "16:9",
    model_name: str = "Seedance1.5-pro",
    headless: bool = True,
) -> RunResult:
    profile = profile_dir or _latest_profile_dir()
    if not image_path.exists():
        raise FileNotFoundError(f"首帧图片不存在: {image_path}")

    _log(f"使用 profile: {profile}")
    _log(f"使用首帧图片: {image_path}")
    _log(
        "生成参数: "
        f"mode={mode}, resolution={resolution}, duration={duration_seconds}s, "
        f"aspect_ratio={aspect_ratio}, model={model_name}"
    )

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=headless,
            viewport={"width": 1600, "height": 1800},
            accept_downloads=True,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(project_url, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(2_000)
            _wait_for_project_page(page, profile=profile)

            card = _find_storyboard_card(page, storyboard_index)
            before_count = _task_rows(card).count()
            _log(f"当前故事板已有任务数: {before_count}")

            row = _new_task_row(card, before_count)
            task_title = _task_title(row)
            _log(f"新建任务: {task_title}")

            _expand_row(row)
            _snapshot(page, "after_expand.png")

            _fill_prompt(row, prompt)
            _ensure_prompt_persisted(row, prompt)

            baseline_img_count = row.locator("img").count()
            _open_first_frame_modal(row)
            page.wait_for_timeout(500)
            _switch_upload_tab(page)
            page.wait_for_timeout(500)
            _upload_first_frame(page, image_path)
            _wait_for_first_frame_ready(row, baseline_img_count)
            _snapshot(page, "after_upload.png")

            _ensure_generation_settings(
                row,
                mode=mode,
                resolution=resolution,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                model_name=model_name,
            )
            _ensure_prompt_persisted(row, prompt)
            _snapshot(page, "after_settings.png")

            baseline_video_count = row.locator("video").count()
            button = _wait_generate_button_enabled(row)
            button.click(timeout=10_000)
            page.wait_for_timeout(1_000)
            _snapshot(page, "after_submit.png")

            video_src = _wait_for_generated_video(row, baseline_video_count)
            _snapshot(page, "after_wait.png")
            _download_video(video_src, output_path)
        finally:
            context.close()

    return RunResult(
        success=True,
        task_title=task_title,
        output_path=output_path,
        video_src=video_src,
        message="生成并下载成功",
    )


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manju 单任务最小生成脚本")
    parser.add_argument("--image-path", required=True, help="首帧图片路径")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="视频提示词")
    parser.add_argument("--prompt-file", default="", help="可选: 从 UTF-8 文本文件读取视频提示词")
    parser.add_argument("--output-path", default=str(PROJECT_ROOT / "outputs" / "videos" / "manju_one_shot.mp4"))
    parser.add_argument("--project-url", default=DEFAULT_URL, help="Manju 项目 URL")
    parser.add_argument("--storyboard-index", type=int, default=0, help="从 0 开始的故事板索引")
    parser.add_argument("--profile-dir", default="", help="已登录的 Chrome profile 目录; 不传则自动取最新 manju profile")
    parser.add_argument("--mode", default="normal", help=f"生成模式，支持 normal/draft/{NORMAL_MODE_LABEL}/{DRAFT_MODE_LABEL}")
    parser.add_argument("--resolution", default="1080p", help="清晰度，默认 1080p")
    parser.add_argument("--duration-seconds", type=int, default=4, help="视频时长，默认 4")
    parser.add_argument("--aspect-ratio", default="16:9", help="比例，默认 16:9")
    parser.add_argument("--model-name", default="Seedance1.5-pro", help="模型名称")
    parser.add_argument("--headed", action="store_true", help="使用有头浏览器，方便人工登录和排查页面问题")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip() or prompt
    try:
        result = run_manju_one_shot(
            image_path=Path(args.image_path),
            prompt=prompt,
            output_path=Path(args.output_path),
            project_url=args.project_url,
            storyboard_index=args.storyboard_index,
            profile_dir=Path(args.profile_dir) if args.profile_dir else None,
            mode=_normalize_mode(args.mode),
            resolution=args.resolution,
            duration_seconds=args.duration_seconds,
            aspect_ratio=args.aspect_ratio,
            model_name=args.model_name,
            headless=not args.headed,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"执行失败: {exc}")
        return 1

    _log("执行成功")
    _log(f"任务: {result.task_title}")
    _log(f"视频: {result.output_path}")
    _log(f"视频源: {result.video_src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
