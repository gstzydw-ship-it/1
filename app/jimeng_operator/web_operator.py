"""即梦网页最小 Web Operator。"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

from app.jimeng_operator.models import (
    JimengDryRunRequest,
    JimengDryRunResult,
    JimengOneShotRequest,
    JimengOneShotResult,
    JimengOperatorConfig,
    JimengWatchResult,
)
from app.jimeng_operator.selectors import (
    DEFAULT_JIMENG_SELECTORS,
    JimengSelectors,
    build_reference_option_selectors,
)

GENERATE_BUTTON_SELECTORS = (
    "button.submit-button-KJTUYS",
    "button.submit-button-CpjScj",
    "button[class*='submit-button']",
    "button:has-text('生成视频')",
    "button:has-text('立即生成')",
    "button:has-text('开始生成')",
    "button:has-text('生成')",
)

NEGATIVE_PROMPT_SELECTORS = (
    "textarea[placeholder*='负向']",
    "textarea[placeholder*='反向']",
    "textarea[placeholder*='避免']",
    "textarea[aria-label*='负向']",
)

DOWNLOAD_ENTRY_SELECTORS = (
    "button:has-text('下载')",
    "a:has-text('下载')",
    "text=下载",
)

DETAIL_ENTRY_SELECTORS = (
    "button:has-text('详情信息')",
    "text=详情信息",
)

RESULT_READY_TEXTS = ("再次生成", "重新编辑", "详情信息", "下载")
RESULT_PENDING_TEXTS = ("生成中", "排队", "队列", "处理中", "预计")
RESULT_FAILED_TEXTS = ("失败", "异常", "重试")
LOGIN_REQUIRED_TEXTS = ("同意协议后前往登录", "已阅读并同意用户服务协议", "前往登录")


class BrowserSessionProtocol(Protocol):
    """供 operator 调用的最小浏览器会话协议。"""

    def goto(self, url: str) -> None: ...

    def close_blocking_dialogs(self, selectors: JimengSelectors) -> None: ...

    def enter_video_reference_mode(self, selectors: JimengSelectors) -> bool: ...

    def fill_prompt(self, selectors: JimengSelectors, prompt_main: str) -> bool: ...

    def fill_negative_prompt(self, prompt_negative: str) -> bool: ...

    def upload_reference_files(self, selectors: JimengSelectors, file_paths: list[Path]) -> list[str]: ...

    def select_reference_asset(self, selectors: JimengSelectors, asset_name: str) -> bool: ...

    def get_selected_reference_names(self, selectors: JimengSelectors) -> list[str]: ...

    def submit_generation(self) -> bool: ...

    def wait_for_generation_result(self, timeout_seconds: int, poll_interval_seconds: int) -> tuple[bool, str]: ...

    def download_latest_result(self, output_path: Path) -> bool: ...

    def close(self) -> None: ...


class PlaywrightBrowserSession:
    """基于 Playwright persistent context 的最小浏览器会话。"""

    def __init__(self, config: JimengOperatorConfig) -> None:
        self.config = config
        self._playwright = None
        self._context = None
        self._page = None
        self._baseline_ready_marker_count = 0
        self._baseline_video_srcs: set[str] = set()
        self._latest_generated_video_src = ""

    def goto(self, url: str) -> None:
        self._ensure_started()
        self._page.goto(url, wait_until="domcontentloaded", timeout=120000)
        self._page.wait_for_timeout(3000)

    def close_blocking_dialogs(self, selectors: JimengSelectors) -> None:
        for selector in selectors.popup_close_buttons:
            locator = self._page.locator(selector).first
            try:
                locator.click(timeout=2000)
                self._page.wait_for_timeout(1000)
            except Exception:
                continue

    def enter_video_reference_mode(self, selectors: JimengSelectors) -> bool:
        if self._is_generate_page(selectors):
            return True

        for selector in selectors.video_entry_buttons:
            locator = self._page.locator(selector).first
            try:
                locator.click(timeout=5000)
                self._page.wait_for_timeout(4000)
                if self._is_generate_page(selectors):
                    return True
            except Exception:
                continue
        return self._is_generate_page(selectors)

    def fill_prompt(self, selectors: JimengSelectors, prompt_main: str) -> bool:
        locator = self._first_visible_locator(selectors.prompt_inputs)
        if locator is None:
            return False

        locator.click()
        self._page.keyboard.press("Control+A")
        self._page.keyboard.press("Backspace")
        self._page.keyboard.type(prompt_main)
        return True

    def fill_negative_prompt(self, prompt_negative: str) -> bool:
        if not prompt_negative.strip():
            return False

        locator = self._first_visible_locator(NEGATIVE_PROMPT_SELECTORS, timeout_ms=1500)
        if locator is None:
            return False

        locator.click()
        try:
            locator.fill(prompt_negative)
        except Exception:
            self._page.keyboard.press("Control+A")
            self._page.keyboard.press("Backspace")
            self._page.keyboard.type(prompt_negative)
        return True

    def upload_reference_files(self, selectors: JimengSelectors, file_paths: list[Path]) -> list[str]:
        if not file_paths:
            return []

        locator = self._first_attached_locator(selectors.reference_file_inputs)
        if locator is None:
            return []

        locator.set_input_files([str(path.resolve()) for path in file_paths])
        self._page.wait_for_timeout(8000)
        return [f"图片{i}" for i in range(1, len(file_paths) + 1)]

    def select_reference_asset(self, selectors: JimengSelectors, asset_name: str) -> bool:
        prompt_locator = self._first_visible_locator(selectors.prompt_inputs)
        if prompt_locator is None:
            return False

        prompt_locator.click()
        for key in ("Control+End", "End"):
            try:
                self._page.keyboard.press(key)
            except Exception:
                continue
        self._page.keyboard.type(" ")
        self._page.keyboard.type("@")
        self._page.wait_for_timeout(800)

        typed_name = asset_name.strip()
        if typed_name:
            self._page.keyboard.type(typed_name)
            self._page.wait_for_timeout(800)

        if self._click_reference_option_by_name(asset_name):
            self._page.keyboard.type(" ")
            return True

        if self._click_reference_option_by_index(selectors, asset_name):
            self._page.keyboard.type(" ")
            return True

        return False

    def get_selected_reference_names(self, selectors: JimengSelectors) -> list[str]:
        collected_names: list[str] = []
        for selector in selectors.prompt_inputs:
            locator = self._page.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    editor_text = candidate.inner_text()
                except Exception:
                    continue
                names = re.findall(r"图片\s*(\d+)", editor_text)
                if names:
                    deduped_names: list[str] = []
                    for index_text in names:
                        normalized = f"图片{index_text}"
                        if normalized not in deduped_names:
                            deduped_names.append(normalized)
                    collected_names = deduped_names
                    break
            if collected_names:
                break
        return collected_names

    def submit_generation(self) -> bool:
        self._baseline_ready_marker_count = self._count_ready_markers()
        self._baseline_video_srcs = self._collect_video_srcs()
        self._latest_generated_video_src = ""
        locator = self._first_visible_locator(GENERATE_BUTTON_SELECTORS, timeout_ms=2500)
        if locator is None:
            return False

        try:
            if locator.is_disabled():
                return False
        except Exception:
            pass

        try:
            locator.click(timeout=5000)
        except Exception:
            try:
                locator.click(timeout=5000, force=True)
            except Exception:
                return False

        self._page.wait_for_timeout(3000)
        return True

    def wait_for_generation_result(self, timeout_seconds: int, poll_interval_seconds: int) -> tuple[bool, str]:
        deadline = None if timeout_seconds <= 0 else time.time() + max(timeout_seconds, 5)
        while deadline is None or time.time() < deadline:
            body_text = self._safe_body_text()

            if any(keyword in body_text for keyword in LOGIN_REQUIRED_TEXTS):
                return False, "login_required"

            new_video_src = self._find_new_video_src()
            if new_video_src:
                self._latest_generated_video_src = new_video_src
                return True, "new_video_src_detected"

            current_ready_marker_count = self._count_ready_markers()
            if current_ready_marker_count > self._baseline_ready_marker_count:
                time.sleep(2)
                continue

            if any(keyword in body_text for keyword in RESULT_PENDING_TEXTS):
                time.sleep(max(poll_interval_seconds, 1))
                continue

            if any(keyword in body_text for keyword in RESULT_FAILED_TEXTS):
                return False, "page_reported_failure"

            time.sleep(max(poll_interval_seconds, 1))

        return False, "timeout"

    def download_latest_result(self, output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self._try_download_tracked_video_src(output_path):
            return True

        return False

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure_started(self) -> None:
        if self._page is not None:
            return

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError("当前环境不可用 Playwright，请先安装并执行 playwright install。") from exc

        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.config.user_data_dir),
            headless=self.config.headless,
            accept_downloads=True,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def _first_visible_locator(self, selectors: tuple[str, ...], timeout_ms: int | None = None):
        self._ensure_started()
        effective_timeout = timeout_ms or self.config.timeout_ms
        for selector in selectors:
            try:
                locator = self._page.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=effective_timeout)
                    return candidate
                except Exception:
                    continue
        return None

    def _first_attached_locator(self, selectors: tuple[str, ...], timeout_ms: int | None = None):
        self._ensure_started()
        effective_timeout = timeout_ms or self.config.timeout_ms
        for selector in selectors:
            locator = self._page.locator(selector).first
            try:
                locator.wait_for(state="attached", timeout=effective_timeout)
                return locator
            except Exception:
                continue
        return None

    def _click_reference_option_by_name(self, asset_name: str) -> bool:
        selectors = build_reference_option_selectors(asset_name)
        for selector in selectors:
            option = self._page.locator(selector).first
            try:
                option.wait_for(state="visible", timeout=2000)
                option.click(timeout=3000)
                self._page.wait_for_timeout(800)
                return True
            except Exception:
                continue
        return False

    def _click_reference_option_by_index(self, selectors: JimengSelectors, asset_name: str) -> bool:
        match = re.fullmatch(r"图片(\d+)", asset_name)
        if match is None:
            return False

        option_index = max(int(match.group(1)) - 1, 0)
        for selector in selectors.mention_option_items:
            option = self._page.locator(selector).nth(option_index)
            try:
                option.click(timeout=3000)
                self._page.wait_for_timeout(800)
                return True
            except Exception:
                continue
        return False

    def _is_generate_page(self, selectors: JimengSelectors) -> bool:
        prompt_locator = self._first_visible_locator(self._configured_prompt_markers(selectors))
        if prompt_locator is None:
            return False

        file_input_locator = self._first_attached_locator(
            self._configured_reference_markers(selectors),
            timeout_ms=1500,
        )
        if file_input_locator is not None:
            return True

        body_text = self._safe_body_text()
        return "全能参考" in body_text or "Seedance 2.0" in body_text

    def _configured_prompt_markers(self, selectors: JimengSelectors) -> tuple[str, ...]:
        return selectors.prompt_inputs + selectors.page_ready_markers

    def _configured_reference_markers(self, selectors: JimengSelectors) -> tuple[str, ...]:
        return selectors.reference_file_inputs + selectors.reference_mode_markers

    def _count_ready_markers(self) -> int:
        self._ensure_started()
        total = 0
        for label in RESULT_READY_TEXTS:
            try:
                total += self._page.get_by_text(label, exact=True).count()
            except Exception:
                continue
        return total

    def _safe_body_text(self) -> str:
        try:
            return self._page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    def _collect_video_srcs(self) -> set[str]:
        self._ensure_started()
        collected: set[str] = set()
        videos = self._page.locator("video")
        try:
            count = videos.count()
        except Exception:
            return collected

        for index in range(count):
            video = videos.nth(index)
            try:
                src = video.get_attribute("src") or ""
            except Exception:
                continue
            if not src.startswith("http"):
                continue
            collected.add(src)
        return collected

    def _find_new_video_src(self) -> str:
        current_video_srcs = self._collect_video_srcs()
        new_video_srcs = [src for src in current_video_srcs if src not in self._baseline_video_srcs]
        if not new_video_srcs:
            return ""
        return new_video_srcs[-1]

    def _try_download_tracked_video_src(self, output_path: Path) -> bool:
        src = self._latest_generated_video_src.strip()
        if not src.startswith("http"):
            return False
        try:
            urllib.request.urlretrieve(src, output_path.resolve())
            return True
        except Exception:
            return False


class JimengWebOperator:
    """即梦网页最小 Web operator。"""

    def __init__(
        self,
        config: JimengOperatorConfig,
        *,
        selectors: JimengSelectors = DEFAULT_JIMENG_SELECTORS,
        session_factory: Callable[[JimengOperatorConfig], BrowserSessionProtocol] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.selectors = selectors
        self.session_factory = session_factory or PlaywrightBrowserSession
        self.logger = logger or logging.getLogger(__name__)
        self._session: BrowserSessionProtocol | None = None

    def open_jimeng(self) -> bool:
        self.logger.info("开始打开即梦页面。")
        session = self._ensure_session()
        session.goto(self.config.base_url)
        session.close_blocking_dialogs(self.selectors)
        self.logger.info("即梦首页已打开，并尝试关闭阻挡弹窗。")
        return True

    def ensure_reference_mode(self) -> bool:
        self.logger.info("尝试进入图生视频 / 全能参考模式。")
        success = self._ensure_session().enter_video_reference_mode(self.selectors)
        self.logger.info("全能参考模式状态: %s", "成功" if success else "失败")
        return success

    def fill_prompt(self, prompt_main: str) -> bool:
        self.logger.info("开始填写主提示词。")
        sanitized_prompt = _sanitize_prompt_for_jimeng(prompt_main)
        success = self._ensure_session().fill_prompt(self.selectors, sanitized_prompt)
        self.logger.info("主提示词填写状态: %s", "成功" if success else "失败")
        return success

    def fill_negative_prompt(self, prompt_negative: str) -> bool:
        self.logger.info("尝试填写负向提示词。")
        success = self._ensure_session().fill_negative_prompt(_sanitize_prompt_for_jimeng(prompt_negative))
        self.logger.info("负向提示词填写状态: %s", "成功" if success else "未填写")
        return success

    def upload_reference_assets(self, reference_file_paths: list[Path]) -> list[str]:
        self.logger.info("开始上传参考图，共 %s 个文件。", len(reference_file_paths))
        names: list[str] = []
        session = self._ensure_session()
        for index, path in enumerate(reference_file_paths, start=1):
            session.upload_reference_files(self.selectors, [path])
            names.append(f"图片{index}")
        self.logger.info("参考图上传完成，生成引用名: %s", names)
        return names

    def select_reference_assets(self, ref_assets_in_order: list[str]) -> bool:
        self.logger.info("开始按顺序选择参考图，共 %s 项。", len(ref_assets_in_order))
        session = self._ensure_session()
        for asset_name in ref_assets_in_order:
            if not session.select_reference_asset(self.selectors, asset_name):
                self.logger.info("参考图选择失败: %s", asset_name)
                return False
            self.logger.info("参考图已选择: %s", asset_name)
        return True

    def validate_reference_selection(self, expected_order: list[str]) -> tuple[bool, list[str]]:
        actual_names = self._ensure_session().get_selected_reference_names(self.selectors)
        success = actual_names[: len(expected_order)] == expected_order
        self.logger.info("参考图校验状态: %s", "成功" if success else "失败")
        return success, actual_names

    def submit_generation(self) -> bool:
        self.logger.info("尝试点击生成。")
        success = self._ensure_session().submit_generation()
        self.logger.info("点击生成状态: %s", "成功" if success else "失败")
        return success

    def poll_generation_result(self, timeout_seconds: int, poll_interval_seconds: int) -> tuple[bool, str]:
        self.logger.info("开始轮询生成结果，最长等待 %s 秒。", timeout_seconds)
        success, status = self._ensure_session().wait_for_generation_result(timeout_seconds, poll_interval_seconds)
        self.logger.info("轮询生成结果状态: %s (%s)", "成功" if success else "失败", status)
        return success, status

    def download_latest_video(self, output_path: Path) -> bool:
        self.logger.info("开始下载最新生成视频到: %s", output_path)
        success = self._ensure_session().download_latest_result(output_path)
        self.logger.info("下载视频状态: %s", "成功" if success else "失败")
        return success

    def run_dry_run(self, request: JimengDryRunRequest) -> JimengDryRunResult:
        result = JimengDryRunResult()
        messages = result.messages

        try:
            result.page_opened = self.open_jimeng()
            messages.append("页面打开步骤已执行。")

            result.reference_mode_ready = self.ensure_reference_mode()
            messages.append("全能参考模式检查已执行。")

            result.uploaded_reference_names = self.upload_reference_assets(request.reference_file_paths)
            messages.append("参考图上传步骤已执行。")

            result.prompt_filled = self.fill_prompt(request.prompt_main)
            messages.append("提示词填写步骤已执行。")

            result.references_selected = self.select_reference_assets(result.uploaded_reference_names)
            messages.append("参考图选择步骤已执行。")

            result.validation_passed, result.selected_reference_names = self.validate_reference_selection(
                result.uploaded_reference_names
            )
            messages.append("参考图选择校验步骤已执行。")
            return result
        finally:
            self.close()

    def run_one_shot(self, request: JimengOneShotRequest) -> JimengOneShotResult:
        result = JimengOneShotResult(
            shot_id=request.shot_id,
            prompt_main=request.prompt_main,
            ref_assets_in_order=list(request.ref_assets_in_order),
            transition_reference=request.transition_reference,
        )
        messages = result.messages
        should_close = not request.hold_for_audit

        try:
            result.page_opened = self.open_jimeng()
            messages.append("页面打开步骤已执行。")
            if not result.page_opened:
                return self._fail_result(result, "打开页面", "未能打开即梦页面。")

            result.reference_mode_ready = self.ensure_reference_mode()
            messages.append("全能参考模式检查已执行。")
            if not result.reference_mode_ready:
                return self._fail_result(result, "进入模式", "未能进入图生视频 / 全能参考模式。")

            result.uploaded_reference_names = self.upload_reference_assets(request.reference_file_paths)
            messages.append("参考图上传步骤已执行。")

            result.prompt_filled = self.fill_prompt(request.prompt_main)
            messages.append("主提示词填写步骤已执行。")
            if not result.prompt_filled:
                return self._fail_result(result, "填 prompt", "未能填写主提示词。")

            expected_reference_order = list(result.uploaded_reference_names)
            if expected_reference_order:
                result.references_selected = self.select_reference_assets(expected_reference_order)
                messages.append("参考图选择步骤已执行。")
                if not result.references_selected:
                    return self._fail_result(result, "选参考图", "未能按顺序完成参考图 @ 选择。")

                result.validation_passed, result.selected_reference_names = self.validate_reference_selection(
                    expected_reference_order
                )
                messages.append("参考图选择校验步骤已执行。")
                if not result.validation_passed:
                    return self._fail_result(result, "选参考图", "参考图已选结果与预期顺序不一致。")
            else:
                messages.append("当前没有可上传的本地参考图，跳过 @ 选择。")

            result.negative_prompt_filled = self.fill_negative_prompt(request.prompt_negative)
            messages.append("负向提示词尝试填写已执行。")

            result.submitted = self.submit_generation()
            messages.append("点击生成步骤已执行。")
            if not result.submitted:
                return self._fail_result(result, "点击生成", "未能点击生成按钮，可能是页面按钮未就绪。")

            result.generation_completed, poll_status = self.poll_generation_result(
                request.poll_timeout_seconds,
                request.poll_interval_seconds,
            )
            messages.append(f"轮询生成结果步骤已执行，状态: {poll_status}")
            if not result.generation_completed:
                if poll_status == "login_required":
                    return self._fail_result(result, "需要登录", "即梦当前浏览器会话需要先登录，提交后页面弹出了登录门槛。")
                return self._fail_result(result, "轮询结果", f"轮询生成结果失败: {poll_status}")

            result.ready_for_download = True
            if request.hold_for_audit:
                messages.append("生成已完成，等待下载前人工审计。")
                return result

            result.download_succeeded = self.download_latest_video(request.output_path)
            result.download_path = str(request.output_path.resolve()) if result.download_succeeded else ""
            messages.append("下载视频步骤已执行。")
            if not result.download_succeeded:
                return self._fail_result(result, "下载视频", "已检测到生成结果，但未找到可用下载入口。")

            return result
        finally:
            if should_close:
                self.close()

    def watch_and_download(
        self,
        *,
        output_path: Path,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> JimengWatchResult:
        result = JimengWatchResult()
        messages = result.messages

        try:
            result.page_opened = self.open_jimeng()
            messages.append("页面打开步骤已执行。")
            if not result.page_opened:
                return self._fail_watch_result(result, "打开页面", "未能打开即梦页面。")

            result.reference_mode_ready = self.ensure_reference_mode()
            messages.append("全能参考模式检查已执行。")
            if not result.reference_mode_ready:
                return self._fail_watch_result(result, "进入模式", "未能进入图生视频 / 全能参考模式。")

            result.generation_completed, result.poll_status = self.poll_generation_result(
                timeout_seconds,
                poll_interval_seconds,
            )
            messages.append(f"轮询生成结果步骤已执行，状态: {result.poll_status}")
            if not result.generation_completed:
                if result.poll_status == "login_required":
                    return self._fail_watch_result(result, "需要登录", "即梦当前浏览器会话需要先登录，监视过程中检测到了登录门槛。")
                return self._fail_watch_result(
                    result,
                    "轮询结果",
                    f"未在监视窗口内等到任务完成: {result.poll_status}",
                )

            result.download_succeeded = self.download_latest_video(output_path)
            result.download_path = str(output_path.resolve()) if result.download_succeeded else ""
            messages.append("下载视频步骤已执行。")
            if not result.download_succeeded:
                return self._fail_watch_result(result, "下载视频", "检测到任务完成，但视频下载失败。")

            return result
        finally:
            self.close()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _ensure_session(self) -> BrowserSessionProtocol:
        if self._session is None:
            self._session = self.session_factory(self.config)
        return self._session

    def _fail_result(self, result: JimengOneShotResult, stage: str, message: str) -> JimengOneShotResult:
        result.failed_stage = stage
        result.messages.append(message)
        self.logger.info("单镜头流程失败，阶段: %s，原因: %s", stage, message)
        return result

    def _fail_watch_result(self, result: JimengWatchResult, stage: str, message: str) -> JimengWatchResult:
        result.failed_stage = stage
        result.messages.append(message)
        self.logger.info("监视流程失败，阶段: %s，原因: %s", stage, message)
        return result


def build_default_jimeng_config(project_root: Path) -> JimengOperatorConfig:
    """构建默认的即梦浏览器配置。"""

    return JimengOperatorConfig(
        base_url="https://jimeng.jianying.com/ai-tool/home",
        user_data_dir=project_root / ".runtime" / "jimeng-browser",
        dry_run=True,
        headless=False,
    )


def _sanitize_prompt_for_jimeng(prompt_text: str) -> str:
    """移除尚未在即梦页面里真实注入的占位引用。"""

    return prompt_text.replace("@TransitionFrame", "最佳承接帧参考")
