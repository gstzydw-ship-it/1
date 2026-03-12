"""漫剧平台单任务最小生成脚本。

这个脚本专门负责一条稳定链路：
1. 打开已登录的漫剧平台 profile
2. 在指定故事板下新增一个视频任务
3. 填写提示词
4. 上传首帧图片
5. 按顺序设置模式、分辨率、时长等参数
6. 点击生成
7. 只下载当前新任务自己的视频结果
"""

from __future__ import annotations

import argparse
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
    "前景壮硕男生震惊回头，人物外观稳定，固定中景，背景保持教室空间一致。"
)


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
        " 可使用 --headed 打开有头浏览器配合人工登录，或传入 --profile-dir 指向已登录的 Manju profile。"
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
    return row.locator("span.text-sm.font-semibold.text-gray-800").first.inner_text(timeout=5000).strip()


def _find_storyboard_card(page: Page, storyboard_index: int) -> Locator:
    cards = _storyboard_cards(page)
    count = cards.count()
    if storyboard_index < 0 or storyboard_index >= count:
        raise IndexError(f"故事板索引越界：{storyboard_index}，当前只有 {count} 个故事板。")
    return cards.nth(storyboard_index)


def _wait_for_project_page(page: Page, *, profile: Path, timeout_seconds: int = 90) -> None:
    for _ in range(timeout_seconds):
        if _is_login_page(page):
            raise RuntimeError(_build_login_required_message(profile, page.url))
        if _storyboard_cards(page).count() > 0:
            return
        page.wait_for_timeout(1_000)

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
            button.scroll_into_view_if_needed(timeout=10_000)
        except Exception:
            pass
        try:
            button.click(timeout=10_000)
            return
        except Exception:
            continue
    raise RuntimeError("新增视频任务失败：未找到可点击的新增按钮。")


def _new_task_row(card: Locator, before_count: int) -> Locator:
    page = card.page
    for attempt in range(3):
        _click_add_task(card)
        for _ in range(12):
            rows = _task_rows(card)
            after_count = rows.count()
            if after_count > before_count:
                return rows.nth(after_count - 1)
            page.wait_for_timeout(1_000)
        page.wait_for_timeout(1_000 * (attempt + 1))
    raise RuntimeError("新增视频任务失败：任务数量没有增加。")


def _expand_row(row: Locator) -> None:
    row.scroll_into_view_if_needed(timeout=10_000)
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
            locator.dblclick(timeout=10_000)
            row.page.wait_for_timeout(2_000)
            if row.locator("textarea").count() > 0:
                return
        except Exception:
            continue
    raise RuntimeError("新视频任务展开失败。")


def _fill_prompt(row: Locator, prompt: str) -> None:
    textarea = row.locator("textarea:visible").first
    page = row.page
    for attempt in range(3):
        textarea.click(timeout=10_000)
        textarea.fill("", timeout=10_000)
        textarea.fill(prompt, timeout=10_000)
        page.wait_for_timeout(500)
        current_value = textarea.input_value(timeout=3_000).strip()
        if current_value == prompt.strip():
            textarea.blur()
            page.wait_for_timeout(300)
            return

        textarea.click(timeout=10_000)
        textarea.press("Control+A", timeout=3_000)
        textarea.type(prompt, delay=10, timeout=30_000)
        page.wait_for_timeout(500)
        current_value = textarea.input_value(timeout=3_000).strip()
        if current_value == prompt.strip():
            textarea.blur()
            page.wait_for_timeout(300)
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
        page.wait_for_timeout(500)
        current_value = textarea.input_value(timeout=3_000).strip()
        if current_value == prompt.strip():
            textarea.blur()
            page.wait_for_timeout(300)
            return

        page.wait_for_timeout(1_000 * (attempt + 1))

    raise RuntimeError("提示词填写失败：输入框仍为空。")


def _ensure_prompt_persisted(row: Locator, prompt: str) -> None:
    textarea = row.locator("textarea:visible").first
    expected_value = prompt.strip()
    page = row.page
    for _ in range(3):
        current_value = textarea.input_value(timeout=3_000).strip()
        if current_value == expected_value:
            return
        _fill_prompt(row, prompt)
        page.wait_for_timeout(2_000)
    raise RuntimeError("提示词未成功保存在当前任务中。")


def _open_first_frame_modal(row: Locator) -> None:
    row.locator("div.w-20.h-20.border-2.border-dashed").first.click(timeout=10_000)


def _switch_upload_tab(page: Page) -> Locator:
    modal = page.locator("div.fixed.inset-0.z-50").first
    modal.locator("div.flex.border-b.border-gray-200.bg-gray-50 > button").nth(2).click(timeout=10_000)
    return modal


def _upload_first_frame(page: Page, image_path: Path) -> None:
    input_locator = page.locator("input[type='file'][accept*='image']").first
    input_locator.set_input_files(str(image_path), timeout=10_000)


def _wait_for_first_frame_ready(row: Locator, baseline_img_count: int) -> None:
    page = row.page
    for _ in range(30):
        page.wait_for_timeout(1_000)
        if row.locator("img").count() > baseline_img_count:
            return
        if page.locator("div.fixed.inset-0.z-50").count() == 0:
            return
    raise RuntimeError("首帧图片上传后未回填到当前任务。")


def _normalize_ui_text(text: str) -> str:
    return "".join(text.split()).replace("-", "").lower()


def _matching_text_controls(row: Locator, target_value: str) -> list[Locator]:
    controls = row.locator("button, div, span")
    normalized_target = _normalize_ui_text(target_value)
    matches: list[Locator] = []
    for index in range(controls.count()):
        candidate = controls.nth(index)
        try:
            text = candidate.inner_text(timeout=500).strip()
        except Exception:
            continue
        if _normalize_ui_text(text) != normalized_target:
            continue
        matches.append(candidate)
    return matches


def _parameter_buttons(row: Locator) -> list[Locator]:
    buttons = row.locator("button")
    result: list[Locator] = []
    allowed_values = {
        _normalize_ui_text(value)
        for value in {
            "Seedance1.5-pro",
            "Seedance1.5 pro",
            "4s",
            "5s",
            "6s",
            "7s",
            "8s",
            "9s",
            "10s",
            "11s",
            "12s",
            "480p",
            "1080p",
            "16:9",
            "普通模式",
            "草稿模式",
        }
    }
    for index in range(buttons.count()):
        candidate = buttons.nth(index)
        try:
            text = candidate.inner_text(timeout=1000).strip()
        except Exception:
            continue
        if _normalize_ui_text(text) in allowed_values:
            result.append(candidate)
    return result


def _click_visible_text_option(row: Locator, target_value: str) -> bool:
    for option in _matching_text_controls(row, target_value):
        try:
            option.click(timeout=3_000)
            row.page.wait_for_timeout(500)
            return True
        except Exception:
            continue
    return False


def _select_inline_select_option(row: Locator, target_value: str) -> bool:
    selects = row.locator("select")
    for index in range(selects.count()):
        select = selects.nth(index)
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

        try:
            normalized_target = _normalize_ui_text(target_value)
            for option_text in option_texts:
                if _normalize_ui_text(option_text) == normalized_target:
                    select.select_option(label=option_text, timeout=3_000)
                    row.page.wait_for_timeout(500)
                    return True

            if target_value == "普通模式" and {"true", "false"}.issubset(set(option_values)):
                select.select_option(value="false", timeout=3_000)
                row.page.wait_for_timeout(500)
                return True

            if target_value == "草稿模式" and {"true", "false"}.issubset(set(option_values)):
                select.select_option(value="true", timeout=3_000)
                row.page.wait_for_timeout(500)
                return True

            if target_value.endswith("s") and target_value[:-1].isdigit() and target_value[:-1] in option_values:
                select.select_option(value=target_value[:-1], timeout=3_000)
                row.page.wait_for_timeout(500)
                return True
        except Exception:
            continue

    return False


def _set_select_value(select: Locator, *, target_value: str) -> bool:
    for _ in range(10):
        try:
            disabled = select.is_disabled(timeout=1_000)
        except Exception:
            disabled = False
        if disabled:
            select.page.wait_for_timeout(500)
            continue

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

        if target_value == "普通模式" and {"true", "false"}.issubset(set(option_values)):
            select.select_option(value="false", timeout=3_000)
            return True

        if target_value == "草稿模式" and {"true", "false"}.issubset(set(option_values)):
            select.select_option(value="true", timeout=3_000)
            return True

        if target_value.endswith("s") and target_value[:-1].isdigit() and target_value[:-1] in option_values:
            select.select_option(value=target_value[:-1], timeout=3_000)
            return True

        if target_value in option_values:
            select.select_option(value=target_value, timeout=3_000)
            return True

        return False

    return False


def _select_dropdown_value(row: Locator, *, current_value: str, target_value: str) -> None:
    if current_value == target_value:
        return

    if _select_inline_select_option(row, target_value):
        return

    if _click_visible_text_option(row, target_value):
        return

    page = row.page
    selects = row.locator("select")
    normalized_current = _normalize_ui_text(current_value)
    normalized_target = _normalize_ui_text(target_value)
    for index in range(selects.count()):
        select = selects.nth(index)
        options = select.locator("option")
        option_tokens: set[str] = set()
        for option_index in range(options.count()):
            option = options.nth(option_index)
            try:
                option_tokens.add(_normalize_ui_text((option.get_attribute("value") or "").strip()))
                option_tokens.add(_normalize_ui_text(option.inner_text(timeout=200).strip()))
            except Exception:
                continue
        if normalized_current not in option_tokens and normalized_target not in option_tokens:
            continue
        class_name = select.get_attribute("class") or ""
        disabled = select.get_attribute("disabled") is not None or "cursor-not-allowed" in class_name
        if disabled:
            return

    matching_buttons = _matching_text_controls(row, current_value) or [
        button
        for button in _parameter_buttons(row)
        if _normalize_ui_text(button.inner_text(timeout=1000).strip()) == normalized_current
    ]
    if not matching_buttons:
        raise RuntimeError(f"未找到当前参数按钮：{current_value}")

    clicked = False
    for button in matching_buttons:
        try:
            button.click(timeout=5_000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        raise RuntimeError(f"无法展开参数下拉框：{current_value}")

    option_candidates = page.locator("div[role='option'], li, div")
    for _ in range(20):
        page.wait_for_timeout(500)
        for index in range(option_candidates.count()):
            option = option_candidates.nth(index)
            try:
                text = option.inner_text(timeout=500).strip()
            except Exception:
                continue
            if _normalize_ui_text(text) != normalized_target:
                continue
            try:
                option.click(timeout=3_000)
                page.wait_for_timeout(500)
                return
            except Exception:
                continue
    raise RuntimeError(f"未找到目标参数选项：{target_value}")


def _ensure_generation_settings(
    row: Locator,
    *,
    mode: str,
    resolution: str,
    duration_seconds: int,
    aspect_ratio: str,
    model_name: str,
) -> None:
    """按稳定顺序设置 Manju 页面参数。"""

    selects = row.locator("select")
    if selects.count() >= 5:
        ordered_targets = [
            ("mode", selects.nth(4), mode),
            ("model", selects.nth(0), model_name),
            ("duration", selects.nth(1), f"{duration_seconds}s"),
            ("resolution", selects.nth(2), resolution),
            ("aspect_ratio", selects.nth(3), aspect_ratio),
        ]
        for label, select, target in ordered_targets:
            if not _set_select_value(select, target_value=target):
                raise RuntimeError(f"未找到 {label} 的目标参数选项：{target}")
            row.page.wait_for_timeout(500)

        expected_values = {
            "model": model_name,
            "duration": str(duration_seconds),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "mode": "false" if mode == "普通模式" else "true" if mode == "草稿模式" else mode,
        }
        actual_values = {
            "model": selects.nth(0).input_value(timeout=3_000).strip(),
            "duration": selects.nth(1).input_value(timeout=3_000).strip(),
            "resolution": selects.nth(2).input_value(timeout=3_000).strip(),
            "aspect_ratio": selects.nth(3).input_value(timeout=3_000).strip(),
            "mode": selects.nth(4).input_value(timeout=3_000).strip(),
        }
        for label, expected in expected_values.items():
            if actual_values[label] != expected:
                raise RuntimeError(
                    f"Manju 参数未成功切换：{label} 期望 {expected}，实际 {actual_values[label]}"
                )
        return

    _select_dropdown_value(row, current_value="普通模式", target_value=mode)
    _select_dropdown_value(row, current_value="480p", target_value=resolution)
    _select_dropdown_value(row, current_value="4s", target_value=f"{duration_seconds}s")
    _select_dropdown_value(row, current_value="16:9", target_value=aspect_ratio)
    _select_dropdown_value(row, current_value="Seedance1.5-pro", target_value=model_name)


def _wait_generate_button_enabled(row: Locator) -> Locator:
    button = row.locator("button.ml-auto.bg-blue-500").first
    for _ in range(30):
        try:
            if not button.is_disabled(timeout=1_000):
                return button
        except TimeoutError:
            pass
        row.page.wait_for_timeout(1_000)
    raise RuntimeError("生成按钮在等待后仍不可点击。")


def _wait_for_generated_video(row: Locator, baseline_video_count: int) -> str:
    page = row.page
    for _ in range(90):
        page.wait_for_timeout(10_000)
        body = row.inner_text(timeout=10_000)
        video_count = row.locator("video").count()
        if "已生成" in body and video_count > baseline_video_count:
            src = row.locator("video").first.get_attribute("src", timeout=10_000)
            if src:
                return src
    raise RuntimeError("等待当前任务生成完成超时。")


def _download_video(video_src: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(video_src, str(output_path))


def _snapshot(page: Page, name: str) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(TMP_DIR / name), full_page=True)


def _normalize_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    mapping = {
        "normal": "普通模式",
        "draft": "草稿模式",
        "普通模式": "普通模式",
        "草稿模式": "草稿模式",
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
    mode: str = "普通模式",
    resolution: str = "1080p",
    duration_seconds: int = 4,
    aspect_ratio: str = "16:9",
    model_name: str = "Seedance1.5-pro",
    headless: bool = True,
) -> RunResult:
    profile = profile_dir or _latest_profile_dir()
    if not image_path.exists():
        raise FileNotFoundError(f"首帧图片不存在：{image_path}")

    _log(f"使用 profile：{profile}")
    _log(f"使用首帧图片：{image_path}")
    _log(f"生成参数：mode={mode}, resolution={resolution}, duration={duration_seconds}s, aspect_ratio={aspect_ratio}, model={model_name}")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=headless,
            viewport={"width": 1600, "height": 1800},
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(project_url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(8_000)
        _wait_for_project_page(page, profile=profile)

        card = _find_storyboard_card(page, storyboard_index)
        before_count = _task_rows(card).count()
        _log(f"当前故事板已有任务数：{before_count}")

        row = _new_task_row(card, before_count)
        task_title = _task_title(row)
        _log(f"新建任务：{task_title}")

        _expand_row(row)
        page.wait_for_timeout(2_000)
        _snapshot(page, "after_expand.png")

        _fill_prompt(row, prompt)
        _ensure_prompt_persisted(row, prompt)
        page.wait_for_timeout(1_000)

        baseline_img_count = row.locator("img").count()
        _open_first_frame_modal(row)
        page.wait_for_timeout(2_000)
        _switch_upload_tab(page)
        page.wait_for_timeout(1_000)
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
        page.wait_for_timeout(3_000)
        _snapshot(page, "after_submit.png")

        video_src = _wait_for_generated_video(row, baseline_video_count)
        _snapshot(page, "after_wait.png")
        _download_video(video_src, output_path)
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
    parser.add_argument("--prompt-file", default="", help="可选：从 UTF-8 文本文件读取视频提示词")
    parser.add_argument("--output-path", default=str(PROJECT_ROOT / "outputs" / "videos" / "manju_one_shot.mp4"))
    parser.add_argument("--project-url", default=DEFAULT_URL, help="Manju 项目 URL")
    parser.add_argument("--storyboard-index", type=int, default=0, help="从 0 开始的故事板索引")
    parser.add_argument("--profile-dir", default="", help="已登录的 Chrome profile 目录；不传则自动取最新 manju profile")
    parser.add_argument("--mode", default="normal", help="生成模式，支持 normal/draft/普通模式/草稿模式")
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
        _log(f"执行失败：{exc}")
        return 1

    _log("执行成功")
    _log(f"任务：{result.task_title}")
    _log(f"视频：{result.output_path}")
    _log(f"视频源：{result.video_src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
