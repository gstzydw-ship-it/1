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


def _click_add_task(card: Locator) -> None:
    add_button = card.locator("button.inline-flex.items-center.gap-1").first
    try:
        add_button.click(timeout=10_000)
    except Exception:
        card.locator("button.text-green-700").first.click(timeout=10_000)


def _new_task_row(card: Locator, before_count: int) -> Locator:
    rows = _task_rows(card)
    after_count = rows.count()
    if after_count <= before_count:
        raise RuntimeError("新增视频任务失败：任务数量没有增加。")
    return rows.nth(after_count - 1)


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
    textarea = row.locator("textarea").first
    textarea.click(timeout=10_000)
    handle = textarea.element_handle()
    if handle is None:
        raise RuntimeError("未找到提示词输入框。")
    row.page.evaluate(
        """([el, value]) => {
            el.value = value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        }""",
        [handle, prompt],
    )


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


def _parameter_buttons(row: Locator) -> list[Locator]:
    buttons = row.locator("button")
    result: list[Locator] = []
    for index in range(buttons.count()):
        candidate = buttons.nth(index)
        try:
            text = candidate.inner_text(timeout=1000).strip()
        except Exception:
            continue
        if text in {"Seedance1.5-pro", "4s", "5s", "6s", "7s", "8s", "9s", "10s", "11s", "12s", "480p", "1080p", "16:9", "普通模式", "草稿模式"}:
            result.append(candidate)
    return result


def _select_dropdown_value(row: Locator, *, current_value: str, target_value: str) -> None:
    if current_value == target_value:
        return

    page = row.page
    matching_buttons = [button for button in _parameter_buttons(row) if button.inner_text(timeout=1000).strip() == current_value]
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
            if text != target_value:
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

    _select_dropdown_value(row, current_value="草稿模式", target_value=mode)
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
            headless=True,
            viewport={"width": 1600, "height": 1800},
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(project_url, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_timeout(8_000)

        card = _find_storyboard_card(page, storyboard_index)
        before_count = _task_rows(card).count()
        _log(f"当前故事板已有任务数：{before_count}")

        _click_add_task(card)
        page.wait_for_timeout(3_000)

        row = _new_task_row(card, before_count)
        task_title = _task_title(row)
        _log(f"新建任务：{task_title}")

        _expand_row(row)
        page.wait_for_timeout(2_000)
        _snapshot(page, "after_expand.png")

        _fill_prompt(row, prompt)
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
    parser = argparse.ArgumentParser(description="漫剧平台单任务最小生成脚本")
    parser.add_argument("--image-path", required=True, help="首帧图片路径")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="视频提示词")
    parser.add_argument("--output-path", default=str(PROJECT_ROOT / "outputs" / "videos" / "manju_one_shot.mp4"))
    parser.add_argument("--project-url", default=DEFAULT_URL)
    parser.add_argument("--storyboard-index", type=int, default=0, help="从 0 开始的故事板索引")
    parser.add_argument("--profile-dir", default="", help="已登录 profile 目录，不填则自动取最新 manju profile")
    parser.add_argument("--mode", default="普通模式", help="生成模式，默认 普通模式")
    parser.add_argument("--resolution", default="1080p", help="清晰度，默认 1080p")
    parser.add_argument("--duration-seconds", type=int, default=4, help="视频时长，默认 4")
    parser.add_argument("--aspect-ratio", default="16:9", help="比例，默认 16:9")
    parser.add_argument("--model-name", default="Seedance1.5-pro", help="模型名称")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = run_manju_one_shot(
            image_path=Path(args.image_path),
            prompt=args.prompt,
            output_path=Path(args.output_path),
            project_url=args.project_url,
            storyboard_index=args.storyboard_index,
            profile_dir=Path(args.profile_dir) if args.profile_dir else None,
            mode=args.mode,
            resolution=args.resolution,
            duration_seconds=args.duration_seconds,
            aspect_ratio=args.aspect_ratio,
            model_name=args.model_name,
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
