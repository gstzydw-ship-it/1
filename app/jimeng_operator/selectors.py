"""即梦页面 selector 集中定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JimengSelectors:
    """即梦页面关键 selector 集合。"""

    popup_close_buttons: tuple[str, ...]
    video_entry_buttons: tuple[str, ...]
    page_ready_markers: tuple[str, ...]
    prompt_inputs: tuple[str, ...]
    reference_mode_markers: tuple[str, ...]
    reference_file_inputs: tuple[str, ...]
    mention_option_items: tuple[str, ...]


DEFAULT_JIMENG_SELECTORS = JimengSelectors(
    popup_close_buttons=(
        ".close-icon-wrapper-GXKG2I",
        ".icon-close-_TmiMV",
    ),
    video_entry_buttons=(
        "button:has-text('视频生成')",
        "text=视频生成",
        "text=Seedance 2.0",
    ),
    page_ready_markers=(
        "div[role='textbox'][contenteditable='true']",
        "input[type='file'].file-input-OfqonL",
    ),
    prompt_inputs=(
        "div[role='textbox'][contenteditable='true']",
        "[contenteditable='true'][role='textbox']",
        "textarea",
    ),
    reference_mode_markers=(
        "text=全能参考",
        "text=Seedance 2.0",
        "input[type='file'].file-input-OfqonL",
    ),
    reference_file_inputs=(
        "input[type='file'].file-input-OfqonL",
        "input[type='file']",
    ),
    mention_option_items=(
        "li[role='option']",
        "[data-testid='mention-option']",
    ),
)


def build_reference_option_selectors(reference_name: str) -> tuple[str, ...]:
    """根据参考图名称构建候选项 selector。"""

    escaped = reference_name.replace('"', '\\"')
    return (
        f"li[role='option']:has-text(\"{escaped}\")",
        f"[data-testid='mention-option']:has-text(\"{escaped}\")",
        f"text={reference_name}",
    )
